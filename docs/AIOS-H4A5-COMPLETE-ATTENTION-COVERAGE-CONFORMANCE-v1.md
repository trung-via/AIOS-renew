# H4A5 complete attention coverage conformance v1

This is a bounded Human-owned procedure for use after eligible reviewed publication.
It is not a report of live conformance, Human planning reconciliation, H4A5 closure,
H4B resume or downstream activation. Runtime owns canonical verification. The
Executor does not perform this procedure, modify local bindings or submit a wake.

## Contract and source reconstruction

`.ai/brain-attention-families.yaml` mirrors the version 1 registry in
`aios_renew.brain_attention`. Every selector is mandatory, scalar and bounded.
Unknown fields, families, versions, duplicate keys, nested values, noncanonical
identities and substituted family/schema combinations are rejected. The module
owns classification; source workflows supply typed boundary facts only.

The envelope contains exactly `format`, `version`, `family`, and `selectors`.
Generic identities are `attention:v1:<family>:<canonical selector JSON encoded as
unpadded base64url>`. Encoding is not secrecy. The content must remain selector-only.
RESULT and FAILURE retain their existing `terminal:<kind>:<RUN>:<artifact SHA>`
identities, dedupe records and canonical freshness checks; no alternate generic
terminal identity is accepted.

The registry's type names specify positive safe integers, SHA-1 commit/blob
selectors, SHA-256 source digests, bounded RUN/TASK/delivery IDs, fixed operation
enums, literal source boundaries and literal `run_created=false`. They never carry
commands, task text, assistant output, an action, verdict, correction strategy,
roadmap successor, Executor, model, effort or private browser configuration.

Source artifacts identify an exact workflow run, attempt, artifact ID and projected
source digest. They are immutable subordinate observations, not canonical lifecycle
artifacts. The reader checks the repository, trusted producer path, event framing,
attempt, artifact ownership, name and bounded single-file archive. It never reads
workflow logs. Existing Operational Receipt v2 may include execution-profile facts;
the projector excludes those facts from attention transport.

The local workflow retains completed-source `workflow_run` fan-in for compatible
producers, using published-main projection code with `contents: read` and
`actions: read`. Publication also has a direct edge in the reviewed publisher run:
canonical review/publication capture writes the bounded source and its digest;
the existing upload retains that exact `source.json` for 90 days; only successful
capture and upload permit `brain_attention publication-event` to project it.
The shared `project_source` classifier validates the capture digest and current
workflow run, attempt and uploaded artifact ID, and exposes at most one `event_id`.
No YAML expression classifies a family or constructs an attention identity.

The publisher passes only that `event_id` and fixed `trung-via/AIOS-renew` identity
to `aios-local-chat-wake.yml` through its existing `workflow_call` boundary, under
the existing Human enable gate with `contents: read`. The reusable delivery job
runs within the publisher run; it requires no separate downstream `workflow_run`
notification. The terminal workflow retains its direct admitted handoff. No
self-dispatch, actions-write permission, new secret, Issue/comment bridge or
polling mechanism is added. Missing, changed, malformed or unproven source evidence,
failed upload, and multiple eligible identities fail closed with no direct handoff.

The bridge preserves every eligible outcome produced by the publication projector,
including publication failure and canonical conflict; it is not gated on publisher
job success. Progress such as `AUTO_PUBLICATION_STARTED` or dispatch acceptance
projects no identity. Failure/conflict families retain the exact current run,
attempt, artifact ID and source digest. Pointer-free families, including publication
success, retain the same canonical identity regardless of collection path. If the
legacy fan-in also observes that source, its identity is identical, and existing
durable lane dedupe, tombstones and ambiguity no-resend rules apply to one subject.

The existing freshness reader still requires completed producer evidence for
pointer-bearing families. A direct failure/conflict intake may therefore be held
as `DEFERRED` / `CANONICAL_UNKNOWN` while its publisher run is active. Its admitted
identity remains in the same durable lane for the existing bounded recheck after
completion. This does not weaken source, chat-target, generation or Send guards.
Exact PASS lineage and canonical-main inclusion remain publication-success gates;
the handoff cannot publish a candidate, reconcile planning or change roadmap state.

## TASK-282 bounded recovery and observed replay limits

The Human-authorized publication replay of RUN-280-002 in publisher workflow
`37083846294` completed successfully and exported publication attention artifact
`11260375137`. The direct reusable handoff and completed-source `workflow_run`
fallback reconstructed an identical exact publication-success event identity.
Both local-wake receipts were `DEFERRED` with reason `CANONICAL_UNKNOWN` after
approximately the existing thirty-second observation window. Those receipts prove
durable pre-submit deferral, not an exact regular-Chat user turn or H4A5 closure.

