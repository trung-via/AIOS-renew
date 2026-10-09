# AIOS-Renew Verification Architecture Replacement v1

Status: HUMAN_APPROVED_PLANNING (refined v1.1 and v1.2 on 2026-10-09) — NOT AN ADMITTED TASK, REVIEW OR PUBLICATION
Canonical repository: `trung-via/AIOS-renew`
Human decision date: 2026-10-08
Planning origin: `08a5021236938b99d994d41e3d4f3907ec237230`; future work MUST resync fresh `main`.

## Purpose

Retire Brain-authored executable verification commands and V2 command-driven test scheduling, replacing them with a single Runtime-owned deterministic proof execution boundary. Optimize full PRIMARY, REPAIR, REMEDIATION and DIRECT_CANDIDATE verification wall-clock time using valid evidence preservation and cost-profiled infrastructure, without weakening proof completeness. Optimize Verified Useful Work / (Time + Tokens + Human Effort).

## Priority and authority

Human explicitly preempts the former BO-2/3 unique NEXT to undertake VP-01 through VP-07 immediately. The BO-2/3 work remains paused, not completed; return is an explicit Human/Brain planning transition after VP-07 closure, never automatic. This plan does not select Executor/model/effort for a new TASK, grant implementation authority, create a RUN, claim verification, judge review, publish a source candidate or alter frozen Kernel v0.1.

Brain owns WHAT/WHY and semantic proof obligations; one admitted Executor owns HOW; Runtime owns deterministic verification, identity, bindings and evidence; Reviewer owns semantic verdict; Publisher owns exact eligible reviewed source publication. No authority may be merged by co-location.

## Retirement and replacement decisions

- Retire mandatory Brain-authored executable command lists in prospective TASK and correction authoring; do not move the same lists to a new field or manifest.
- Retire V2 command-driven orchestration (`authored → delta probe → broad authored command → failure reproduction/base replay`) as new-policy production scheduling. Safe existing SHA/evidence/isolation primitives can survive independently; no parallel verification scheduler.
- Maintain approved semantic proof obligations and a provenance-bound proof coverage contract. Runtime deterministically resolves it without guessing dependencies from filenames, imports or test history; unknown obligation remains blocking for an unsupported PASS.
- A single runtime execution plan must distinguish equivalent execution from genuinely different fixture, worker, order, profile, environment or integration obligations. Do not reexecute equivalent proofs with still-valid evidence; do not suppress a required distinct integration proof.
- Evidence validity has three outcomes: VALID, INVALIDATED, UNKNOWN. Keep historic subject and raw digests; correctly bind any reused proof to the current subject and conditions. Cross-run/correction reuse never infers safety from filenames alone.
- Failure attribution prefers already proven comparable baseline evidence, then minimal lawful comparison; serial results must not silently substitute for parallel/integration conformance.
- Retain exact preflight, scope, complete canonical phase/nodeid observations, immutable history, fail closed on unstable/incomplete evidence, and root-cause preservation for cleanup/observation errors.
- Any actual frozen Kernel schema change needs separately established Human constitutional/kernel authority; this roadmap approval alone cannot amend it.

## Seven bounded implementation packages

| Package | Outcome | Stage gate |
|---|---|---|
| VP-01 — Baseline & Measurement | Per-stage Runtime wall time, item unique/total count, command/Git/fixture startup, attribution/replay and cleanup measurement; feed BO-9B only | Proven measurement and historical baseline |
| VP-02 — Proof Contract & Compatibility | Semantic proof coverage, no Brain-authored test commands, frozen Kernel compatibility review | Policy/schema authority and proof soundness approved |
| VP-03 — Evidence Validity | Explicit reusable vs invalidated vs unknown proof and correction lineage bindings | No false PASS and exact immutable provenance |
| VP-04 — Single Proof Engine | Exactly one Runtime-owned execution plan replacing command-driven V2, no double-scheduler | No equivalent duplicate execution or under-verification |
| VP-05 — Correction & Attribution | Minimum lawful verification for PRIMARY/REPAIR/REMEDIATION/DIRECT_CANDIDATE, exact failure attribution | No unnecessary base replay or root rerun |
| VP-06 — Infrastructure Performance | Measured Git fixture, clone, collection, worker distribution, Windows process, fingerprint/evidence IO optimization | Correctness and conformance unchanged, benefit measured |
| VP-07 — Cutover & Conformance | Regression proof on real cases, prospective policy admission/cutover, historical decode continuity | Exact Runtime/Reviewer/Publisher-gated activation |

