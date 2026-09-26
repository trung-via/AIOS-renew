# AIOS Brain Portability Architecture and Roadmap

Status: HUMAN-APPROVED PLANNING BASELINE  
Approved by Human: 2026-09-17  
Scope: provider-independent Brain/Reviewer continuity and zero-touch control-plane hardening above the frozen Kernel v0.1

## 1. Purpose

This baseline records the Human-approved BP-0 architecture for making AIOS-renew independent of any one Brain model/provider while preserving the existing authority model, canonical engineering truth, exact immutable lineage, GitHub/self-hosted execution path, and frozen Kernel v0.1 semantics.

The target is not a larger agent framework. The target is a deterministic cognitive-support layer that prepares exact bounded context for a replaceable Brain or Reviewer, validates the returned semantic decision, and then delegates only to existing canonical AIOS surfaces.

The optimization target remains:

```text
Verified Useful Work / (Time + Tokens + Human Effort)
```

## 2. Core architectural rule

If an answer is uniquely determined by canonical state plus deterministic rules, the Brain must not be required to remember or reason it out.

If a decision requires semantic judgment, architecture, task meaning, review judgment, correction strategy, or interpretation of Human intent, that decision remains with the applicable Human/Brain/Reviewer authority and is not moved into Runtime.

## 3. Target architecture v1

```text
Human Intent / Priority
        |
        v
Deterministic Context Composition
  - canonical repository/main identity
  - roadmap planning bookmark
  - Brain Sync snapshot
  - Unified State lifecycle facts
  - current explicit Human input
        |
        v
Thin Flow Resolution
        |
        v
Repository-owned Flow Card
        |
        v
Deterministic Decision Packet
        |
        +-------------------------+
        |                         |
        v                         v
AIOS_BRAIN_REQUEST          AIOS_REVIEW_REQUEST
        |                         |
replaceable Brain           replaceable Reviewer
provider                    provider
        |                         |
        v                         v
AIOS_BRAIN_DECISION         AIOS_REVIEW_DECISION
        |                         |
        +-----------+-------------+
                    v
        deterministic validation
                    |
                    v
        existing canonical surfaces
  authoring ingress / Runtime / Publisher
                    |
                    v
       GitHub + self-hosted execution
                    |
                    v
       canonical terminal / typed receipt
                    |
                    v
                fresh sync
```

This is composition above existing primitives, not a new Kernel peer, Planner agent, generic lifecycle router, or independent source of lifecycle truth.

## 4. GitHub connector and self-host boundary

Brain-provider portability must not depend on a provider-specific GitHub connector.

A Brain provider may receive one bounded structured request and return one bounded structured decision without direct GitHub access. AIOS/control-plane code may read canonical Git/GitHub state, compile the request, validate the decision, and use the existing ingress/wakeup/publication surfaces.

This does not remove GitHub and does not remove self-hosting. GitHub remains the current canonical repository, ref/artifact transport, workflow trigger, publication surface, and audit trail. Windows self-hosted runners remain the current execution environment for AIOS Runtime and Codex/Antigravity. Provider-neutrality only removes a hard dependency on a specific model's GitHub connector.

## 5. Authority matrix

| Concern | Authority | Deterministic support may | Deterministic support must not |
| --- | --- | --- | --- |
| Product intent, priority, risk | Human | preserve and bind provenance | invent or change priority |
| Roadmap architecture | Human / Brain | validate currentness and conflicts | advance semantics autonomously |
| TASK meaning / WHAT / WHY | Brain | prepare context and validate schema | prescribe implementation HOW |
| Implementation HOW | one Executor | enforce admission/scope/SHA/evidence gates | implement on behalf of Executor |
| Lifecycle engineering state | Runtime + canonical lineage | reduce exact state deterministically | become semantic Planner |
| Semantic review | Reviewer | compile exact review context | decide PASS/CHANGES_REQUIRED deterministically |
| REMEDIATION / REPAIR strategy | Brain / Human | expose exact finding/failure/selectors | infer a correction automatically |
| Executor choice where required | Human / existing surface | validate supported identity | auto-route based on model scoring |
| Canonical verification | Runtime | execute and capture evidence | delegate truth to Brain/Executor claims |
| Publication | Publisher | validate reviewed candidate | let Brain/Executor publish directly |
| Provider implementation choice | Human/configuration | validate adapter/config | create model router/scoring authority |
| Transport | subordinate carrier | deliver exact bounded payload | become semantic or engineering truth |

