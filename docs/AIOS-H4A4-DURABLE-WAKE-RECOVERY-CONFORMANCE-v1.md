# H4A4 durable regular-Chat wake recovery conformance v1

This is a bounded **Human-owned live proof procedure**, not a report of completed
live proofs. TASK-278 implementation, synthetic regressions, Runtime verification,
review and publication do not themselves establish live H4A4 conformance, close a
roadmap milestone, or activate a downstream project. Human/Brain must assess the
observations separately after eligible reviewed publication.

## Navigation classification

Current generic self-host operational navigation: [AIOS Self-Host End-to-End Flow v1](AIOS-SELF-HOST-END-TO-END-FLOW-v1.md).
REPLACE_WITH_POINTER applies to that traversal only.
RETAIN_NORMATIVE: independent privacy, exact-target, generation, durable
deferral/ambiguity, no-resend, canonical supersession, isolation, compaction and
Human/Brain live-proof/assessment obligations. HISTORICAL_ONLY applies specifically
to the original terminal-only intake claim, version-2 repository-lane ownership,
attached-browser/no-acquisition/cadence assumptions and cross-event flight/completion gating
below where later published transport contracts supersede them. Those claims
do not prescribe current deployment or resurrect flight gating. Current policy
is resolved from the published local-wake/origin contracts and canonical lineage;
the other independent conformance requirements remain active, not historical.

## Authority and privacy

Retain the reviewed H4A3 exact-chat, authenticated regular-Chat surface, empty
composer, unique scoped Send and exact bounded outbound-user-turn safeguards.
Use an ACK-only Human planning agreement when a live wake is explicitly authorized.
The transport never reads assistant output or ACK content, navigates, launches a
browser, opens a tab, clears a draft, invokes ChatGPT Work, chooses an action or
Executor, dispatches execution, reviews, publishes or advances roadmap state.
In that original H4A4 claim only, no navigation/browser launch/tab opening is
HISTORICAL_ONLY under later H4D acquisition authority. Assistant-output isolation,
draft protection and all semantic/lifecycle authority prohibitions remain
RETAIN_NORMATIVE; H4D does not waive them.
HISTORICAL_ONLY intake claim at TASK-278: production intake remained exactly
`terminal:RESULT|FAILURE:<RUN>:<artifact SHA>`. Later H4A5 family coverage
supersedes only that terminal-only restriction.

Only the Human may prepare or reconcile machine-local configuration and state.
The Executor must not run these live procedures or alter operational files. Keep
chat/account/turn identities, CDP endpoints, credentials, profile data and local
paths out of Git, uploaded artifacts, Issues and proof notes. Retain sanitized
event identity, lane aliases, generation numbers, fixed reason/status codes and
counts. Inspect Human-owned files locally; do not attach their raw contents.

## Operational contract and readiness

`AIOS_LOCAL_CHAT_WAKE_CONFIG` names an external JSON registry. Version 2 contains
only `version` and `lanes`; `lanes` maps exact repository identity to an object with
`chat_url`, `cdp_endpoint`, `state_path`, and positive integer `generation`.
No private example values are supplied here. Exact conversation UUIDs must be
unique even across standalone/project-prefixed URLs. State ownership includes
the state file, `.lock`, `.pending` and `.queue` sibling surfaces; collisions or
nesting across lanes and registry storage fail closed. A lane's ownership path
must stay stable through a binding change; moving it or resetting it would discard
dedupe and is not an ordinary rebinding operation.

The legacy three-field single-project binding remains readable at generation 0.
To rebind, the Human upgrades to the registry and increments the lane generation.
Changing a chat or endpoint at the same generation, reusing an older generation,
or crossing repository ownership fails closed. The Human must coordinate edits
with the per-lane lock: do not edit a registry binding during an active critical
section. Old generation snapshots remain local for proof-only reconciliation.
Do not assign an old uncertain chat to a new lane while its original flight is held.