Scheduled local-wake runs `37053912495` and `37075944497` showed that the bounded
recheck command can execute when invoked. No new scheduled run was observed after
publication replay `37083846294` despite waiting beyond one configured five-minute
interval. The observed schedule cadence is non-guaranteed: cron remains optional
best-effort recovery and supplies no correctness SLA or eventual-delivery guarantee.

Each exact freshness observation now starts its own thirty-second deadline,
including later FIFO subjects and later pre-send barriers. GitSources and
ArtifactSources share that observation's deadline; each Git operation remains
capped at fifteen seconds and each API operation at ten seconds. A successful
exact-SHA fetch may be reused only by that observation's disposable reader. A later
observation reconstructs a new store and re-fetches its own exact evidence. Mutable
refs are never cached: the existing before/after lineage, inclusion and planning
snapshots remain fresh reads. Unavailable, malformed, moved, conflicting or
time-exhausted evidence remains UNKNOWN and cannot authorize submission.

Both reusable `deliver` and `workflow_run` `deliver-projected` intake jobs now offer
an in-job follow-up using the existing local `--drain` contract. It runs after an
attempted intake even when delivery failed, since inbox admission can have survived
a lane-lock conflict or delivery failure. It performs at most two finite passes,
with one fifteen-second interval, under the unchanged five-minute job timeout.
It processes only already-admitted bounded local lane work and creates or classifies
no semantic attention subject. A submission, exact submission proof or ambiguity
hold ends rechecks in that invocation. Ineligible or interrupted work remains
durable; the follow-up offers another bounded opportunity, not unlimited retries.
The optional later scheduled drain uses the same lane lock, FIFO/one-flight,
exact-event dedupe, generation, browser/privacy and no-resend rules. AMBIGUOUS work
remains proof-only, including when direct, fallback and follow-up paths race.
No actions-write permission, workflow self-dispatch, repository_dispatch, PAT/App
secret, daemon, polling service, semantic retry authority or ChatGPT Work dependency
is introduced. PublicationReport semantics and publication truth are unchanged.

Already-handled old publication-success subjects may still be unresolved in the
durable FIFO lane. Human chat activity and manual continuation do not canonically
supersede those subjects. Before a post-publication live wake proof, separate
Human/Brain canonical planning reconciliation must record the exact already-handled
subjects through their required DONE/completed_by lineage, or otherwise supply the
existing exact canonical successor proof. That planning mutation is outside
TASK-282 transport implementation; transport cannot infer it, bypass old FIFO work,
close H4A5 or authorize H4B. Deterministic regression coverage of budgets, immutable
retrieval, concurrent follow-ups and duplicate identities does not claim H4A5 closure
or H4B readiness. A live wake proof remains a separately authorized Human procedure.

## Family/source matrix

| Family | Exact source and eligibility |
| --- | --- |
| INGRESS_OR_CARRIER_REJECTION | One rejected immutable Issue event: Issue ID/number, body digest, structural operation/subject and its exact source attempt. Malformed envelopes retain the generic UNKNOWN operation. |
| PRIMARY_REMEDIATION_REPAIR_DISPATCH_REJECTION | Explicit failed dispatch step or exact admission/preflight rejection receipt, fixed operation and delivery identity. An ordinary downstream workflow failure is insufficient. |
| PRE_AIOS_OPERATIONAL_FAILURE_REQUIRING_DIAGNOSIS | Operational Receipt v2 with a workflow-owned PRE_AIOS failure cause and no created RUN or invoked Executor. Requires the exact admitted dispatch/correction/repair delivery selector and bounded provenance. Malformed, missing or noncanonical delivery identity fails closed, creating no attention subject and no eligible projected attention until exact bounded provenance exists. No fallback identity is invented. |
| RUN_RESULT_REQUIRING_SEMANTIC_REVIEW | Existing exact RESULT artifact/ref and RUN/candidate identity. |
| RUN_FAILURE_REQUIRING_CORRECTION_REASONING | Existing exact FAILURE artifact/ref and failed RUN/candidate identity. |
| REVIEW_INGRESS_REJECTION | Rejected SUBMIT_REVIEW envelope with its exact RUN and prepared reviewed subject selectors. |
| REVIEW_CHANGES_REQUIRED_OR_BLOCKED | Canonical review-decision SHA, source RUN, RESULT artifact and reviewed candidate. The canonical document supplies the eligibility fact; no verdict enters the event. |
| REMEDIATION_OR_REPAIR_AUTHORING_REJECTION | Rejected AUTHOR_REMEDIATION/AUTHOR_REPAIR envelope, exact source/failed RUN, prepared subject and finding selector where applicable. |
| PUBLICATION_DISPATCH_OR_EXECUTION_FAILURE | Exact canonical review identity and immutable failed dispatch or publisher-attempt observation. Workflow conclusion is insufficient to establish canonical publication. |
| PUBLICATION_SUCCESS_REQUIRING_HUMAN_BRAIN_PLANNING | Exact PASS review lineage and published candidate inclusion in canonical main. This event requests fresh Human/Brain planning; it never advances a roadmap. |
| CANONICAL_CONFLICT_OR_STALENESS | Exact prepared subject SHA/digest and predecessor ref, prepared predecessor SHA and observed stale/conflicting SHA. No replacement or retry action is selected. |
| WAKE_DELIVERY_RECOVERY_FOR_UNRESOLVED_ATTENTION | An existing original PENDING, DEFERRED or AMBIGUOUS lane entry. Recovery identity is deterministic from that original identity and cannot reference recovery. Intake operates on the original record, not a second queue subject. |