## 6. Work Context v1

`AIOS_BRAIN_WORK_CONTEXT` is a transient, request-scoped projection. It is not a database, memory store, lifecycle state, or independent planning authority.

It may compose:

- canonical repository and main identity;
- current explicit Human intent when present;
- canonical roadmap bookmark;
- exact semantic subject identity;
- Brain Sync and Unified State references/projections;
- selected flow identity;
- bounded semantic provenance and invalidation refs.

It must not persist or duplicate:

- chat transcripts;
- chain-of-thought;
- model memory;
- RUN/RESULT/FAILURE truth already owned elsewhere;
- duplicated `next_action` authority;
- raw logs or secrets;
- persistent learning.

Durable semantic decisions remain in their existing authoritative artifact families: roadmap/architecture planning, TASK, REVIEW, REMEDIATION, REPAIR, or other explicitly authorized canonical documents.

## 7. Flow Resolver v1

The Flow Resolver is deterministic and read-only. It does not decide general lifecycle progression because Unified State already owns deterministic lifecycle reduction.

Its bounded responsibility is to identify which semantic reasoning contract is applicable when current authority belongs to Brain or Reviewer, for example:

- ARCHITECTURE;
- TASK_AUTHORING;
- SEMANTIC_REVIEW;
- REMEDIATION_AUTHORING;
- REPAIR_AUTHORING;
- DIAGNOSTIC.

Operational actions such as `EXECUTE_PRIMARY`, `EXECUTE_REMEDIATION`, `EXECUTE_REPAIR`, publication, wait, or done do not become Brain reasoning flows merely because the resolver exists.

## 8. Flow Card v1

A Flow Card is repository-owned procedural policy containing only bounded entry conditions, required/optional/forbidden context, allowed decision types, handoff target, expected return shape, branch conditions, and invalidation rules.

A Flow Card must not contain a predetermined semantic verdict, automatic correction choice, automatic executor choice, model-specific reasoning instructions, or hidden lifecycle authority.

## 9. Decision Packet v1

The Decision Packet is deterministic, bounded, read-only context compiled for one semantic decision.

It must distinguish deterministic facts/observations/Human input from hypotheses and semantic judgment. AIOS may supply the former; Brain/Reviewer owns the latter.

Typical packet content may include exact TASK identity/contract, exact RUN and candidate selectors, bounded implementation delta, canonical RESULT/EVIDENCE, relevant review/correction lineage, Unified State projection, and explicit Human input. It must not embed unbounded repository context, chat history, secrets, or chain-of-thought.

## 10. Provider contracts v1

Brain and Reviewer remain separate semantic authorities even if the same model implementation can fill either role.

Provider-neutral contracts are therefore separate:

```text
AIOS_BRAIN_REQUEST  -> Brain Provider   -> AIOS_BRAIN_DECISION
AIOS_REVIEW_REQUEST -> Review Provider  -> AIOS_REVIEW_DECISION
```

Provider identity is implementation metadata, not authority identity. A provider does not need direct GitHub access if AIOS supplies the complete bounded request.

Returned decisions must carry exact subject identity, decision type/value, bounded artifact-reference basis, uncertainty state where applicable, and invalidation/CAS expectations. Chain-of-thought is neither required nor canonical.

## 11. Execution handoff rule

No generic execute-anything lifecycle router is authorized.

An internal handoff envelope may share framing/provenance, but operation selectors remain family-specific:

- PRIMARY: exact authorized TASK semantic identity plus explicit Executor;
- REMEDIATION: exact source RUN + finding + approved correction identity + explicit Executor;
- REPAIR: exact failed RUN + current REPAIR authorization SHA + Executor only where the repair action requires one.

Existing canonical operation surfaces remain authoritative.

