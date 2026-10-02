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

The local workflow receives completed-source notifications through `workflow_run`,
uses published-main projection code with `contents: read` and `actions: read`, and
passes eligible identities into the existing local lane. It neither dispatches
itself nor requests actions-write permission. The terminal workflow retains its
direct admitted handoff. Missing or unproven sources do not authorize submission.

## Family/source matrix

| Family | Exact source and eligibility |
| --- | --- |
| INGRESS_OR_CARRIER_REJECTION | One rejected immutable Issue event: Issue ID/number, body digest, structural operation/subject and its exact source attempt. Malformed envelopes retain the generic UNKNOWN operation. |
| PRIMARY_REMEDIATION_REPAIR_DISPATCH_REJECTION | Explicit failed dispatch step or exact admission/preflight rejection receipt, fixed operation and delivery identity. An ordinary downstream workflow failure is insufficient. |
| PRE_AIOS_OPERATIONAL_FAILURE_REQUIRING_DIAGNOSIS | Operational Receipt v2 with a workflow-owned PRE_AIOS failure cause and no created RUN or invoked Executor. Malformed delivery input uses the bounded UNATTRIBUTED operational marker, bound to the exact attempt. |
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