Dependencies: VP-01 → VP-02 → VP-03 → VP-04 → VP-05 → VP-06 → VP-07 for publication/cutover.

Human planning override (2026-10-09): VP-01 is explicitly DEFERRED, not completed or accepted; VP-02 is the unique effective NEXT for bounded semantic design and independently admissible work. This does not erase the VP-01 measurement dependency or authorize a publication/cutover claim that requires unproven VP-01 evidence. Any such exit stays blocked until fresh Human/Brain reconciliation and the relevant evidence; later VP work does not auto-advance. The planning override is not a frozen Kernel amendment. VP-06 profiling experiments may begin after VP-01 without mutating production ahead of authorized TASK sequencing. Every package requires an independently admitted TASK and normal runtime verification, review and Publisher publication; REPAIR must preserve prior successful proof until invalidated.

## Historical counterexamples and acceptance gates

- RUN-313-007: no broad base replay when exact candidate-only identity already blocks.
- RUN-316-003: keep original verification cause, bound/correct observation and Windows cleanup failure behavior.
- RUN-318-002: narrow REMEDIATION does not need full original TASK proof without invalidation.
- RUN-319-002: a raw pytest PASS is insufficient if canonical higher-level conformance blocks.
- RUN-320-001: distinguish verification time from Executor time.
- RUN-322-001/002/004: early blocking, intrinsically expensive focused tests, correction proof reuse.
- Real integration and unknown dependency cases: correct fail closed, do not infer PASS from a narrower probe.
- Track measured collection, materialization, test execution, attribution, evidence, cleanup, toolchain, Git subprocess and duplicate-proof execution; unknown metrics remain unknown and may not make lifecycle decisions.

## Branch/main publication safety

Canonical `main` outranks any local branch, planning branch, review-decision ref, repair ref, wake receipt, chat memory or old authoring baseline. Before each TASK, resync exact main, roadmap and canonical lineage and enforce expected-main/CAS for TASK authoring and PRIMARY. A reviewed repair candidate (including RUN-110-008 as of this planning decision) must be published only by the Publisher using its exact reviewed source or authorized integration. Never manually push source, force update a ref, cherry-pick or substitute a planning commit for publication. Downstream pin migration requires separate fresh downstream Brain Sync.

## Human-approved refinement v1.1 — no-double-verification and candidate/base replay gates (2026-10-09)

Status: HUMAN_APPROVED_PLANNING_REFINEMENT — not an admitted TASK, Runtime verification, semantic REVIEW PASS, source publication, kernel amendment, or VP implementation. This section refines the existing VP-01→VP-07 roadmap, not a new lifecycle or parallel policy. Previous planning paragraphs remain in force except where this refinement states stricter prospective acceptance gates.

### Normative architecture and authority

**VP_NO_EQUIVALENT_DOUBLE_EXECUTION**: No Runtime proof plan may repeat an equivalent verification execution when valid evidence already discharges the same approved proof obligation. Repetition may be legitimate only as a separately approved repeatability, ordering, integration, concurrency or other distinct proof obligation; such a distinction must be grounded, not inferred from the convenience of an execution runner.