## 12. PRIMARY Exact Intent Binding v2

Future PRIMARY authorization must bind the exact semantic TASK identity, not only `task_id`.

The v2 authorization contract must bind at least:

- `task.id`;
- `task.revision`;
- exact canonical TASK artifact identity (Git blob SHA or equivalent immutable content identity);
- canonicalization provenance sufficient to establish that the authorized TASK existed on canonical lineage;
- explicit Executor identity.

After self-host synchronization, admission must prove the exact authorized TASK semantic artifact is still current. An unrelated newer `main` commit is allowed when the exact TASK artifact is unchanged. TASK revision/content drift must fail closed before RUN creation and before Executor invocation.

Historical v1 dispatch records remain immutable and readable/reconcilable. New authorizations after v2 rollout must not silently fall back to v1 semantics.

## 13. Operational receipt v2

Operational receipts are subordinate observability, never engineering-state truth.

The control-plane should make these boundaries attributable where applicable:

```text
CARRIER_ADMITTED
RUNNER_STARTED
AIOS_INVOKED
ADMISSION_ACCEPTED / ADMISSION_REJECTED
RUN_ATTRIBUTED
OPERATIONAL_FAILED
TERMINAL_POINTER
```

Pre-AIOS failure must carry a typed bounded boundary/reason without fabricating a RUN or FAILURE. Once a canonical terminal exists, the receipt carries only a bounded pointer to canonical identity/artifact truth.

Human-facing projections must preserve typed lower-layer causes rather than collapsing all delegated failures to a generic code when an authoritative typed cause exists.

## 14. Explicit non-goals

BP-0 does not authorize:

- Planner agent;
- generic lifecycle router;
- model router or provider scoring;
- multi-agent voting;
- multiple-reviewer consensus;
- autonomous retry/reroute/failover;
- persistent learning database;
- chat-history/conversation database;
- orchestration database or broker merely for architecture symmetry;
- second Unified State;
- second correction frontier;
- second publication authority;
- generic execute-anything endpoint;
- automatic correction strategy;
- automatic executor selection;
- rewriting frozen Kernel v0.1 history.

## 15. Ordered implementation roadmap

### BP-1 — PRIMARY Exact Intent Binding v2

Close the confirmed PRIMARY TOCTOU/intent-binding gap. New PRIMARY authorization binds exact TASK revision/artifact identity while allowing unrelated newer `main` state when the authorized TASK remains semantically identical.

Exit gate: stale TASK revision/content fails before RUN; unchanged authorized TASK can execute on a newer admissible base; historical v1 dispatch records remain readable.

### BP-2 — Operational Attribution v2

Close the self-host/pre-AIOS dark zone and preserve typed lower-level causes through Human-facing projections.

Exit gate: every addressed handoff can be explained by a typed operational boundary or canonical pointer without inventing lifecycle truth.

### Verification Foundation planning gate — TA-0 / TA-1

Human-approved on 2026-09-20 against canonical main `edd7d8d92d54900c56442bbfcddb8648ec4d2e09`.

TA-0 and TA-1 are read-only architecture audits that prospectively harden the Brain Portability roadmap. They do not reopen or rewrite historical Performance Closure TASK/RUN/REVIEW evidence, do not themselves authorize production mutation, and do not change frozen Kernel v0.1 semantics.

The audits established the following planning facts:

- minimum-sufficient verification and reuse-until-invalidated remain constitutional requirements;
- the historical Performance Closure P1 design required duplicate/subset-then-full-suite detection before RUN admission, but current TASK validation only rejects exact duplicate command strings and Runtime executes the authorized command list in order;
- RUN-145-002 spent 1450.91 seconds in Runtime verification; its one full-suite command ran 1207 tests in 1343.67 seconds, so intrinsic full-suite cost is a separate defect from nested verification duplication;
- RUN-144-006 showed the Git-heavy `test_operator.py + test_authoring_ingress.py + test_publication.py` group consuming 1192.47 seconds for 378 tests while the full 1222-test suite consumed 1578.39 seconds, identifying the control-plane Git fixture harness as the dominant observed hotspot;
- production-shaped Git boundary coverage must remain real where Git ancestry, refs, remotes, transport, publication, or race semantics are under test; optimization must not achieve speed by adding skips/xfails, deselecting canonical coverage, broadly mocking Git, weakening fail-closed gates, or merely increasing timeouts;
- common immutable Git baselines may be reused only when every test retains isolated mutable worktrees/remotes/refs;
- serial harness acceleration must be measured before parallel execution is accepted, and any worker count must be chosen from deterministic self-host measurements rather than an automatic heuristic;
- long verification must be isolated from unrelated mutable control-checkout movement so valid candidate verification is not discarded merely because an independent planning/control mutation advances the shared checkout.