Freshness repeats immediately before insertion and the possible Send boundary.
Exact review decisions resolve terminal RESULT attention; exact repair lineage
resolves terminal FAILURE attention. Review follow-up requires exact correction
successors for all findings, rather than treating one finding as completion of a
whole review. A canonical review/authoring successor can close its exact rejected
prepared subject. An unchanged closed Issue is not canonical resolution; an edited
body without canonical successor proof is UNKNOWN.

Publication failure resolves only through exact reviewed-candidate inclusion.
Publication success remains planning attention until canonical roadmap `sequence`
contains a DONE entry with `completed_by` matching the exact RUN, artifacts,
review ID/decision, reviewed candidate and published candidate. Status alone never
resolves it. Only Human/Brain-owned canonical changes can supply that record.

A rejected operational delivery may resolve through at most eight later attempts
of the same exact workflow, when an exact matching receipt attributes a RUN and
canonical terminal/candidate/RUN lineage proves that attribution. No RUN is inferred
from timing, workflow success, similar metadata or an unrelated invocation. Missing,
conflicting or out-of-bound evidence remains held.

## Readiness and privacy

Retain the H4A3/H4A4 authenticated regular-Chat, exact conversation, unique composer
and scoped Send guards, generation checks and exact outbound-user-turn proof.
No navigation, new tabs, browser launch, draft clearing, assistant-output extraction,
chat-history scraping or ACK interpretation is permitted. An ACK-only Human agreement
does not grant semantic lifecycle authority to the transport.

Only Human may prepare the external version 2 lane registry and its monotonically
increasing generations. Preserve existing lane state ownership and historical
snapshots. No binding or state file belongs in Git or a GitHub artifact. Do not add
another project to the deployed workflow as part of this procedure. A second lane
must already have separately authorized deployment and available runner capacity.

The Human enable gate remains `AIOS_LOCAL_CHAT_WAKE_ENABLED == 'true'`. Source
documents contain at most 16 observations, are bounded to 64 KiB on read, and are
retained for 90 days. Events are bounded to 8 KiB. API reads are bounded to ten
seconds each inside the existing thirty-second observation budget; Git reads retain
the fifteen-second individual ceiling. Artifact expiry or inaccessible evidence
causes a hold, not permission to evict, rename or blindly recreate the subject.

Keep sanitized family/event identity, source selectors, lane aliases, generation,
fixed reason/status codes and counts in proof notes. Never attach raw local state,
URLs, CDP endpoints, account/turn identifiers, paths, credentials or browser exceptions.

## Bounded Human proof procedure

Use at most one existing eligible source per family and lane for each case. Select
real already admitted immutable sources. Do not fabricate a RUN, FAILURE, REVIEW,
publication or dispatch merely to populate the matrix. If a representative source
is unavailable, record that live case as unproved and retain synthetic coverage.
Any replay must be separately authorized and must not launch another execution.

### Repaired publication-success live probe

The prior Issue #1390 replay was idempotent and created no new RUN. Publisher run
`37040170067` completed successfully and exported artifact `11242411027`, but no
post-publication local wake workflow was created. That is the recorded
`PUBLICATION_SUCCESS_SOURCE_NOTIFICATION_GAP` blocker, not live conformance.

TASK-280's direct Publisher-to-local-wake handoff has since received exact review
and reviewed publication. A subsequent live replay, publisher workflow
`37080008668`, used integrated REMEDIATION `RUN-280-002` with canonical PASS
`REVIEW-280-002` and reviewed candidate
`aa0648049194d8d51b2b04cd23b1bd0d46e71db5`, which was already published.
Publication rejected the historical integrated `authorized_main_sha`
`abc2aba3afd601dafbdda77647fd213a18dfe395` as stale before it could classify
the replay as a no-op. This is the stale integrated-replay ordering blocker;
it does not establish a defect in TASK-280's published direct handoff or prove
live H4A5 conformance.

