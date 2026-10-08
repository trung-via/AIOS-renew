# AIOS-Renew Verification Architecture Replacement v1

Status: HUMAN_APPROVED_PLANNING — NOT AN ADMITTED TASK, REVIEW OR PUBLICATION
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

Dependencies: VP-01 → VP-02 → VP-03 → VP-04 → VP-05 → VP-06 → VP-07 for publication/cutover. VP-06 profiling experiments may begin after VP-01 without mutating production ahead of authorized TASK sequencing. Every package requires an independently admitted TASK and normal runtime verification, review and Publisher publication; REPAIR must preserve prior successful proof until invalidated.

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

## Non-goals

No new Planner, generic execution router, Retry/Failover authority, Reviewer, Publisher, independent telemetry system, semantic dependency guesser, automatic roadmap transition, retroactive V1/V2 artifact migration, silent Kernel amendment, or Brain implementation of production code.