**VP_BASE_REPLAY_BY_PROVEN_NECESSITY_ONLY**: Candidate-first is the default. Base materialization, base collection, base pytest execution and broad base replay are not ordinary stages automatically triggered by a candidate PASS or FAIL. Each incurs cost and requires proven eligibility. A decisive candidate blocking failure can fail the RUN without determining whether it is candidate-only; attribution may remain UNKNOWN, provided an independent mandatory comparison obligation is not silently discharged. A raw failure is never automatically a reproduction request.

Brain owns semantic proof obligations, accepted scope, non-goals and proof adequacy requirements; it does not author mandatory executable commands. Reviewed/versioned deterministic proof mappings are inputs to Runtime mechanical resolution, not a semantic dependency oracle, mutable planner or separate lifecycle store. Missing or ambiguous mapping is unresolved and cannot PASS. Exactly one Runtime-owned proof scheduler and canonical verifier replace V2 scheduling for prospectively admitted work. Executor implementation claims never become canonical evidence; Reviewer judges semantic sufficiency; Publisher publishes only the exact eligible reviewed source candidate.

### Base Replay Eligibility Gate — all six required

1. **BR-1 APPROVED_NECESSITY**: An approved proof obligation actually depends on baseline comparison, rather than requiring ordinary candidate conformance alone.
2. **BR-2 DECISION_RELEVANCE**: Comparison can resolve a currently undecided required outcome or satisfy a distinct mandatory comparison proof; diagnostic curiosity or desire to label every FAIL is insufficient.
3. **BR-3 NO_VALID_ALTERNATIVE**: Comparable baseline evidence is absent or proven invalid; first inspect valid immutable history without running base.
4. **BR-4 MINIMUM_LAWFUL_SCOPE**: Use the smallest scope that still covers the mandatory comparison, with a bounded declared method. Broad base command is never fallback merely because an exact probe failed or was ineligible.
5. **BR-5 EXACT_COMPARABILITY**: Relevant source identity, proof population, profile, toolchain, fixture, environment, ordering, concurrency and evidence collection conditions admit comparison; unknown/incompatible means no unsupported attribution.
6. **BR-6 AUTHORITY_COST_BOUNDS**: The operation is admitted within approved authority and explicit execution bounds, and does not re-run equivalent already-covered proofs.

If a gate fails, do not execute base merely for attribution. Reuse other lawful evidence, retain a decisive candidate failure, or return explicit UNRESOLVED/BLOCK. If a mandatory comparison itself is unresolved, fail closed; do not claim PASS. Cost bounds are a guard against unapproved escalation, never permission to skip mandatory proof.

### Candidate-first execution and failure semantics

Plan required proof coverage and equivalence before execution. Prefer one candidate execution that fulfills the broadest legitimate set of obligations without duplicate item execution. A passing candidate-delta probe is *not* proof of a distinct whole-suite/integration obligation; if broader execution truly requires different fixture, shared-state, worker, parallelism or ordering conditions, it is separately required and cannot be deduplicated by nodeid alone. If broad and narrow runs are provably equivalent over overlapping proof obligations, avoid overlapping work by lawful plan consolidation. Candidate failure only authorizes early BLOCK when a complete required blocking observation is established and no independent mandatory proof must still execute for the failure contract. Do not automatically rerun failed candidate nodeids for reproduction. Preserve canonical observation, original root cause and complete failure provenance; incomplete, unstable or conflicting observations remain non-reusable.

Prohibited automatic chain: candidate broad -> candidate exact reproduction -> base collection -> base exact probe -> base broad fallback. Fallback cannot silently widen into full-suite execution. A base-dependent accepted-preexisting-failure exception may need minimal comparable base proof, but only through BR-1–BR-6. Early stopping and attribution independence must be tested both for true failures and false-positive risks.

### Reuse across correction and operation types

