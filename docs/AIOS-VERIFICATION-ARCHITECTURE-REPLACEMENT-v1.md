# AIOS-Renew Verification Architecture Replacement v1

Status: HUMAN_APPROVED_PLANNING (refined v1.1 on 2026-10-09) — NOT AN ADMITTED TASK, REVIEW OR PUBLICATION
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

## Non-goals

No new Planner, generic execution router, Retry/Failover authority, Reviewer, Publisher, independent telemetry system, semantic dependency guesser, automatic roadmap transition, retroactive V1/V2 artifact migration, silent Kernel amendment, or Brain implementation of production code.
