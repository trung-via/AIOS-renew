# Lean VP L3-01: single prospective proof scheduler core

This slice provides the explicit, nondefault Runtime API
`verification.plan_candidate_proofs(repository, expected=ProofSchedulerRequest(...))`.
It returns an immutable `ProofSchedulerPlan` version 1. It reads existing native
authority and produces a candidate-first declaration of proof work and eligible
source reuse. It performs no proof execution, materialization, collection,
checkpoint sealing, feedback delivery, continuation, lifecycle mutation or
durable writes. A plan is never an execution permit, acceptance PASS, Review,
TASK mutation or automation trigger.

KA-01 v1.2 remains **NOT_ACTIVATED**. D.1 **G3 and G6 remain outstanding**.
This core alone cannot establish integrated engineering readiness, policy
activation, production consent, publication or measured performance.

## Typed boundary and exact native authority

`ProofSchedulerRequest` binds version, TASK/revision/RUN/base, committed
candidate SHA/tree, identical subject SHA, canonical admission artifacts and
RUN bytes, expected current main, exact contract/mapping pins, operation and
optional original source locators. Each source is a `ProofReuseRequest` for one
exact reviewed obligation. Sources cannot carry an authenticated witness, a
serialized plan, an alternate provider or caller-selected semantic population.
Checkpoint and terminal are output consumers of this same request and owner;
there is no second scheduling API or consumer-specific execution selection.

The shared internal `_app_reviewed_population` extracts the unchanged native
population-level authentication from L2-02. L2-02 still applies its original
source-specific condition-document checks afterward. The reader authenticates
the existing Human-selected mapping review, GitHub actor and unedited original
Issue, carrier policy, canonical review RUN/RESULT/EVIDENCE, full independent
PRIMARY REVIEW PASS, published sources and complete review-scope correspondence.
The existing proof contract/mapping validators enforce exact complete coverage
and all condition/claim distinctions. Caller pins and structural digests select
nothing without this authority.

The core independently reads the admitted ACTIVE local RUN, canonical run
transport and exact current target refs; checks HEAD/tree, immutable TASK and
base ancestry; checks candidate changes against TASK modify scope; rejects a
correction wrapper or competing terminal/correction/successor namespace; and
rechecks admission, transport, main, selection, Human provenance and refs after
its reads. After source decisions it rereads the complete population through a
fresh reader and repeats L2-03 observations. A stale, swapped, superseded or
moving root produces BLOCKED. These bounded current observations do not create
an atomic distributed transaction or a lease for later consumption.
The final boundary also rereads each authenticated original source and its complete
correction/ref namespace, target conditions and retained admission/review roots
after those observations; source changes during later population reads cannot
retain the earlier eligibility decision.

## Complete deterministic coverage

The result retains the entire reviewed `CoverageDeclaration`, independent
review/source identities and target provenance. Every obligation row preserves
its stable identity, revision, digest, claim, acceptance, blocking flag, kind,
full conditions and provenance. Exactly one explicit reviewed mapping entry
assigns each obligation to exactly one proof unit. A unit includes its complete
reviewed proof, claims, conditions, identity and assigned obligation/acceptance
population. No executor assertion, common nodeid, command text, clean diff or
inferred test subset establishes equivalence.

| Unit state | Required basis |
| --- | --- |
| OUTSTANDING_PLANNED | Exact reviewed candidate proof lacks complete eligible source coverage; one original full proof is retained, with no execution permission. |
| REUSE_ELIGIBLE | Every mapped obligation has a fresh independently authenticated, genuinely satisfactory L2-03 observation of its exact original source and current target. |
| BLOCKED | Missing/unknown authority, incomplete/unstable/unsatisfactory source, unsupported operation or comparison gates not established. |

A first candidate requires no previous EVIDENCE. With no source locators all
reviewed candidate units remain outstanding; absence never invents prior
execution, PASS or reuse. An authenticated INVALIDATED source leaves its exact
candidate proof outstanding. An unknown, failed, errored, incomplete, disputed,
missing, stale or conflicting source blocks its unit. All members of a shared
unit must independently qualify before the entire unit becomes eligible; a
narrow source cannot replace a distinct full-condition proof.

