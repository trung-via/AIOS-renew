# AIOS-renew Performance Closure

**Status:** Human/Brain planning state — PENDING after TASK-091  
**Authority:** Subordinate to the AIOS Manifesto, Constitution, frozen Kernel, Project Contract, current canonical Git state, and exact immutable engineering lineage.  
**Purpose:** Improve `Verified Useful Work / (Time + Tokens + Human Effort)` without weakening authority separation, evidence, correction continuity, or deterministic safety gates.

## Why this track exists

A canonical audit of the ten most recently executed TASKs (TASK-081 through TASK-090) found 27 RUNs consuming about 535.35 minutes of admitted runtime. About 354.45 minutes (66.2%) were Executor time and about 180.81 minutes (33.8%) were Runtime-owned canonical verification; deterministic coordination overhead itself was negligible by comparison.

The dominant efficiency risks are therefore not the existence of Runtime safety gates. They are:

- large Executor/context surfaces around `operator.py` and related tests;
- coarse verification granularity during narrow correction cycles;
- repeated broad verification where a smaller affected set could establish the same authorized claim;
- review/correction lineage amplification when several independent findings are discovered in one PRIMARY review but must be corrected serially;
- long Executor attempts that can consume nearly the full native execution budget before terminal failure;
- TASK inspection scopes that increasingly carry historical predecessor context that may not all be necessary for the current authority boundary.

Observed examples include TASK-085 PRIMARY at about 62 minutes, TASK-086 with six RUNs and about 159 minutes total lineage runtime, a TASK-086 remediation that reached about 65 minutes before FAILURE, and multiple TASK-086 remediation candidates repeatedly running roughly 250+ tests for about 8–9 minutes each.

## Constitutional constraints

Performance work must preserve all of the following:

- Human owns intent and priority.
- Brain owns WHAT/WHY; exactly one Executor owns HOW.
- Runtime remains deterministic and owns canonical verification/evidence.
- Reviewer remains the sole semantic verdict authority.
- Publication remains separate.
- One active canonical mutation authority remains invariant.
- Exact SHA/lineage, Git truth, scope, clean-worktree, completion, changed-files, failure taxonomy, correction continuity, and publication race protections must not be weakened for speed.
- No automatic retry, reroute, fallback, hidden planner, verifier agent, reviewer agent, or autonomous correction loop is introduced merely to improve latency.
- Evidence may be reused only while the state supporting it remains valid; optimization must not pretend evidence for one SHA proves another SHA.

## Closure sequence

Performance Closure is intentionally sequenced after TASK-091 is semantically reviewed PASS and published so the third-executor extension can be measured against the same stabilized current product surface. Exact TASKs should be authored only when their predecessor state is canonical, to avoid stale contracts.

### P1 — Verification Granularity

Goal: reduce correction verification cost while preserving Runtime ownership and evidence quality.

Design direction:

- decompose broad test surfaces into stable semantic groups aligned with existing authority boundaries;
- make REMEDIATION/REPAIR `affected_verification` minimum-sufficient rather than routinely re-running large unrelated suites;
- detect exact duplicate verification commands and subset-then-full-suite duplication against unchanged state before RUN admission;
- preserve full-suite execution when a TASK or correction genuinely invalidates broader evidence;
- keep test selection deterministic and contract-driven, not heuristic or model-selected.

Success evidence should include measured reduction in correction verification p50/p95 with no regression in semantic review findings attributable to missing required verification.

### P2 — Operator Context Decomposition

Goal: reduce Executor context and test amplification without creating new authority.

Design direction:

- keep one Human-facing Operator façade;
- extract pure deterministic domains from the large `operator.py` implementation into narrow modules with explicit responsibilities;
- split `test_operator.py` correspondingly by semantic boundary;
- do not create a second lifecycle reducer, second admission implementation, router, planner, or orchestration layer;
- preserve public command behavior and exact canonical authority ownership.

Source-size reduction alone is not success. Success is lower bounded context and more focused verification while behavior remains equivalent.

### P3 — Correction Lineage Efficiency

Goal: prevent serial review/remediation bookkeeping from rediscovering or renaming already-known unresolved defects unnecessarily.

Design direction:

- PRIMARY review may report all material findings it actually observes;
- correction execution remains narrow and lineage-bound;
- unresolved findings from the same reviewed candidate should be carried structurally as known outstanding semantic state when possible rather than rediscovered from scratch;
- any optimization must preserve exact predecessor/finding identity and DELTA-review semantics;
- do not batch unrelated mutations merely to save review cycles.

This item requires especially careful governance review because it touches correction semantics. If a frozen semantic change is required, it must be handled as an explicit Human-authorized post-freeze evolution rather than an implementation shortcut.

### P4 — Execution Budget Observability and Escalation

Goal: avoid large silent sunk-cost attempts while keeping legitimate heavy tasks possible.

Design direction:

- retain bounded native watchdogs;
- measure operation-specific Executor duration distributions for PRIMARY, REMEDIATION, and REPAIR;
- surface soft-budget thresholds as observation/escalation information rather than automatic retry/reroute;
- do not terminate a valid long-running task solely because a narrow-task median is lower;
- any hard-budget change requires observed evidence and explicit contract impact analysis.

### P5 — Performance Observation Surface

Goal: make the North Star measurable from canonical operational telemetry without creating engineering truth.

A read-only bounded surface may summarize existing `RUN_OBSERVATION` facts such as:

- RUN counts by operation and terminal kind;
- Executor and verification p50/p95;
- correction depth and failure rate;
- observed input/output/cached tokens where available;
- timeout/watchdog incidence;
- verification share of admitted runtime.

This is analytics only. It must not infer semantic PASS, select Executors, mutate roadmap state, or become completion evidence.

## TASK authoring discipline for this track

Before each Performance Closure TASK:

1. BRAIN SYNC current main and exact latest lineage.
2. Read only the directly relevant predecessor contracts and implementation.
3. Identify the one authority boundary being changed.
4. State the measured waste being reduced.
5. Define a minimum-sufficient verification contract with no subset/full-suite duplication on unchanged state.
6. Require before/after runtime evidence when the change claims an efficiency improvement.
7. Preserve all existing fail-closed semantics unless explicit Human authority authorizes a semantic change.

## Exit criteria

Performance Closure may be considered complete only when canonical evidence demonstrates that:

- correction verification cost is materially reduced on representative narrow fixes;
- Executor context/test surfaces are measurably narrower around the Operator hotspot;
- no constitutional authority has moved or been duplicated;
- no safety gate has been removed merely to improve benchmark time;
- correction lineage remains exact and reviewable;
- recent-task performance telemetry can be reproduced without manual forensic reconstruction;
- the improvements raise verified useful work per time/token/human effort rather than only shifting cost between layers.

After Performance Closure is completed and semantically published through its exact TASK lineage, the roadmap may proceed to Downstream Adoption unless Human explicitly changes priority again.
