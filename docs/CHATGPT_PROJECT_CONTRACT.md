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
envelope, and TASK acceptance-phase ledger) as a mutation prerequisite. The two-stage
Brain audit itself remains a semantic authoring obligation: Brain must still audit the
TASK design before handoff, but cognitive-support/audit plumbing is not canonical
engineering truth and must not be required as a second mutation authority.

This retirement is prospective. Until a reviewed implementation removes the old ingress
gate, the currently published Runtime may continue to require the legacy audited handoff
for the one transition TASK needed to remove it. After activation, AUTHOR_TASK admission
must validate the final TASK contract and canonical/provenance bindings directly. The
post-BO-1 production path rejects any supplied `audited_handoff`, including a null carrier
field; it does not accept a legacy alternative path or silently ignore that material.
Runtime does not reconstruct, validate, fingerprint or freshness-recheck Decision Packets,
Stage-1/Stage-2 material, `acceptance_phase_ledger` or TASK_AUTHORING audit-support sections
to authorize AUTHOR_TASK mutation. Brain still performs `CONSTRUCT` then
`ADVERSARIAL_AUDIT_AND_RECONCILE` as mandatory semantic authoring discipline.

Direct admission preserves final TASK schema and identity/revision continuity,
`minimum-sufficient-v2` for new identities/revisions, authored return-affinity rules,
expected-main currentness, unrelated-delta rejection and expected-old-main
compare-and-swap publication. Identical historical replay remains non-mutating.
AUTHOR_REMEDIATION and AUTHOR_REPAIR retain their existing audited handoff, canonical
reconstruction, correction-lineage and freshness gates; BO-1 changes AUTHOR_TASK only.
This rule supersedes the historical H1/H3 AUTHOR_TASK mutation prerequisites in the
[semantic-handoff hardening baseline](AIOS-BRAIN-RUNTIME-SEMANTIC-HANDOFF-HARDENING-v1.md),
without changing their history or the two-stage Brain audit obligation.

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

## 11. Task Design Audit

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

Do not audit the entire repository indiscriminately.

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