Enable only through the existing `AIOS_LOCAL_CHAT_WAKE_ENABLED == 'true'` Human
gate. Configure attached regular-Chat readiness outside engineering truth; no
workflow accepts browser or state configuration. Repository permissions remain
`contents: read`, with no actions-write or self-dispatch. The timer runs every five
minutes on published main and performs four bounded rechecks fifteen seconds apart
for this repository's exact lane, with a five-minute job ceiling. Other projects
need separately authorized deployment and an available runner; this workflow does
not enroll or activate them. Independent lane progress requires available worker
capacity; the transport adds no global busy state or workflow concurrency slot.

`--drain --repository <exact repository>` reads only previously admitted state and
inbox entries. It takes no event ID. `--rechecks` is 1–8 and `--interval` is 0–30
seconds. Each canonical observation budget is finite, with individual read-only Git
calls bounded to fifteen seconds and an overall observation budget of thirty
seconds per pass. Network identity uncertainty is a hold, never permission to send.

### Durable states and failure boundaries

| Operational state | Allowed behavior |
| --- | --- |
| Inbox / PENDING | Exact terminal intake is durably retained, including intake while a lane lock is busy; FIFO queue admission follows when its lock becomes available. |
| DEFERRED | Only a boundary known to precede a possible Send may retry. Draft, generation, target/CDP unavailability, generation drift and unknown canonical identity preserve attention. Every attempt repeats guards and freshness. |
| AMBIGUOUS | Written before any possible Send click. No automatic resend. An exact outbound-user-turn proof on the original binding generation or exact canonical resolution may close it. |
| SUBMITTED | Exact bounded user-turn proof, or safely migrated historical dedupe. Redelivery is NOOP. A current flight remains held until scoped generation has been observed busy then idle, or exact canonical resolution is observed. |
| RESOLVED_NOOP | Exact canonical successor/supersession observed; no insertion or Send is authorized. |

Existing version 1 SUBMITTED entries migrate to no-send SUBMITTED dedupe. Existing
ATTEMPTING entries migrate to AMBIGUOUS with **no invented generation**. They cannot
be automatically rebound or inspected on the current chat as historical proof;
exact canonical resolution or bounded Human reconciliation is required. Malformed
state, duplicate JSON keys, unknown schema, stale locks, incomplete inbox files and
leftover `.pending` writes fail closed. Do not delete them to authorize retry.

Canonical projection reads exact remote artifact/candidate identities and immutable
RUN/TASK binding in a disposable operational Git object store. It recognizes an
exact RESULT review-decision bound to the same RUN/candidate, or an exact FAILURE
repair authorization (and contiguous bound supersession chain). It does not inspect
verdict semantics, choose correction strategy, infer a successor RUN or task, read
planning bookmarks or emit `next_action`. Missing, moved, competing or unknown
identity remains held. Mere Human chat activity never resolves a subject.

Freshness and current binding are checked before attachment, immediately before
insertion and again before the possible Send boundary. A change after insertion
can leave staged text; the transport never clears it. The Human must reconcile any
such draft without treating its presence as submission proof. A fast generation
whose busy phase was not observed remains held rather than being presumed complete.

## Bounded live proof matrix

For each case, select genuine already admitted exact canonical terminal subjects
under explicit Human authorization. Record the prior operational state, the bounded
trigger, the subsequent fixed receipt and the local queue/flight observations. Do
not invent terminal refs, fake canonical artifacts, or create engineering subjects
merely to make a transport test pass. Use synthetic regression coverage where an
appropriate live subject is unavailable; mark that live case unproved.

### 1. Deferred recovery

1. On lane A, retain a Human draft or observe active generation before one admitted
   terminal arrives. Confirm DEFERRED, durable attention and no inserted wake or Send.
2. Let the Human clear their own draft or finish their own generation. Allow the
   gated timer to recheck; do not submit the event again as a new source.
3. Confirm repeated exact canonical/binding guards and at most one submitted wake,
   using H4A3's exact bounded outbound-user-turn proof. Confirm SUBMITTED persistence.
4. Redeliver the same source only if independently authorized; observe NOOP and no
   additional wake. Report timer/runner delay separately from submission facts.

### 2. Human canonical supersession

1. Retain an exact event as DEFERRED. Unrelated Human chat activity alone must leave
   it deferred; it must not be reported resolved from an ACK or assistant response.