Apply to PRIMARY, REPAIR, REMEDIATION and DIRECT_CANDIDATE. Each required proof has an immutable identity and applicability/condition footprint. Evaluate VALID, INVALIDATED or UNKNOWN before execution. VALID evidence may discharge the obligation through authorized lineage rebinding without falsifying the historical subject; INVALIDATED evidence needs fresh execution; UNKNOWN needs a bounded resolution or BLOCK. Never declare proof unaffected merely from filename overlap/non-overlap, matching nodeid, identical command or elapsed time. Relevant test code, fixture, shared helper, environment and policy changes invalidate appropriate evidence. REPAIR/REMEDIATION rerun affected proofs and new regression obligations, not the entire root TASK by default; scope can expand only on proven invalidation. Failure before canonical verification cannot manufacture PASS evidence.

### Required conformance counterexamples

- **RUN-313-007-shaped**: 796 candidate items, 776 base items, five candidate failures classified candidate-only (1,572 executed items historically). Under a decisive blocking candidate failure and no distinct comparative requirement, target **zero base executions**; no claim that VP itself has been measured or that all 796 candidate executions are necessarily minimal.
- **TASK-316 r5 / RUN-316-003 and RUN-316-006-shaped**: Valid baseline recovery and exact fixture/protocol negative coverage; initial broad affected proofs may genuinely be required. Later bounded correction reuses still-valid proofs; Windows cleanup/observation faults do not conceal or replace the original verification cause.
- **RUN-318-002-shaped**: Narrow remediation does not automatically re-prove the original root TASK.
- **RUN-319-002-shaped**: Raw pytest PASS cannot substitute for canonical higher-level conformance.
- **RUN-320-001-shaped**: Distinguish Runtime verification wall time from Executor time and infrastructure stages.
- **RUN-322-shaped**: Prior-PASS preservation under bounded correction; no ritual root replay without evidence invalidation.
- Mandatory integration, serial-vs-parallel, changed shared fixture, flaky/non-reproducible, incomplete collection, conflicting cache, missing mapping and genuinely baseline-dependent accepted-preexisting failure counterexamples must also be covered.

### Mandatory acceptance criteria for VP-07 cutover

- **AC-01** All mandatory semantic obligations are covered by valid proof or explicitly BLOCK/UNRESOLVED.
- **AC-02** No false PASS from stale, incomplete, conflicting or unbound evidence.
- **AC-03** Distinct integration, concurrent, fixture and ordering proofs are preserved.
- **AC-04** Unknown validity or incomparable outcomes do not silently become PASS.
- **AC-05** Ordinary candidate PASS causes zero base collection/execution absent approved comparison.
- **AC-06** Decisive blocking candidate failure causes zero base replay absent separate comparison obligation.
- **AC-07** No execution of equivalent proof twice absent approved distinct repetition requirement.
- **AC-08** No automatic candidate failure reproduction when complete existing observation suffices for a blocking verdict.
- **AC-09** No automatic broad base fallback from an ineligible/failed narrow attribution attempt.
- **AC-10** Every admitted base execution has documented BR-1–BR-6 eligibility, reason, exact binding, scope and measured cost.
- **AC-11** REPAIR preserves and reuses unaffected VALID evidence.
- **AC-12** REMEDIATION likewise preserves valid root evidence unless invalidated.
- **AC-13** Unstable/conflicting/incomplete proof is never reusable.
- **AC-14** Failure and cleanup/observation error provenance is preserved without root-cause substitution.
- **AC-15** Immutable V1/V2 historical lineage and read-only decode remain intact.
- **AC-16** Prospective Brain authoring does not contain mandatory executable test command lists, including disguised copies in a new field.
- **AC-17** Exactly one production Runtime scheduler; V2 and new scheduling cannot both execute for the same admitted work.
- **AC-18** Frozen Kernel compatibility is resolved by its rightful authority; planning approval is not a Kernel amendment.
- **AC-19** Human, Brain, Executor, Runtime, Reviewer and Publisher authority boundaries remain distinct.
- **AC-20** Real execution counts, wall-clock breakdown, proof equivalence and reuse evidence demonstrate the benefit; no simulated savings claim.