The verification foundation is therefore a prerequisite before BP-3 begins, after BP-1 correctness and BP-2 attribution hardening.

### BP-V1 — Verification Contract Hardening

Enforce minimum-sufficient canonical verification without moving semantic judgment into Runtime. Repository-owned deterministic policy must reject or normalize known redundant verification coverage such as exact duplicates and known subset/full-suite subsumption on unchanged relevant state, while preserving justified full-suite execution.

Exit gate: TASK/correction verification contracts cannot silently require known redundant proof on the same unchanged subject; full-suite use has an explicit bounded reason; Runtime remains deterministic and evidence-preserving.

### BP-V2 — Full-Suite Harness Acceleration

Reduce intrinsic full-suite wall time without reducing the canonical test population or weakening production-shaped Git semantics. Profile the exact Windows self-host baseline, eliminate repeated construction of equivalent Git baselines, separate pure semantic tests from Git-boundary integration tests where authority permits, and preserve isolated mutable repositories/remotes/refs per test.

Exit gate: before/after evidence on the same self-host class proves the same canonical semantic coverage with no new skip/xfail/deselect behavior, and one bounded serial-harness saturation pass has exhausted the clearly safe setup-only optimization surface while preserving production-shaped real-Git boundaries. Reviewer owns the saturation judgment from canonical evidence. The historical 50% / 1170.145 second estimate from the earlier audit and TASK-154 r1 remains useful planning context but is not a mandatory completion threshold and must not be pursued by weakening semantics or by unbounded optimization.

### BP-V3 — Verification Workspace Isolation

Decouple candidate verification from unrelated movement of the mutable control checkout while preserving exact candidate SHA, clean-state, provenance, Runtime evidence ownership, and fail-closed mutation detection inside the verification subject.

Exit gate: an unrelated authorized planning/control-main advance cannot invalidate an otherwise unchanged verification subject; mutation of the actual verification subject still fails closed.

### BP-V4 — Parallel Verification and Performance Guard

After serial fixture isolation/acceleration is proven, establish bounded parallel full-suite conformance and verification-cost observability. Worker count must be selected from deterministic measurements on the supported self-host class, and performance telemetry must surface material regression without becoming semantic completion authority.

Exit gate: parallel execution preserves the same canonical test population and deterministic evidence semantics, is free of cross-test mutable-state sharing, and demonstrates a measured improvement over the optimized serial baseline.

### BP-3 — Brain Context Foundation

Add transient Work Context projection, thin Flow Resolver, and repository-owned Flow Cards by composing existing Brain Sync, Unified State, roadmap and canonical lineage.

Exit gate: a fresh Brain can identify the correct semantic flow and required context without chat memory, with no new planning/lifecycle authority.

### BP-4 — Decision Packet Compiler

Compile minimum-sufficient deterministic packets for TASK authoring, review, correction, diagnostics, and architecture decisions as justified by evidence.

Exit gate: packets are bounded, deterministic, provenance-bound, and do not duplicate semantic judgment.

### BP-4A — Brain Semantic Audit Protocol

Establish a bounded provider-neutral two-stage semantic procedure for high-value Brain-owned decisions:

```text
STAGE 1 — CONSTRUCT
  -> bounded semantic candidate + construct_fingerprint

STAGE 2 — ADVERSARIAL_AUDIT_AND_RECONCILE
  -> fixed-lens audit of the construct candidate
  -> bounded transient risk/coverage claims
  -> reconciled candidate
  -> mandatory final closure sweep on that reconciled candidate
  -> CANDIDATE or NO_DECISION
```