2. Through independently authorized Human/Brain lifecycle work, establish an exact
   canonical successor for that very RESULT or FAILURE subject.
3. Allow the timer to recheck. Confirm RESOLVED_NOOP, no insertion and no Send.
4. If canonical identity is unavailable or conflicts, confirm CANONICAL_UNKNOWN and
   preserved attention. Never repair canonical truth through the wake transport.

### 3. Two-lane isolation

1. Human-configure lanes A/B with different exact chats, distinct state ownership and
   valid generations, preferably sharing one authenticated attached CDP session.
2. Hold A with a draft, generation or its own operational lock, then admit a genuine
   B terminal under B's separately authorized deployment.
3. Confirm B uses only its chat/state and can submit while A remains deferred or held.
   Compare A's operational bytes locally: B must not consume, mutate or redirect A.
4. Admit additional exact A events while A is locked; confirm exclusive local inbox
   retention and later FIFO consumption without semantic coalescing or silent loss.
5. For duplicate chat, state, lock or queue ownership, use the deterministic synthetic
   negative coverage; do not intentionally corrupt the Human production registry.

### 4. Binding-generation safety

1. With A pre-submit deferred, coordinate a Human binding change under its lane lock,
   preserve state ownership and increment generation. Confirm retry uses the new
   binding only after freshness/current-generation checks.
2. For an existing ambiguous attempt, increment the current generation without
   resetting state. Confirm its recorded attempted generation remains unchanged and
   any proof-only attachment addresses the original exact chat. No Send is permitted.
3. If that original chat is unavailable, confirm a Human-visible ambiguity hold.
   Current-chat activity or proof cannot redirect the original attempt.
4. Use deterministic generation-race/reuse/rollback coverage for mutation-edge cases;
   a live Human must not race registry edits against an active critical section.

### 5. Ambiguity and no-resend

1. Use a genuinely existing uncertain attempt, if available; do not create one by
   issuing an extra live Send. Confirm repeated drain performs no insertion or Send.
2. If H4A3's same exact user-turn proof is available on its original generation, permit
   proof-only reconciliation and confirm SUBMITTED. Observe the separate flight hold.
3. If proof is absent, ambiguous, duplicated, hidden or otherwise uncertain, confirm
   AMBIGUOUS persists with ATTEMPT_REQUIRES_HUMAN and no automatic resend.
4. An exact canonical successor may instead resolve the held subject as RESOLVED_NOOP.
   A historical generationless v1 ATTEMPTING entry must never acquire the current
   generation merely because the Human has supplied a new binding.

### 6. Safe compaction and queue completion

1. Locally observe unresolved deferred/ambiguous records and submitted dedupe. A drain
   with `--compact` must retain all unresolved records. Canonical uncertainty must
   prevent compaction of otherwise eligible records.
2. For safely submitted/resolved subjects with fresh exact canonical resolution,
   confirm their full records can be summarized into permanent exact-event digests.
   Compaction does not erase the digest, reassign a generation or infer new work.
3. Redeliver an independently authorized identical source and confirm
   COMPACTED_DUPLICATE NOOP with no browser attachment or resend, even if subsequent
   canonical observations are unavailable. No digest is evicted at capacity.
4. For a current submitted flight, observe scoped busy then idle controls, or exact
   canonical resolution, before permitting another queued wake. Freshly validate the
   next distinct subject; resolve stale subjects without coalescing other events.
5. Capacity overflow remains locally retained in the inbox or operational records,
   with bounded state-capacity/Human holds. Never delete unresolved work as cleanup.

## Assessment record

Human/Brain should retain one sanitized row per proof: exact public event identity,
lane alias, initial/final state, attempted/current generation numbers, fixed receipt,
outbound proof count, no-resend observation and any limitation. Clearly separate
submission, later proof reconciliation, generation completion, canonical supersession
and compaction. Missing live observations remain unproved; synthetic coverage does
not supply live conformance. Closure and any later H4A5/downstream activation require
their own Human/Brain authority. This document claims none of those live proofs passed.
