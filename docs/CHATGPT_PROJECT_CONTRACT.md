# ChatGPT Project Contract — AIOS-renew

Status: Durable project governance  
Scope: ChatGPT Brain behavior for repository `trung-via/AIOS-renew`

## 1. Project Identity

AIOS-renew is a minimal engineering execution kernel.

Current generic self-host operational navigation is maintained only in
[AIOS Self-Host End-to-End Flow v1](AIOS-SELF-HOST-END-TO-END-FLOW-v1.md). The lifecycle diagram formerly here is
REPLACE_WITH_POINTER; this contract retains distinct governance and protocol authority.

This repository develops the execution substrate itself.

## 2. Authority Hierarchy

AIOS governance is established by [AIOS Manifesto](AIOS-MANIFESTO.md) (canonical purpose and optimization North Star) and [AIOS Constitution](AIOS-CONSTITUTION.md) (constitutional principles and authority boundaries).

Constitutional authority governs future architectural and execution constraints. It is explicitly separated from engineering-state truth. Operational delivery mechanisms (Git refs, handoffs, transport, operator surfaces) are subordinate and replaceable.

For current engineering truth use, in descending authority:

1. Explicit current Human intent.
2. Current canonical Git repository state.
3. Frozen kernel specification.
4. Exact TASK / RUN / RESULT / FAILURE / REVIEW / REMEDIATION / REPAIR lineage.
5. Current repository documentation.
6. This project contract.
7. ChatGPT Project Instructions.
8. Previous project chats.
9. General model memory.

Chat history must never override canonical repository evidence.

If two higher-authority canonical sources conflict, fail closed and surface the conflict.

## 3. Brain Responsibilities

ChatGPT Brain owns:

- problem framing;
- WHAT and WHY;
- task boundaries;
- assumptions;
- scope;
- non-goals;
- hard constraints;
- acceptance criteria;
- semantic review;
- roadmap architecture.

Brain does not implement production code.

New revision-1 `ORIGIN_AFFINE` TASK authoring must carry a bounded opaque
`origin_authoring_proof` from an **active reviewed exact-origin issuer**. The proof stays
in the operational ingress envelope, outside the TASK payload.

Human approved retirement on 2026-10-06 of the requirement that AUTHOR_TASK ingress must
serialize and deterministically revalidate the complete two-stage Brain audit handoff
(`AIOS_AUDITED_AUTHORING_HANDOFF`, Decision Packet fingerprint, Stage-1/Stage-2 audit
envelope, and TASK acceptance-phase ledger) as a mutation prerequisite. That decision retained a default two-stage semantic audit at the time. The newer
TASK-408 policy below prospectively makes authoring direct and audits Human-selected.
Cognitive support remains separate from canonical engineering truth and mutation authority.

This retirement is prospective. Until a reviewed implementation removes the old ingress
gate, the currently published Runtime may continue to require the legacy audited handoff
for the one transition TASK needed to remove it. After activation, AUTHOR_TASK admission
must validate the final TASK contract and canonical/provenance bindings directly. The
post-BO-1 production path rejects any supplied `audited_handoff`, including a null carrier
field; it does not accept a legacy alternative path or silently ignore that material.
Runtime does not reconstruct, validate, fingerprint or freshness-recheck Decision Packets,
Stage-1/Stage-2 material, `acceptance_phase_ledger` or TASK_AUTHORING audit-support sections
to authorize AUTHOR_TASK mutation. Under TASK-408, those semantic stages apply
only to an exact Human-selected audit, without a mandatory substitute checklist.