### Package allocation and ordering

- **VP-01**: Historical baseline measurement of candidate/base executions, item unique/total counts, materialization, pytest collection, fixture, Git, startup, attribution, cleanup and wall time. Deferred by Human on 2026-10-09; not DONE, not waived.
- **VP-02 (sole NEXT)**: Authoritative semantic proof contract, deterministic mapping requirements, frozen Kernel compatibility analysis, candidate-first and BR-1–BR-6 hard gates, conformance criteria. Early bounded design allowed under Human priority override; VP-01-dependent acceptance/publication still gated.
- **VP-03**: Exact evidence validity, proof equivalence and cross-correction applicability bindings, with UNKNOWN fail-close.
- **VP-04**: One candidate-first proof scheduler, deduplication, lawful short-circuit and zero unnecessary candidate or base repetition.
- **VP-05**: Correction-local proof execution, mandatory-comparison eligibility, minimal lawful attribution, no broad fallback after a decisive BLOCK.
- **VP-06**: Instrumented Git/collection/fixture/process/worker/Windows and IO optimization, without violating soundness or repeatability.
- **VP-07**: AC-01–AC-20 conformance against historic shaped cases plus genuine negative/integration cases; both correctness and no-double-execution effectiveness required for cutover.

Use existing BO-9B telemetry (no second telemetry system) for candidate/base pytest item executions, equivalent duplicate item executions, reused/invalidated proofs, base and reproduction avoided counts, collection/materialization/execution/cleanup wall time, exact blocking reason, and UNKNOWN obligations. No test-count or latency claim without real evidence. Optimize **Verified Useful Work / (Time + Tokens + Human Effort)**, not an unsupported promise that every run executes fewer tests.

### Brain-high-value-v3 architecture reconciliation

Stage 1 CONSTRUCT: RISK_FOUND across all eight lenses — authority leak from Runtime semantic dependency inference; scope expansion; lineage/evidence false reuse; candidate/base replay counterexamples; proof completeness vs deduplication tension; verification/Reviewer ordering; machine-local portability; dual scheduler/duplicate authority.

Stage 2 ADVERSARIAL_AUDIT_AND_RECONCILE: each risk receives a binding resolution above (reviewed deterministic mapping, no semantic runtime guessing, BR-1–BR-6, evidence validity, distinct required integration proofs, bounded UNKNOWN/BLOCK, exact provenance, single scheduler). Outcome is a **semantic architecture candidate subject to mandatory VP implementation/conformance gates**, not a machine-validated Brain audit payload, executable proof, Runtime PASS or Reviewer verdict.

### Planning and publication safety

This amendment is Human-approved planning only. It selects no Executor, creates no TASK/RUN, grants no engineering mutation authority, publishes no implementation, and triggers no automatic roadmap advancement. Keep VP-01 DEFERRED_BY_HUMAN, VP-02 the only NEXT, VP-03–VP-07 queued, and BO-2/3 paused. All future engineering TASK authoring must start from freshly synchronized canonical main with ordinary admission, exact SHA/CAS, Runtime verification, Reviewer verdict and Publisher source publication.

## Human-approved refinement v1.2 — Bounded Intra-Run Correction (2026-10-09)

Status: HUMAN_APPROVED_PLANNING_REFINEMENT — NOT IMPLEMENTED, NOT A TASK/RUN, NOT A KERNEL AMENDMENT. The Human approved the previously drafted two-stage audit on 2026-10-09. This refinement adds scoped requirements to VP-02..VP-07; it does not create VP-08, alter v1.1, waive VP-01, change the unique roadmap NEXT, or grant production execution authority.