TASK-281 repairs that ordering for integrated REMEDIATION and integrated REPAIR.
Exact REVIEW, RESULT, predecessor, integration identity/candidate/ref and correction
frontier validation remain required before either successful no-op. Equal canonical
main yields `ALREADY_PUBLISHED`; strict reviewed-candidate ancestry yields
`ALREADY_INCLUDED`. Both outcomes require zero push and no main rewrite. When main
does not contain the reviewed candidate, stale integrated authorization still
rejects publication before any push. PublicationReport and the existing publication
success projector and direct handoff retain their contracts.

Rerun the publication-success probe only after TASK-281 has received exact review
and its reviewed candidate has been published into canonical main, and only under
a separate Human authorization for that bounded live replay. Executor implementation,
Runtime deterministic checks, a source artifact, or a successful publisher workflow
alone does not satisfy this prerequisite or prove a regular-Chat user turn. Until
the Human live procedure succeeds, H4A5 remains blocked and H4B readiness remains
unclaimed.

After reviewed publication, Human may separately authorize one bounded replay of
an existing exact eligible PASS review, retaining the no-new-RUN/no-Executor
condition. Check that the publisher projects canonical publication truth, exports
the attempt-specific source artifact, and exposes exactly one selector-only identity
after upload. Record its run ID, attempt, artifact ID, source digest and event ID.
Observe the reusable `local-chat-wake` delivery job within that same publisher run
enter the existing lane even when no downstream wake workflow run is created.

In the exact already-bound authenticated regular Chat, observe one bounded wake
user turn, subject to the unchanged draft, generation, freshness and ambiguity
guards. If legacy `workflow_run` fan-in also fires, reconstruct the same artifact
and record an identical event ID and a duplicate NOOP, with at most one user turn
across both paths. Repeat observation/replay only under the bounded Human procedure;
an ambiguous Send must remain held with zero additional Sends. Retain sanitized
identity, lane alias, generation and fixed status/reason proof only.

Assess direct failure/conflict representatives through the same boundary when
existing exact sources are available, including any pre-completion deferral and
existing recheck. Do not manufacture publication failure/conflict or a new execution
for coverage. Neither this procedure update nor deterministic identity/dedupe tests
claims live regular-Chat conformance, planning completion, H4A5 closure or H4B readiness.

### Remaining bounded cases

1. **Representative emissions:** select a carrier rejection, one proven operational
   rejection/failure, RESULT, FAILURE, non-PASS review, correction-authoring rejection,
   publication failure, publication success and canonical conflict. Observe exactly
   one deterministic identity per selected source; replay the same source under
   authorization and observe dedupe. PASS review must produce no correction event.
2. **Progress exclusion:** observe DISPATCH_ACCEPTED, RUNNER_STARTED, AIOS_INVOKED,
   Executor/verification progress and auto-publication start. Record zero new wake
   subjects and zero user turns. Successful dispatch alone is not attention.
3. **Stale-source suppression:** defer a known exact subject using a Human draft.
   Let independently authorized Human/Brain activity supply its exact canonical
   successor or full planning bookmark. The timer must close it as RESOLVED_NOOP
   before insertion. Unrelated chat activity must leave it unresolved. Missing,
   moved, malformed or conflicting source identity must leave it held.
4. **Two lanes:** hold lane A with a draft or active generation. Admit one existing
   unresolved source to already authorized lane B. B may submit once while A's
   state and target remain untouched. Duplicate chat/state ownership must fail
   closed. Record available runner capacity rather than assuming parallelism.
5. **Ordering and generations:** queue two different exact subjects in one lane.
   Observe one flight and retained later work. Change a binding generation only
   through Human-owned operational procedure; a pre-submit race must abort, while
   an ambiguous attempt stays attached to its original generation.
6. **Recovery:** choose one already unresolved original entry. Request the bounded
   recovery family for that identity once. For pre-submit deferral, later eligible
   guards may deliver the original once. For AMBIGUOUS, only exact outbound proof
   or exact canonical resolution may close it; there must be zero additional Sends.
   A recovery-of-recovery, missing original or unproven source must not create a
   queue subject or discard the original.
7. **Compaction:** after exact canonical resolution, compact the original record.
   Observe its permanent digest and replay NOOP, including when source availability
   later disappears. Unresolved and ambiguous records must remain retained.

Assess these observations separately from implementation and Runtime verification.
Do not mark live conformance, roadmap closure, Human planning reconciliation or H4B
resume complete merely because source artifacts or synthetic tests exist.