Both stages bind the same AIOS_DECISION_PACKET fingerprint and the same immutable repository-owned audit-profile content identity. Stage 2 additionally binds the exact construct_fingerprint. A changed/stale packet, changed profile, or substituted construct invalidates the transient state and requires a fresh Stage 1 rather than merging reasoning across canonical subjects.

BP-4A defines semantic stages and pure structural contracts only. Provider/model invocation belongs to BP-5, so BP-4A must not claim or implement an exactly-two-provider-call runtime. The reproducible property is the procedure, bounded inputs, required lenses, identity binding, and closure contract—not identical model reasoning or identical semantic output.

The audit profile is content-addressed procedural policy, not a provider prompt or semantic answer key. V1 uses one fixed high-value profile, deterministically applicable to ARCHITECTURE, TASK_AUTHORING, REMEDIATION_AUTHORING and REPAIR_AUTHORING. DIAGNOSTIC remains outside the mandatory two-stage protocol; SEMANTIC_REVIEW remains Reviewer-owned and outside BP-4A. A future BP-5 AIOS_BRAIN_REQUEST may carry normalized audit-profile material plus an audit_profile_ref containing id, version and content digest.

Risk/coverage material is transient Brain semantic claim output, not Runtime EVIDENCE, REVIEW findings, Correction Frontier state, model memory, a conversation store, or canonical lifecycle truth. It may not rank risks, vote, select corrections, choose an Executor, retry, route lifecycle work, or persist reasoning. The deterministic validator may prove only structure, bounds, exact lens coverage and identity binding; it may not prove that a Brain semantic claim is substantively correct.

Stage 2 must include a final closure sweep over the reconciled candidate itself so a change made while addressing a construct risk is not emitted without adversarial closure. Any closure blocker yields protocol-local NO_DECISION and no handoff-ready candidate. NO_DECISION is not a canonical BLOCKED/FAILURE/REVIEW verdict and must not trigger retry automatically.

Planning evidence and the lineage-based proxy retrospective over TASK-164..TASK-178 are recorded in docs/AIOS-BRAIN-SEMANTIC-AUDIT-PROTOCOL.md. That retrospective motivates the lens set but is not a causal proof that two stages reduce future REVIEW/REPAIR incidence.

Exit gate: one immutable Decision Packet fingerprint and one immutable audit-profile digest are bound across exactly two semantic stages; Stage 2 binds the exact construct fingerprint, performs fixed-lens adversarial audit, reconciliation and final closure sweep, and emits only CANDIDATE or NO_DECISION; all construct/ledger/closure state is transient and non-canonical; the protocol introduces no new semantic/lifecycle/review/publication authority, persistent reasoning store, voting, correction selection, retry/fallback, Executor selection or provider-specific behavior; provider-neutral structural conformance is ready for BP-5 to bind by normalized audit_profile_ref + profile material.

### BP-5 — Brain Provider Protocol

Introduce provider-neutral `AIOS_BRAIN_REQUEST` / `AIOS_BRAIN_DECISION` validation and an adapter boundary that does not require provider-specific GitHub access.

Exit gate: at least two provider implementations can consume/produce the same Brain contract without changing Brain authority semantics.

### BP-6 — Reviewer Provider Protocol

Introduce separate provider-neutral review request/decision contracts while preserving Reviewer independence from Brain authority.

Exit gate: semantic review can be performed through the same bounded canonical evidence regardless of provider implementation.

### BP-7 — Cross-context / Hot-swap Conformance

Prove deterministic continuation when Brain/Reviewer provider changes between authoring, execution, review, remediation, repair, and publication checkpoints.

Exit gate: conformance scenarios require no previous model memory.

### BP-8 — Real Second-provider Proof

Run one controlled real workflow using a non-default Brain and/or Reviewer provider through the provider-neutral contracts while keeping existing GitHub/self-host execution semantics.

Exit gate: observed evidence proves provider replacement rather than only mocked interface compatibility.

### BP-9 — Explicit Downstream Adoption