**Contract:** VP_BOUNDED_INTRA_RUN_CORRECTION_V1.
**Audit profile:** brain-high-value-v3, Stage 1 CONSTRUCT = RISK_FOUND across eight lenses; Stage 2 ADVERSARIAL_AUDIT_AND_RECONCILE = CLEAR_WITH_MANDATORY_GATES. This verdict judges a *planning architecture*, not actual Kernel compatibility, Runtime proof, Reviewer PASS or implementation eligibility.

### Intent and lifecycle boundary

In the first prospective opt-in version, an admitted PRIMARY RUN may perform a bounded sequence of committed candidate C1 -> Runtime-owned preterminal proof checkpoint K1 -> typed factual failed-proof feedback -> the same authorized Executor implements a correction within the same TASK scope -> committed candidate C2 -> Runtime verifies the minimally outstanding/invalidation-dependent proof obligations. A RUN emits exactly one canonical terminal RESULT when every mandatory obligation is discharged, or a canonical terminal FAILURE when continuation is inadmissible/exhausted. Subsequent correction of a terminal FAILURE remains existing AUTHOR_REPAIR on a new admitted correction RUN; REVIEW CHANGES_REQUIRED remains REMEDIATION. Intermediate checkpoint FAIL is never fabricated as canonical FAILURE, REVIEW or REPAIR.

Frozen Kernel v0.1 allows Executor-local targeted test/fix iteration, but autonomous retries were deferred. **Runtime-driven canonical preterminal feedback is a distinct prospective semantic extension**. VP-02 MUST resolve frozen-schema/authority compatibility explicitly; if a Kernel amendment is required, STOP for separate Human/kernel authority and separately admitted implementation. Planning approval here does not silently amend Kernel or grant a new retry controller.

### Normative invariants (IR-01..IR-11)

- **IR-01 — Immutable identity and sticky lease:** Bind TASK revision/blob, admitted RUN and base, committed clean candidate SHA, checkpoint ordinal/content hash/previous hash, contract/mapping digest, exact Executor/profile/model/effort, live lease, proof conditions, environment/toolchain and evidence provenance. One Executor mutation authority at a time; no implicit model switch or Executor push.
- **IR-02 — Preterminal distinct from terminal:** Runtime-owned checkpoint observations are durable and immutable while RUN remains ACTIVE; they are not canonical RESULT/FAILURE/REVIEW/REPAIR. Exactly one terminal outcome. A terminal RUN cannot be reopened or overwritten.
- **IR-03 — Typed factual feedback only:** Transfer failed proof/acceptance IDs, exact subject, completeness/validity, evidence and bounded diagnosis/budget; do not prescribe code edits, select semantic correction strategy, broaden TASK scope or accept instructions embedded in untrusted logs.
- **IR-04 — Strict continuation eligibility:** Require explicit opt-in authority, complete/stable candidate-proof failure, unchanged permitted scope/delegation/lease, no new Human risk or Brain semantic choice, unexpired bounds and trustworthy candidate conditions. Raw pytest nonzero alone is not enough. Missing/ambiguous/unknown gates block or escalate; no blind fix/retry.
- **IR-05 — Candidate-to-candidate proof validity:** The single Runtime proof validity boundary determines VALID, INVALIDATED or UNKNOWN using complete applicability footprints and immutable source-to-current-subject applicability witnesses. Never relabel C1 proof as executed on C2. Filename disjointness, identical nodeids and command strings are insufficient. Track test code, shared fixtures/helpers/state, toolchain, environment, profile, concurrency, worker, ordering, collection and integration dependencies. UNKNOWN never silently PASS.
- **IR-06 — No equivalent double verification:** The final successful checkpoint may discharge terminal RESULT proof without rerunning equivalent commands. Runtime binds existing evidence only after exact validity/currentness checks. Execute only invalidated/new obligations; preserve distinct integration, serial/parallel, repetition or ordering coverage. No automatic same-SHA reproduction, broad root replay, duplicated narrow/broad proof or baseline replay without all BR-1..BR-6.
- **IR-07 — Finite cost and progress:** Pre-admission Human-authorized finite bounds on correction count, wall time, test resources and applicable token cost. Each correction must produce a distinct committed candidate and causally relevant proof basis; exhaustion/no-progress cannot enlarge its own limits. Budgets never authorize omission of mandatory proof.
- **IR-08 — No authority merger:** Human owns risk/delegation; Brain WHAT/WHY, scope and proof obligations; Executor HOW; Runtime admission, verification, evidence, deterministic bounds and terminal state; Reviewer independent verdict on exact final candidate; Publisher exact reviewed source. Neither runtime nor transport selects a correction strategy, REVIEW verdict or roadmap successor.
- **IR-09 — Crash, replay and interruption:** Exact monotonic idempotent checkpoint identity; duplicate feedback must not consume another correction, rerun proof or create a second RUN. Missing/stale/tampered checkpoint, lost lease, nonrecoverable worker state or uncertain provenance fails closed. Pre-admission operational failure cannot fabricate RUN/FAILURE; admitted terminal failure continues through existing REPAIR lineage.
- **IR-10 — Frozen Kernel and legacy containment:** Separate lawful Kernel compatibility/amendment gate at VP-02; no implicit schema expansion, no reclassification of historical V1/V2 artifacts, no replacement of existing correction semantics and no unauthorized correction transport.
- **IR-11 — Measurable, opt-in deployment:** Reuse BO-9B episode telemetry rather than a second metrics store. Attribute total wall time, Executor time, collection/fixture/verification, unique/total pytest items, invalidated/reused evidence, correction count, interventions, token cost and avoided handoffs with exact provenance. Prospective opt-in only after VP-07 conformance and normal Runtime/Reviewer/Publisher activation; downstream pin/adoption remains separate.