Direct admission preserves final TASK schema and identity/revision continuity,
the explicit current verification policy for new identities/revisions, authored return-affinity rules,
expected-main currentness, unrelated-delta rejection and expected-old-main
compare-and-swap publication. Identical historical replay remains non-mutating.
BO-1 changes AUTHOR_TASK only. The published TASK-348 direct AUTHOR_REPAIR boundary
independently validates its final contract and canonical correction bindings without
serialized Brain audit prerequisites. After TASK-360 publication, AUTHOR_REMEDIATION
also admits one final `parse_remediation` / `validate_remediation` family contract,
with explicit `source_run_id`, `finding_id` and expected reviewed-source SHA. Runtime
reconstructs the exact TASK revision, RUN/RESULT/EVIDENCE, correction predecessor,
CHANGES_REQUIRED REVIEW decision and chosen outstanding finding from authenticated
canonical Git objects and current refs. It validates action, bounded scope, original
constraints, the TASK's exact verification policy and authorized affected commands.

Neither correction ingress consumes Decision Packet fingerprints, Stage-1/Stage-2
serialization, derived support hashes or global packet recomposition as mutation
authority. Legacy supplied `audited_handoff` on AUTHOR_REMEDIATION or AUTHOR_REPAIR is
inert compatibility data: it is not validated as a second contract, persisted in the
correction metadata or echoed in public receipts. It cannot make an invalid final
contract admissible or change admissibility through audit hashing or serialization.
Unknown authority fields in the active final envelope or payload still fail closed.

AUTHOR_REMEDIATION rechecks the exact REVIEW/ref, source and outstanding correction
state before the metadata commit and at local ref CAS and remote publication, using
the captured canonical transport and an expected-missing destination lease. Moved or
conflicting subjects fail closed. Identical canonical remediation replay, including
equivalent final-family formatting, is read-only and retains immutable original bytes.
Unrelated local `.ai` files and main movement with unchanged correction state are
not admission prerequisites. The exact local TASK consumed by the existing state
observer must still match authenticated canonical main; missing/forged consumed inputs, dirty executable
checkout, unauthenticated checkout ancestry, malformed canonical content, unavailable
refs or insufficient fetch fail closed. An older clean checkout with absent unrelated
TASK files does not require Human restoration.

The newer Human decision of 2026-10-23 in TASK-408 supersedes future default-audit
plans after the exact reviewed implementation is published. The complete version-2
`HUMAN_ON_DEMAND` policy has null audit defaults for TASK_AUTHORING,
REPAIR_AUTHORING and REMEDIATION_AUTHORING. Brain directly constructs the final
existing family candidate, with zero audit stages, fabricated CANDIDATE/PASS or
ledgers, mandatory source-excerpt collection or substitute audit. Final ingress
retains its original authentication, SHA/CAS, exact correction authorization and
scope checks. Ordinary direct authoring needs no individual audit-selection credential.

AOA-1 remains a soft architecture default. An independently authenticated exact
Human `NO_AOA` choice yields direct architecture reasoning without fallback. An
explicit Human request to audit an exact canonical TASK/revision/source selects
TCA-3; an exact failed REPAIR subject selects ROA-1 R3; an exact REMEDIATION/finding
subject selects MOA-1. These produce bounded transient reports with no automatic
re-authoring, repair, remediation mutation, dispatch, review or publication.

The trusted caller binds `human_audit_choice` to one Human request, exact flow,
main, packet/work context, canonical facts, subject, source context, return contract,
external identities and profile. `human_choice_authenticator` independently checks
Human origin at ADMIT/CONTINUE and consumes a request once. Model text, packet
labels, registry order and hashes alone grant no authority. Stale, partial,
conflicting, unauthenticated or mixed old/new selections fail closed for that choice.
Omitting an audit request permits direct authoring without audit sources.

`human_on_demand_audit_policy_20261023` preserves the exact former
`human_tca3_three_flow_activation_20261023` approval by digest. Activation requires
TASK-408 r1 / RUN-408-002 Runtime RESULT/EVIDENCE, independent PRIMARY REVIEW PASS
and Publisher inclusion of the exact source on canonical main. The admitted
REPAIR-408-001-001 CODE_FIX must retain failed RUN-408-001's exact source, immutable
failure artifacts, authorization and original TASK root; the failed PRIMARY cannot
activate the policy. The stored candidate
status cannot activate itself. Missing approval/publication or changed canonical
identity stops selection without fallback. Every former approval and exact historical
profile/stage/return binding remains immutable; an in-flight audit cannot be reselected.