Only after BP-8 PASS, offer the capability through an explicit reviewed downstream migration/profile bound to that downstream repository's exact AIOS dependency pin and repository-owned configuration.

Exit gate: no downstream repository is silently retargeted to mutable AIOS-renew `main`.

## 16. Dependency order

```text
BP-0 Architecture Baseline
  -> BP-1 PRIMARY Exact Intent Binding v2
  -> BP-2 Operational Attribution v2
  -> BP-V1 Verification Contract Hardening
  -> BP-V2 Full-Suite Harness Acceleration
  -> BP-V3 Verification Workspace Isolation
  -> BP-V4 Parallel Verification and Performance Guard
  -> BP-3 Brain Context Foundation
  -> BP-4 Decision Packet Compiler
  -> BP-4A Brain Semantic Audit Protocol
  -> BP-5 Brain Provider Protocol
  -> BP-6 Reviewer Provider Protocol
  -> BP-7 Hot-swap Conformance
  -> BP-8 Real Second-provider Proof
  -> BP-9 Downstream Adoption
```

Do not collapse the sequence into one mega-TASK. Each implementation milestone must receive a separate executor-neutral TASK after fresh Brain Sync and authority/overlap audit.

## 17. Hot-swap conformance baseline

The final architecture must prove at least:

1. Brain A authors a TASK, disappears, Brain B fresh-syncs and reviews the eventual result without prior chat.
2. Brain A creates CHANGES_REQUIRED; Brain B authors correction from exact canonical finding only.
3. Brain A authorizes REPAIR; Brain B resumes from exact current authorization/failure lineage.
4. Current Human priority change invalidates stale prior context and cannot be overwritten by an older provider decision.
5. PRIMARY authorized for one TASK revision cannot execute a later semantic revision under the old authorization.
6. Unrelated `main` movement does not invalidate an unchanged exact authorized TASK.
7. Self-host failure before AIOS invocation produces a typed operational failure, not a fabricated RUN failure.
8. Provider failure after useful candidate work is recovered from canonical candidate/failure evidence rather than blind re-execution.
9. One provider may act as Brain and a different provider as Reviewer without merging authorities.
10. A provider lacking a GitHub connector can still participate through structured request/decision contracts while GitHub Actions and self-host remain active.

Success criterion: no conformance case requires knowledge of what a previous model remembered in chat.

## 18. Migration and compatibility

The architecture is additive and versioned above frozen Kernel v0.1 wherever possible.

- Do not rewrite frozen specifications or historical artifacts to pretend they were provider-neutral when they were not.
- Preserve historical v1 carrier/dispatch artifacts as immutable evidence.
- Version new contracts where semantics strengthen identity or observability.
- Reuse existing authoring ingress, correction, publication, Runtime, Executor, and terminal-attention authorities rather than duplicating them.
- Any actual change to frozen Kernel semantics requires separate explicit Human authority and a separate TASK.
- Downstream repositories remain governed by their exact pinned AIOS dependency until explicit reviewed migration.

## 19. BP-0 decision

BP-0 is Human-approved as the planning baseline. This approval authorizes canonical roadmap planning for the Brain Portability track. It does not itself implement BP-1 through BP-9, does not claim any engineering milestone complete, does not authorize Executor mutation outside future TASK contracts, and does not alter frozen Kernel v0.1 semantics.


## 20. TA-0 / TA-1 decision

TA-0 (Verification Architecture Audit) and TA-1 (Full-Suite Performance Audit) are Human-approved planning inputs as of 2026-09-20. Their approval inserts BP-V1 through BP-V4 between BP-2 and BP-3. BP-1 / TASK-130 remains the unique current NEXT implementation milestone; no BP-V implementation TASK is authored by this planning decision.

The prior Performance Closure lineage remains immutable historical engineering truth. This decision records a prospective closure-completeness follow-up: test-surface decomposition and performance observation were useful, but current observed evidence requires additional deterministic verification-contract enforcement, intrinsic full-suite harness acceleration, verification-workspace isolation, and measured parallel/performance hardening before provider-driven zero-touch work proceeds.