### Two-stage architecture audit and adversarial closure

Stage 1 CONSTRUCT identified risks across all eight brain-high-value-v3 lenses: AUTHORITY_BOUNDARY, SCOPE_NON_GOALS, PROVENANCE_LINEAGE, FAILURE_MODE_COUNTEREXAMPLES, AC_CONSISTENCY_COMPLETENESS, VERIFICATION_OWNERSHIP_ORDERING, PORTABILITY_PRIVACY_BOUNDEDNESS and SIMPLIFICATION_DUPLICATE_AUTHORITY. Stage 2 reconciled them through IR-01..IR-11 and the mandatory negative cases below; Stage-2 output is CLEAR_WITH_MANDATORY_GATES only.

Mandatory negative/progression cases include: C1 proof PASS retained only with a lawful witness on C2; changed test/helper/fixture/profile/environment correctly invalidates; targeted green never substitutes for required integration/concurrency/ordering; terminalization never repeats final equivalent proof; no automatic exact-failure reproduction or base suite; pre-existing comparison only with BR-1..BR-6; incomplete/unstable raw observation UNKNOWN/BLOCK; malicious log instructions untrusted; weakened tests cannot bypass semantic acceptance/Reviewer; no change beyond authorized scope; duplicate feedback, lost lease, crash and stale checkpoint fail closed; main movement never silently rebases admitted RUN; a terminal FAILURE is not revived; pre-AIOS failure does not manufacture a RUN; old downstream pin and frozen historical artifacts remain unchanged.

### Package allocation (existing seven packages only)