The existing mapping validator requires explicit shared proof consolidation for
equivalent execution conditions and exact approved claims. Multiple approved
claims may share one proof only through that mapping. Distinct fixture, helper,
state, toolchain, environment, profile, worker, population, collection,
concurrency, ordering, integration, repetition, phase or evidence conditions
remain distinct. Partial eligibility never deletes another obligation. Units
are ordered candidate first, then comparison, deterministically by proof ID;
obligation projections are sorted by stable ID.

Source decisions retain original RUN/subject/EVIDENCE/command/exit identity,
provenance and EVIDENCE digests, raw digest/size and the digest of the fresh L2-03
witness. No original subject, evidence or raw capture is rewritten. A parsed
VALID or REUSE_ELIGIBLE witness is never accepted as an API input.

## Baseline and operation gates

Mandatory reviewed comparisons remain present, including after candidate FAIL
or absent source. This version has no independently authenticated native BR
attestation provider: every comparison is BLOCKED with
`BR_1_THROUGH_BR_6_NATIVE_ATTESTATION_NOT_ESTABLISHED`. Thus it cannot propose
base materialization, collection, test commands, reproduction or broad fallback.
A future replay provider must independently establish all BR-1..BR-6 reviewed
necessity, alternatives, exact population/comparability and finite cost bounds
before any such proposal. Structural replay labels or raw FAIL cannot do so.

Only independently authenticated PRIMARY has a positive path. REPAIR and
REMEDIATION always return NOT_ESTABLISHED/BLOCKED here, including exact-looking
correction pins. Future REPAIR support needs the exact admitted failed
predecessor, immutable FAILURE, unique current AUTHOR_REPAIR action/scope and
original/affected verification. REMEDIATION needs CHANGES_REQUIRED REVIEW,
selected still-outstanding finding(s), current authorization/action/scope,
affected verification and DELTA-review continuity. Neither can inherit PRIMARY
authority. Unknown operations have a bounded unsupported-operation reason.

## Shared consumers, bounds and future integration

`scheduler_plan_data` emits checkpoint and terminal projections from the same
unit population, plan identity and coverage identity. Both state the explicit
no-equivalent-duplicate invariant and require the same core reauthentication of
a fresh equivalent checkpoint source. Both assert checkpoint sealing, actual
execution and target acceptance as NOT_ASSERTED and execution as NOT_AUTHORIZED.
They express future reuse eligibility without creating a checkpoint or firing
execution. Parsed plan bytes are inspection data; the API accepts only a fresh
typed request and authenticates native roots again.

Wire formats are versioned closed schemas with one canonical ASCII JSON
encoding. Duplicate/extra/missing fields, alternate encodings, floats, malformed
identities, swapped sources and replayed projections are rejected. There are at
most 256 obligations/proofs and 32 source locators per request; requests/results
also inherit the strict 2 MiB, depth-24, 200,000-node and bounded-text encoding
limits. Projection overflow returns a bounded blocked result. Each native
reader retains L2's 8,192-object/32 MiB aggregate, 4,096-commit, 16-parent and
bounded Git output/time limits; scope walks retain 8,192-entry/depth-32 limits.
There are two population reads and two L2-03 observations per supplied source,
with finite nested L2 rechecks, no retries, fetches or open-ended polling.
The final retained-root check adds one L2-01 read per authenticated source and, when
present, one target-condition read.

`tests/test_lean_vp_l3_proof_scheduler.py` defines isolated real-Git,
canonical-shaped native authority conformance. It covers no-source first plans,
source reuse, exact mapping/completeness, shared versus distinct conditions,
invalidated/unknown/failed proof, baseline denial, stale/superseded/correction
attempts and identical checkpoint/terminal coverage without effects. Only HTTP
transport is replayed; genuine admission, selection, mapping, artifact and
Review validators supply the decisions. Runtime owns running canonical
verification and constructing EVIDENCE.

Future L3 slices must integrate actual execution with the existing Runtime
owner, immutable K_i, bounded factual F_i, live same-Executor lease, finite
scope/resource/progress/accounting limits, per-RUN consent and stop rules,
operation-specific native correction authority and integrated negative safety.
Checkpoint sealing, continuation and terminalization remain separate gates.
This core is absent from hot paths and policy dispatch. Existing L2 public
contracts and minimum-sufficient-v1/V2 behavior remain unchanged. There is no
Kernel, workflow, roadmap, downstream, publication or second-owner change.