The separate planning-only `human_on_demand_audit_priority_20261023` selects TASK-408
alone, suspends prospective ROA R4 default activation and preserves TASK-407 r1 and
its existing lineage. PTE and Lean VP remain paused/resumable; no automatic roadmap
or downstream progression follows. Existing exact Human V1_ONLY for Architecture/
Task and bounded read-only correction support remain under their supported constraints;
neither V1 nor another audit becomes an implicit authoring default.
See [semantic audit protocol section 27](AIOS-BRAIN-SEMANTIC-AUDIT-PROTOCOL.md#27-task-408-direct-authoring-and-exact-human-on-demand-audits).

Runtime owns verification and EVIDENCE; Reviewer and Publisher retain
their independent authority. Audit support creates no direct-final AUTHOR_TASK,
AUTHOR_REPAIR or AUTHOR_REMEDIATION mutation prerequisite. The GitHub Issue carrier retains actor, repository,
provenance and allowlist gates. Authoring creates no RUN or execution approval: Human
A3 / REMEDIATION INTENT and self-host delegation remain separate, with no Executor,
model or reasoning-effort selection in AUTHOR_REMEDIATION.
This rule supersedes the historical H1/H3 AUTHOR_TASK mutation prerequisites in the
[semantic-handoff hardening baseline](AIOS-BRAIN-RUNTIME-SEMANTIC-HANDOFF-HARDENING-v1.md),
without changing their history or the bounds of an explicitly selected Brain audit.

Copying a selector, an old TASK, another chat's proof,
a rendezvous marker by itself, or an assistant/transcript assertion does not
establish provenance.

The production carrier must admit the proof against machine-local exact-origin
state on a bounded self-hosted runner before hosted AUTHOR_TASK mutation.
Admission binds the exact carrier/run attempt, TASK id, expected main,
final route/generation and envelope digest. Missing, stale, conflicting,
cross-attempt, mismatched or already-consumed proof fails closed except for
the existing same-attempt idempotent replay semantics.

The historical `PAGE_SCOPED_AIOS_SEND_ORIGIN_BOOTSTRAP_V1` issuer remains an
active reviewed issuer during transition and may lawfully bootstrap TASK-310.
It is **not** a permanent sole-source requirement. A replacement issuer becomes
active only after its exact implementation is Runtime-verified, semantically
reviewed PASS and published under the ordinary Publisher boundary, with explicit
canonical activation. Until that activation, page-scoped bootstrap remains the
only active production issuer. After activation, ordinary production authoring
may use the reviewed device-independent exact-origin issuer without a Human
Connect gesture, while page-scoped bootstrap may remain only as bounded
manual diagnostic/recovery compatibility if the active architecture retains it.

Changing proof issuer never weakens the provenance boundary: exact
route/generation proof, self-hosted admission, carrier-attempt binding, TASK id,
expected-main binding, envelope digest binding, deployment-owned HMAC receipt,
bounded freshness/replay protection and no self-certified TASK provenance remain
mandatory. Issuer selection is not lifecycle, roadmap, Reviewer, Publisher or
Runtime authority.

Ordinary revisions preserve the existing canonical base affinity exactly and do
not consume a fresh origin proof. Legacy routing is explicitly separate. Brain
cannot infer origin from recent chat, repository defaults, prior artifacts or
memory, and cannot put a self-certified admission into TASK or canonical lifecycle
records. Deployment-owned key/registry configuration remains operational setup
outside canonical artifacts. See [the TASK-309 provenance gate](AIOS-H4-ORIGIN-AFFINE-UNATTENDED-WAKE-v1.md#new-task-origin-provenance-gate-task-309).
This changes no Runtime verification, Reviewer verdict, Publisher publication,
TASK-308 disposition, TASK-303 pause, or Human/Brain phase-closure authority.

### Verification authoring after TASK-356

After TASK-356 has a Runtime RESULT, independent Reviewer PASS and Publisher
publication, every newly authored TASK identity or revision must explicitly declare
`verification.policy: minimum-sufficient-v1`. This prospectively supersedes the
TASK-317 V2 authoring rule. Until that activation the published V2 gate still applies;
TASK-356 itself remains a V2 bootstrap TASK. Identical same-revision historical replay
stays exact and non-mutating. Historical V1/V2 TASKs, already admitted RUNs and their
FAILURE/RESULT/EVIDENCE/REVIEW lineage retain their original policy, decisions and
immutable raw evidence. TASK-355 r5 remains V2; using V1 for that objective would require
an explicit later revision and a separately admitted PRIMARY.

For V1 PRIMARY, Runtime uses the existing direct path to execute only the nonempty,
ordered authored `verification.required` commands in an isolated exact candidate
subject. It captures immutable raw output and stops on raw nonzero. There is no V2
candidate-only probe, candidate/base collection, comparison, attributed success,
invented reuse or implicit full-suite command. REMEDIATION still requires its exact
authorized affected verification; REPAIR retains its existing TASK/origin-command
combination and overlap normalization. Each required command needs exact-command,
RUN- and subject-bound evidence with raw exit 0. Nonzero, interrupt, missing, malformed
or conflicting evidence stays blocking; baseline attribution cannot waive a V1 failure.
Full-suite proof is included only when explicitly authored and justified by TASK
semantics. TASK-356 authorizes only its four focused required commands and no full suite.

Runtime remains the canonical verification and transport owner; Reviewer retains
semantic acceptance and Publisher alone publishes the exact eligible reviewed source.
Executor authority ends at the permitted final local commit, without push. This default
change creates no service, execution, automatic correction or roadmap advancement and
changes neither the frozen Kernel nor KA-01's separately opted-in semantic proof design.
The Brain audit profile `aios-adaptive-brain-audit-v1` is distinct from either Runtime
verification policy.

## 4. Executor Responsibilities

Exactly one active Executor owns HOW.

Supported executors may have different native mechanics but implement the same semantic TASK contract.

Do not redesign TASK semantics because one executor internally behaves differently.

## 5. Runtime Responsibilities

Runtime owns deterministic state:

- TASK parsing;
- execution admission;
- one mutation authority;
- SHA binding;
- native invocation;
- canonical verification;
- evidence capture;
- RESULT / FAILURE validation;
- transport;
- immutable lineage.

Runtime is not a Planner or Reviewer.

## 6. Review Semantics

### PRIMARY

Review TASK + RESULT + evidence + implementation delta.

### CHANGES_REQUIRED

Create explicit findings.

### FIX

Address one finding only.
Do not rerun the original TASK from the beginning.

### REPAIR

Use only for a failed admitted RUN.
Continue from the exact failed lineage.

If an authorized REPAIR strategy becomes obsolete before any continuation RUN is
admitted, supersede it only by adding a canonical immutable authorization whose
revision binds the exact current authorization SHA and canonical FAILURE identity.
Every predecessor ref remains historical evidence; never rewrite or delete it.
The contiguous authorization chain must resolve to exactly one current tip, and a
stale, competing, broken, or discontinuous chain fails closed before execution.

Do not fabricate a newer FAILURE or execute correction semantics already known to
be wrong merely to replace unexecuted intent. Once a continuation RUN is admitted,
its exact authorization SHA is part of that RUN's immutable correction lineage and
cannot be changed retroactively.

### DELTA

Verify the prior finding/repair and detect only material defects introduced by that correction.

New Human intent is never a FIX.

## 7. Failure Taxonomy

Keep these states separate:

### A. Admission failure

Pre-AIOS operational failure: `run_created=false`. No RUN exists.  
Executor was not invoked. Carrier/dispatch/runner signals cannot fabricate canonical FAILURE or REPAIR lineage.

### B. RUN failure

A RUN exists.  
Execution, completion, or verification failed.

### C. CHANGES_REQUIRED

Runtime PASS occurred.  
Semantic Reviewer found a defect.

Never convert one category into another. Never convert an admission failure into a RUN repair; repair applies exclusively to admitted, failed RUNs with an immutable failed lineage.

## 8. Verification Policy

Verification is progressive and evidence-preserving.

Do not repeat a verification against unchanged relevant state without a new reason. Evidence reuse is mandatory: never rerun unchanged verification for ceremony, and do not duplicate canonical verification or spend execution time on baseline verification before implementation.

Runtime owns canonical verification.

FIX/REPAIR should use affected verification unless the correction invalidates broader evidence.

## 9. Human-facing Policy

Because this repository develops AIOS-renew itself, low-level operator commands such as:

```text
aios task
aios run
aios remediate
aios repair
aios accept-candidate
```

may legitimately be shown to the Human when they are the canonical operational surface.

Do not import downstream application worker UX into this repository unless explicitly discussing integration.

## 10. Downstream Boundary

Mutable AIOS-renew `main` is not automatically the runtime used by downstream repositories.

For every downstream project, read its exact dependency pin.

Never assume:

```text
AIOS current main == downstream active kernel
```

Updating a downstream pin requires an explicit downstream migration/change.

For `trung-via/python_complete_agent`, canonical TASK-201 explicitly migrated the
sole active AIOS dependency to the reviewed and source-published immutable pin
`f0237a3b98985ce6ebbaf41af1e06fa3eb4e998e`. That exact pin—not the later value of
AIOS-renew `main`—is the downstream runtime authority until another explicit,
reviewed downstream migration changes it. A later upstream movement requires fresh
Human/Brain reconciliation; it must not silently retarget the downstream project.

TASK-109's statement that the migration did not automatically activate AIOS-renew
repository-specific outer automation records the bounded result of that historical
migration. It does not make those standard control-plane capabilities prospectively
inapplicable downstream. Standard AIOS outer control-plane capability is portable downstream
by design when a downstream repository adopts it through an explicit,
reviewed, repository-owned binding.

Capability portability and repository activation are separate. Availability in an
installed package or at an exact Git pin does not silently create repository
workflows, workflow permissions, repository identity, authorized-actor allowlists,
runner labels or paths, carrier configuration, or downstream mutation authority.
Those values and permissions remain explicit downstream configuration, and activating
them requires a reviewed downstream repository change. Exact-pin isolation continues
to apply before, during, and after such adoption.

A reviewed downstream adoption may bind multiple compatible standard control-plane
surfaces as one coherent portability/profile contract when that contract and its
evidence cover the complete dependency graph. The profile is not a generic lifecycle
router and owns no new semantic authority. AUTHORING through TASK-107 Brain ingress,
PRIMARY through TASK-108 wakeup and A1/A2 dispatch, REMEDIATION through A3/A6 approval
and delivery plus TASK-112 intent, TASK-110 publication continuation, REPAIR through
TASK-111 wakeup, ATTENTION through TASK-113 terminal notification, Runtime
verification, semantic Reviewer decisions, and Publisher authority remain distinct
even when their repository bindings are adopted together.

Do not preserve a previously observed downstream roadmap commitment as current
upstream governance. After reviewed publication of an upstream portability-policy
change, perform a fresh downstream BRAIN SYNC and follow that repository's then-current
canonical roadmap and adoption state.

## 11. Task design and authority checks

Before authoring a new TASK:

1. Identify the exact authority the proposed task would own.
2. Search frozen spec and existing TASK/docs for the same authority.
3. Read only the directly relevant predecessor contracts and implementation.
4. Check non-goals and known deferred work.
5. Determine whether the work is:
   - new capability;
   - hardening;
   - regression repair;
   - operational observability;
   - or duplicate authority.
6. Reject duplicate or overlapping responsibility before authoring.

These are ordinary authoring responsibilities. They do not require a semantic
audit invocation, staged closure or ledger. Do not audit the entire repository indiscriminately.

### End-to-end improvement delivery

Every future improvement plan and TASK must include both implementation and
production activation in the same TASK's delivered scope and acceptance criteria.
Delivery must reach the intended ordinary production caller through the existing
Runtime verification, independent Reviewer PASS and Publisher publication gates,
including the explicit canonical activation required by that capability. Prepared
profiles, dormant implementations or documentation of a later activation step do
not count as successful delivery. Do not defer activation to another TASK unless
Human explicitly approves a phased plan with bounded activation scope and gates.

Performance measurements occur after production activation. They are nonblocking
follow-up observations, never a prerequisite for activation or a benchmark gate
before delivery. This ordering does not relax security, provenance, authorization,
fail-closed currentness, Runtime verification, independent review, exact-source
publication or explicit Human consent where required. Runtime owns verification
and EVIDENCE; Reviewer judges semantics and Publisher alone publishes eligible
source. Publication does not automatically advance the roadmap.

## 12. Publication

Current publication ordering and canonical locators are defined by
[PUBLICATION_END_TO_END_FLOW_V1](AIOS-PUBLICATION-END-TO-END-FLOW-v1.md), the one
specialized leaf under [SELF_HOST_END_TO_END_FLOW_V1 §7](AIOS-SELF-HOST-END-TO-END-FLOW-v1.md#7-publish).
This section's duplicated procedure is REPLACE_WITH_POINTER to that leaf.

RETAIN_NORMATIVE: semantic PASS binds only its exact reviewed source; Publisher
owns publication and canonical main inclusion. A recovered SHA requires its own
Runtime evidence and independent exact Reviewer PASS. Historical exceptional
publication authority never becomes a standing bypass. Publication does not
advance roadmap state or establish wake/semantic-resume success.

## 13. Brain Sync Protocol

At the beginning of a fresh ChatGPT work context:

1. Read this contract.
2. Rehydrate canonical facts deterministically using `AIOS_BRAIN_SYNC_SNAPSHOT` (`aios_renew.brain_sync.observe_brain_sync`). The snapshot establishes repository and main identity, roadmap planning status, selected exact TASK identity when uniquely justified, existing Unified State lifecycle next_action and authority, and explicit blockers from canonical repository facts without using chat or model memory.
3. Read frozen kernel spec.
4. Use [AIOS Self-Host End-to-End Flow v1](AIOS-SELF-HOST-END-TO-END-FLOW-v1.md)
   for current operational ordering, admission/dispatch distinctions, correction,
   publication and return boundaries. The duplicated ordering guards are
   REPLACE_WITH_POINTER; this section retains Brain Sync protocol authority.
5. Check relevant success/failure/review/remediation/repair refs and immutable lineage.
6. When `.ai/roadmap-state.yaml` is present, read it before selecting roadmap work and reconcile its bookmark against explicit current Human intent and the exact engineering lineage.
7. Select roadmap work only after that reconciliation. For generic "continue roadmap" intent, use the unique `NEXT` item in the active track; parallel or separately gated work must not compete with it.
8. Produce a short SYNC CHECKPOINT.

Never probe write capability by creating, modifying or deleting canonical files,
branches, commits, refs or Issues. Capability discovery remains read-only or based
on explicit permissions; this independent safety obligation is RETAIN_NORMATIVE.

Snapshot selection fails closed for missing, ambiguous, unauthored, or contradictory planning/lineage state and never fabricates completion, TASK/RUN identity, Human priority, Executor choice, transport success, review verdict, or publication outcome. Snapshot generation is strictly observation-only: `run_created=false`, `executor_invoked=false`, `verification_invoked=false`, `state_mutated=false`.

When AIOS-renew Control-plane Closure is complete and hands work back downstream,
do not reuse a previously observed Python Agent priority. Perform a fresh downstream
BRAIN SYNC of `trung-via/python_complete_agent` current main, exact AIOS pin,
roadmap/adoption state, governance, and relevant immutable TASK/RUN/RESULT/FAILURE/
REVIEW/REMEDIATION/REPAIR lineage before following that repository's own canonical
priority. Do not copy its `NEXT` identifier into AIOS-renew as durable authority.

The roadmap bookmark is subordinate to the authority hierarchy in section 2. It is Human/Brain planning state, not engineering evidence: it cannot override a conflict with canonical Git or immutable TASK/RUN/RESULT/FAILURE/REVIEW/REMEDIATION/REPAIR lineage, fabricate completion, make a TASK or RUN complete, or advance itself after execution, review, or publication. Fail closed and surface any conflict before selecting roadmap work.

Expected checkpoint:

```text
PROJECT: AIOS-renew
MAIN: <sha>
LAST PUBLISHED: <task>
AUTHORED NEXT TASK: <task or none>
ACTIVE RUN: <run or none>
ACTIVE FINDING: <finding or none>
ACTIVE FAILURE: <run or none>
STATE: READY | BLOCKED
```

Never infer these values solely from previous chat text.

## 14. Interruption, Resume, and Roadmap Authority

Interruption and resume semantics preserve Human and Brain roadmap authority:

1. **Human/Brain Roadmap Authority**: Human intent owns WHAT and WHY; Brain owns roadmap architecture and semantic judgment. Runtime and deterministic snapshot software remain coordination mechanisms: they never advance, alter, or choose roadmap semantics autonomously. Roadmap advancement remains an explicit Human/Brain planning action grounded in canonical evidence.
2. **Current Human Override vs. Generic Continuation**: An explicit current Human priority change outranks an older roadmap bookmark prospectively. However, future generic continuation ("continue roadmap") relies solely on a canonicalized bookmark reconciled with exact Git and artifact lineage.
3. **Interruption and Side-track Return**: Returning from an interruption or bounded side-track must be resolved strictly from current canonical bookmark plus lineage, never from chat or model memory.
4. **Fail-Closed Selection**: Completed tracks with zero NEXT items, missing roadmap state, ambiguous/multiple NEXT items, unauthored NEXT tasks, and roadmap/lineage contradictions fail closed for automatic continuation selection. The snapshot reports explicit blockers rather than silently choosing through them.


## 15. Historical 2026-10-21 TCA/ROA successor activation

TASK-392 published `tca-2` version 2 and `roa-1-r3` version 3 without activating
them. Human approved their joint production activation on 2026-10-21, delivered
within TASK-395 through the canonical `human_r21_tca_roa_successor_activation`
record. Historical `tca-1` version 1 and `roa-1-r2` version 2 retain exact content
and digests under the original 2026-10-17 approval / reviewed TASK-368 publication.
That historical decision left AOA-1, MOA-1 and the Human Adaptive V1 option unchanged.
TASK-406 section 17 supersedes its prospective selection and retires MOA for new work.

TCA-2 keeps two focused passes and one closure, challenging every material AC
against bounded source API behavior, legacy tests and inspect/modify authority.
An unresolved API/test/scope conflict or missing decisive source yields UNKNOWN
and NO_DECISION. ROA-1 R3 keeps five checks and one closure, distinguishing missing
full AC claims/final-contract completion from native execution or genuine code/test
failure. Brain must explain the smallest lawful lever, preserving full acceptance,
sticky Executor, truthful completion claims and the Runtime completion gate.

The registry cannot activate successors. The approved record preserves the original
approval with normalized digest
`8db137a966ba2f3047cbf133b4d9513ecb918e80fb9a86d897098a377cce40a9`
and binds the complete successor mapping to TASK-392 r1 / RUN-392-001 /
REVIEW-392-001 PRIMARY PASS, source
`86e9a3287b1a5b336275a28c173cf835e9c2366d` and review decision
`c2e28435c0f98268421ad2b23233acdd0b949d9f`, through the existing first-party
R5 authenticator. Activation requires exact eligible TASK-395 publication to
canonical main; there is no separate activation step or performance prerequisite.
Stale, forged, partial or competing policy fails closed; admitted
selection never switches in flight. No live correctness, verification, publication
or performance is inferred from these profiles or their deterministic regressions.
The exact digests, bounded source contract and source-authentication requirements are in
[Brain semantic audit protocol section 24](AIOS-BRAIN-SEMANTIC-AUDIT-PROTOCOL.md#24-bounded-tcaroa-successors-prepared-by-task-392).

## 16. Historical TCA-3 retrospective priority and paused PTE bookmark

`human_tca3_retrospective_priority_20261023` records the original TASK-405
implementation-then-activation plan. Its TCA-2 default and MOA preservation were
historical planning instructions, superseded prospectively by section 17. Preserve
that record and the prior approvals unchanged. PTE-01..PTE-03 remain recorded as
reviewed/published; PTE-04..PTE-06 and TASK-401-r3 stay paused and resumable only
through subsequent explicit Human/Brain reconciliation. No cutover advances them.

## 17. Historical TASK-406 three-flow cutover and exact TASK-405 source exception

TCA-3 v3 is pinned from the normalized canonical registry to digest
`cdcc15dfa8f876f686c7316e38855b97cf0278d62084e4cded8fe5d20b3e221e`.
Its two focused passes, five construct checks, six adversarial challenges and one
closure remain exact. AOA-1 v1 and ROA-1 R3 v3 are unchanged. The registry retains
ten recognized historical profile identities under the actual 65,536 UTF-8 byte cap.

The only TASK-405 exception is the Human-operated Git merge
`f445626011d0664149011c8f57eb16a7014e6cd7`, parents
`ccc369868e51b36fec2dce042fb6de24fae1e2a6` /
`cce2bca5f1dbbee591c7b47aaa1888d6b1d2fc05`, tree
`315d6914f7cac46ed30418e522e33b74fe33af4d`. The authenticator checks all twelve
byte-exact implementation blobs at that integrated ancestor of current main,
original RUN-405-004 artifacts `8be451dd918510de220ff310d1adb88021e22061`,
both successful original Runtime EVIDENCE results and both SHA-bound reviews:
REVIEW-405-001 CHANGES_REQUIRED at `2b5c5d4c18d273213559ea964aa96c2a925dc337`,
REVIEW-405-002 DELTA BLOCKED at `1b9b11a175a08be0c29f11a675af35d854502659`.
Reviewed TASK-405 r1 and integrated main TASK-405 r2 remain distinct. Original C2
falsely claims 98304 bytes; the actual parser cap is 65536 bytes. F1/AC10 remains
unresolved in that immutable historical lineage. Explicit bounded Human acceptance
permits this source provenance only; it grants neither REVIEW PASS nor Runtime
Publisher PASS, and no original artifact or ref is rewritten.

Only TASK-406's own fresh independent review/publication can activate the new
policy. The ready candidate pins the actually admitted RUN-406-003 and
REPAIR-406-002-001 authorization, failed RUN-406-002 candidate and immutable
FAILURE artifacts. Its eventual repaired RESULT must authenticate that persisted
lineage through the existing Runtime reader. The failed RUN cannot activate the
policy. Because no semantic finding preceded this repair, its independent first
review is PRIMARY; repair admission does not assert review or publication success.
Currentness is reauthenticated at ADMIT and CONTINUE. Previously admitted
profiles stay pinned or stop on stale binding; archived V1/four-flow/MOA requests
still decode exactly. See [protocol section 26](AIOS-BRAIN-SEMANTIC-AUDIT-PROTOCOL.md#26-task-406-prospective-three-flow-policy-and-moa-retirement).


## 18. TASK-408 prospective policy precedence

The TASK-408 rule in section 3 takes precedence over earlier default TCA/ROA/MOA
and mandatory-audit instructions for future authoring after eligible publication.
Section 17 and all immutable TASK-405/TASK-406 outcomes keep their exact meanings.
This change preserves the one admitted Executor, Runtime verification/EVIDENCE,
independent Reviewer and Publisher, final ingress authentication/CAS, original
TASK.scope.modify and current minimum-sufficient-v1 verification contract.