- **VP-01:** remains DEFERRED_BY_HUMAN; later measurement must compare whole old terminal-REPAIR episode versus the proposed intra-RUN path, not manufactured savings.
- **VP-02:** establish explicit preterminal/terminal semantics, typed feedback contract, opt-in eligibility, sticky delegation/finite bounds and the frozen Kernel compatibility/amendment authority gate.
- **VP-03:** exact checkpoint identity and history; prove C1->C2 proof applicability witnesses, VALID/INVALIDATED/UNKNOWN outcomes and crash/dedup safety.
- **VP-04:** exactly one Runtime proof scheduler for checkpoints and finalization, strict no-double-execution and no unapproved base replay; never run V2 and the new scheduler concurrently for one work item.
- **VP-05:** bounded factual feedback continuation to the *same* admitted Executor, hard stop/escalation and terminal FAILURE -> canonical REPAIR; no generic correction selection engine.
- **VP-06:** measure checkpoint overhead, feedback/re-entry, worker/fixture/IO costs, proof execution/reuse and total episode efficiency rather than only test item counts.
- **VP-07:** real and adversarial conformance, opt-in prospective rollout/rollback, legacy decode continuity, Kernel gate, normal Runtime verification, independent Reviewer PASS and Publisher activation.

### Additional mandatory VP-07 acceptance criteria

Existing AC-01..AC-20 remain fully in force.

- **AC-21:** No historical TASK/RUN/RESULT/FAILURE/REPAIR reinterpretation and no frozen Kernel change absent separate authority.
- **AC-22:** One exact sticky Executor/profile/model/effort lease across all checkpoints; no implicit failover.
- **AC-23:** Intermediate candidate, proof checkpoint and failed observation identities are immutable, monotonic, SHA-bound and nonterminal.
- **AC-24:** Typed feedback is bounded, diagnostic only, untrusted-output-safe and cannot decide HOW or semantic strategy.
- **AC-25:** Eligible C1 candidate-proof FAIL -> authorized correction C2 in one RUN -> exactly one terminal RESULT if all obligations hold.
- **AC-26:** No automatic equivalent same-SHA test repetition; final checkpoint PASS is never redundantly reverified at terminalization.
- **AC-27:** C1->C2 reuse requires a proven applicability witness; fixture/helper/policy/profile/worker/environment changes invalidate or yield UNKNOWN appropriately.
- **AC-28:** Distinct integration, concurrency, ordering and required broad coverage are not suppressed to achieve narrow green.
- **AC-29:** Every baseline replay still passes all six BR gates; a candidate failure alone authorizes neither reproduction nor replay.
- **AC-30:** Budget, no-progress, structural/infra instability, scope/risk/authority conflict, interrupted execution and lost lease stop safely.
- **AC-31:** Terminal FAILURE retains canonical AUTHOR_REPAIR, new correction RUN and sticky Executor preservation; no hidden authorization.
- **AC-32:** Single Runtime scheduler and existing lifecycle/telemetry authority; no second router or dual V2/new plan.
- **AC-33:** Crash/replay/duplicate feedback cannot repeat proof or consume attempts without proven invalidation; stale/tampered checkpoint fails closed.
- **AC-34:** Exact live wall time, total/unique test counts, evidence reuse, tokens, interventions and correction cost are measured; UNKNOWN stays UNKNOWN.
- **AC-35:** Kernel compatibility or separately Human-approved amendment is resolved before production cutover and normal TASK/Runtime/Reviewer/Publisher gates are proven.
- **AC-36:** Legacy and downstream remain unaffected without independent opt-in and explicit dependency/adoption; no automatic roadmap-next transition.

### Planning-only disposition

This v1.2 refinement is Human-approved architecture with mandatory implementation gates, not Runtime proof, a semantic REVIEW verdict, a production feature, a Kernel amendment, or authorization to run an Executor. It leaves VP-01 deferred, VP-02 blocked until its current predecessor/priority reconciliation, VP-03..VP-07 queued, BO-2/3 paused, and the existing unique publication-related NEXT unchanged. Every future VP TASK must be independently authored from fresh canonical main under explicit Human Executor delegation, verified by Runtime, reviewed by Reviewer and source-published by Publisher.

## Non-goals

No new Planner, generic execution router, Retry/Failover authority, Reviewer, Publisher, independent telemetry system, semantic dependency guesser, automatic roadmap transition, retroactive V1/V2 artifact migration, silent Kernel amendment, or Brain implementation of production code.
