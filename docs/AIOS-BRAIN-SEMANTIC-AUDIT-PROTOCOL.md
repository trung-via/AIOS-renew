# AIOS Brain Semantic Audit Protocol — Planning Evidence

Status: HUMAN/BRAIN PLANNING BASELINE
Canonical evidence cut: main 63dce92adb271ea78d04bcd151df91a8ed260ff7
Scope: read-only retrospective plus BP-4A architecture intent; no implementation authority

## 1. Purpose

BP-4A exists to make high-value Brain decisions reproducibly execute a bounded adversarial second-audit procedure without depending on chat memory or on a Human remembering to request "audit kỹ hơn".

The reproducible property is the protocol: bounded inputs, fixed audit lenses, identity binding, coverage requirements, reconciliation shape and closure behavior. BP-4A does not promise deterministic model intelligence, identical reasoning, or identical semantic output across providers.

The refined protocol is:

```text
AIOS_DECISION_PACKET v1
        +
AIOS_BRAIN_AUDIT_PROFILE v1
        │
        ▼
STAGE 1 — CONSTRUCT
        │
        ├── construct_candidate
        └── construct_fingerprint
        │
        ▼
STAGE 2 — ADVERSARIAL_AUDIT_AND_RECONCILE
        │
        ├── audit Stage-1 construct candidate
        ├── bounded transient risk/coverage claims
        ├── reconcile candidate
        ├── final closure sweep on reconciled candidate
        │
        └── CANDIDATE | NO_DECISION
```

There is no third semantic stage and no recursive "audit until satisfied" loop.

This is cognitive support inside one Brain authority. It is not a second Brain, Reviewer, Planner, voting system, lifecycle router, correction selector, retry/failover controller, model router, persistent reasoning store, conversation store, or source of engineering truth.

## 2. Retrospective evidence basis

The retrospective sampled TASK-164 through TASK-178 inclusive: 15 recent canonical TASKs immediately preceding BP-4 closure.

Observed canonical lineage shape:

- 6 of 15 TASKs received semantic TASK revisions before closure: TASK-164, TASK-167, TASK-168, TASK-174, TASK-177, and TASK-178.
- 8 of 15 TASKs have at least one REPAIR lineage in their history: TASK-165, TASK-166, TASK-167, TASK-168, TASK-172, TASK-173, TASK-174, and TASK-177.
- 5 of 15 TASKs reached reviewed/published closure in revision 1 without REPAIR lineage: TASK-169, TASK-170, TASK-171, TASK-175, and TASK-176.
- TASK-178 r2 exposed a separate semantic-review correction class: RUN-178-001 through RUN-178-009 were reviewed before final publication, with eight canonical REMEDIATION authorizations in the lineage.

This is a lineage-based proxy retrospective, not a paired causal experiment. Canonical repository history does not contain a first-audit/second-audit artifact pair for every TASK, task difficulty differs, and some deeper Brain work happened before the first canonical TASK revision. The retrospective therefore motivates the fixed lens set and prospective evaluation; it does not prove that a two-stage protocol causally reduces future failures.

## 3. Lens families supported by recent lineage

Recent revision/review deltas repeatedly fall into eight useful audit families:

1. AUTHORITY_BOUNDARY
   - Executor vs Runtime verification/completion ownership in TASK-167.
   - Human request vs canonical facts and Brain/Reviewer ownership in TASK-177.
   - deterministic correction selection and duplicate semantic-enum authority exposed in TASK-178.

2. SCOPE_NON_GOALS
   - TASK-164 r2 preserved test-only scope rather than widening production behavior.
   - TASK-168 r2 retained useful bounded candidate work while refusing production Correction Integration mutation.
   - TASK-174 r2 bounded structural-completion changes to the intended Executor contract.

3. PROVENANCE_LINEAGE
   - TASK-178 r2 added exact remediation authorization provenance and later review tightened RUN/TASK/failure cross-binding.
   - TASK-164 r2 preserved failed lineage rather than reclassifying an old RUN.

4. FAILURE_MODE_COUNTEREXAMPLES
   - TASK-167 found acceptance/verification ordering contradiction and later unnecessary Executor-shell dependency.
   - TASK-168 found the same ordering class plus known diagnostic-family gaps.
   - TASK-178 reviews found malformed Runtime-shaped FAILURE, type-strict identity and unbound-observation cases.

5. AC_CONSISTENCY_COMPLETENESS
   - TASK-167, TASK-168, TASK-174, TASK-177 and TASK-178 materially strengthened acceptance conditions after deeper audit.

6. VERIFICATION_OWNERSHIP_ORDERING
   - TASK-167/TASK-168 separated pre-verification structural claims from Runtime-owned verification.
   - TASK-174 hardened truthful completion semantics instead of laundering downstream verification into Executor claims.

7. PORTABILITY_PRIVACY_BOUNDEDNESS
   - TASK-177 bounded Human input and prohibited persistent context truth.
   - TASK-178 enforced provider-neutral packet bounds, minimum necessary correction context and machine-local exclusions.

8. SIMPLIFICATION_DUPLICATE_AUTHORITY
   - TASK-167 r3 removed redundant native Executor repository inspection.
   - FINDING-178-004 removed full RESULT/REVIEW replication per correction-frontier item.
   - FINDING-178-009 removed a duplicate semantic action contract from Decision Packet support.

The observed value comes from explicit adversarial lenses and counterexample search, not from merely increasing model effort or token budget.

## 4. Two-stage semantic contract

### Stage 1 — CONSTRUCT

Stage 1 consumes one exact AIOS_DECISION_PACKET v1 plus one exact audit-profile content identity and returns one bounded construct candidate.

Stage 1 binds at minimum:

```text
packet_fingerprint
audit_profile_ref.id
audit_profile_ref.version
audit_profile_ref.digest
selected_flow
expected_return_shape
normalized construct_candidate
```

The pure protocol layer derives construct_fingerprint from the normalized semantic material. Provider/model/session/timestamp/hostname identity must not participate in that semantic fingerprint.

### Stage 2 — ADVERSARIAL_AUDIT_AND_RECONCILE

Stage 2 consumes:

```text
the same Decision Packet
the same normalized audit-profile material
the same audit_profile_ref
the exact Stage-1 construct_candidate
the exact construct_fingerprint
```

It performs three activities inside one semantic stage:

1. audit the Stage-1 construct candidate with every required profile lens;
2. reconcile all construct risks into a candidate or retain a protocol-local blocker;
3. perform a mandatory final closure sweep over the reconciled candidate itself.

The final closure sweep is mandatory because reconciliation may introduce a defect that did not exist in the Stage-1 candidate. Without closure over the reconciled candidate, a two-stage protocol could reproduce the same "fix one defect, introduce another" pattern it is intended to reduce.

Stage 2 emits exactly one of:

```text
CANDIDATE
NO_DECISION
```

NO_DECISION is protocol-local semantic output only. It is not canonical BLOCKED, FAILURE, REVIEW, REMEDIATION, REPAIR, next_action, or roadmap state, and it must not trigger automatic retry.

## 5. Freshness and invalidation

Using the same packet_fingerprint proves that two stages refer to the same packet bytes; it does not by itself prove the packet is still current canonical context.

BP-4A therefore does not perform Git/GitHub/Brain-Sync discovery. Before Stage 2, the future caller/provider protocol must freshly compose or revalidate the Decision Packet through the existing BP-3/BP-4 path.

If the newly valid packet fingerprint differs from Stage 1:

```text
discard Stage-1 transient state
restart from Stage 1
```

Do not merge or reconcile reasoning across different canonical subjects.

This freshness orchestration belongs to the future provider request layer in BP-5. BP-4A owns only pure exact-binding validation.

## 6. Repository-owned audit profile

V1 should use one fixed profile: brain-high-value-v1.

The profile is procedural semantic policy, not provider-specific prompt text and not an answer key. It defines:

- profile id and version;
- exact applicable Brain-owned flows;
- required ordered lens set;
- bounded structural output contract;
- reconciliation and final-closure requirements;
- normalization and byte/privacy limits.

Its identity is content-addressed:

```yaml
audit_profile_ref:
  id: brain-high-value-v1
  version: 1
  digest: <sha256-of-normalized-profile-content>
```

A ref alone is not enough for a provider without repository access. BP-5 must carry normalized audit-profile material together with audit_profile_ref and recompute the digest before use.

The BP-4A published profile is structurally sufficient for the completed BP-4A exit gate, but its current provider-facing lens material consists of stable lens identifiers rather than bounded normative lens descriptions. A fresh provider with no repository or chat context therefore cannot be expected to reconstruct the intended audit method from the identifiers alone. Before BP-5 invokes a replaceable Brain provider, the repository-owned profile must be prospectively extended so each existing lens id carries bounded self-sufficient procedural meaning. That extension changes the content digest and must be reviewed as a new implementation mutation; it does not reopen or reinterpret historical BP-4A completion.

The profile remains the single semantic authority for audit-lens procedure. Provider protocol code and adapters must not hard-code a second copy of lens meanings or add provider-specific semantic instructions. The provider-visible profile should describe only bounded adversarial checks, not copy the Constitution, store reasoning, or prescribe a semantic answer.

Line-ending, checkout path, hostname, provider or session changes must not alter the profile digest. Any semantic profile-content change, including a required lens, lens description or procedural requirement, must change the digest.

Lens order is part of procedural profile content and therefore part of the digest. Ordered execution must not imply risk priority or ranking.

## 7. Deterministic applicability

BP-4A v1 applies the single fixed profile deterministically to exactly these Brain-owned flows:

```text
ARCHITECTURE
TASK_AUTHORING
REMEDIATION_AUTHORING
REPAIR_AUTHORING
```

DIAGNOSTIC is not mandatory two-stage work in v1. If a diagnostic result leads to a high-value authoring decision, that later authoring decision uses BP-4A.

SEMANTIC_REVIEW is Reviewer-owned and remains outside BP-4A. An analogous Reviewer audit protocol, if justified, belongs to separate BP-6 reasoning.

Do not let a model assign risk/complexity scores or select among audit profiles. V1 contains no semantic profile router.

## 8. Risk/Coverage Ledger semantics

"Risk/Coverage Ledger" is a Human-facing name for transient Brain semantic claims.

It is not:

- Runtime EVIDENCE;
- canonical facts;
- REVIEW findings;
- Correction Frontier state;
- lifecycle state;
- a planning bookmark;
- model memory;
- a reasoning database;
- a conversation log.

The deterministic protocol validator may prove only:

- every required lens appears exactly once;
- the structural schema is valid;
- the output is bounded and privacy-safe;
- identity/fingerprint bindings are exact;
- closure requirements are structurally satisfied.

It must not claim that CLEAR is objectively true or that a semantic risk assessment is correct.

For the Stage-1 construct audit, each required lens has:

```text
CLEAR
RISK_FOUND
```

If RISK_FOUND, bounded material may include:

```text
risk_summary
counterexample
candidate_anchor
disposition:
  ADDRESSED_BY_RECONCILIATION
  DISMISSED_WITH_BOUNDED_BASIS
```

There is no severity score, ranking, vote, finding id or correction-frontier identity.

All eight lenses are required for every applicable v1 flow; the provider cannot self-declare NOT_APPLICABLE to skip a lens.

If a risk is marked ADDRESSED_BY_RECONCILIATION while the normalized candidate is unchanged, the pure structural layer may fail closed as self-contradictory.

## 9. Final closure sweep

After reconciliation, Stage 2 reruns the exact required profile lens set against the reconciled candidate.

Closure output for each required lens is only:

```text
CLEAR
BLOCKER
```

If every lens closes CLEAR, Stage 2 may emit CANDIDATE.

If any lens closes BLOCKER, Stage 2 emits NO_DECISION and no handoff-ready final candidate.

The deterministic validator checks complete exact lens coverage and structural consistency. It does not independently decide whether a semantic CLEAR/BLOCKER judgment is substantively correct.

## 10. Handoff and authority boundary

BP-4A validates audit-protocol structure only. It must not duplicate downstream semantic validators.

A final TASK_AUTHORING candidate still goes through the existing TASK validation and authoring ingress.

A final REMEDIATION_AUTHORING candidate still goes through the existing remediation validator/ingress.

A final REPAIR_AUTHORING candidate still goes through the existing repair-authorization validator/ingress.

ARCHITECTURE remains Human/Brain planning authority and requires explicit canonicalization through the existing planning mutation process.

Do not add a seventh Flow Resolver flow called SEMANTIC_AUDIT. BP-4A is a procedure layered over already-selected Brain-owned flows.

Do not modify Unified State, Correction Frontier, Runtime, Reviewer, Publisher, Flow Resolver, Flow Cards or provider adapters merely to implement the BP-4A pure protocol.

## 11. Provider boundary

BP-4A defines two semantic stages; it does not invoke a provider/model.

Exactly-two-provider-invocation orchestration for a successful audited semantic attempt belongs to BP-5 AIOS_BRAIN_REQUEST/AIOS_BRAIN_DECISION work. A failed transport call, malformed response or stale pre-Stage-2 packet is not a completed semantic stage, does not fabricate NO_DECISION or canonical FAILURE, and must not trigger automatic retry/failover.

A future provider adapter may execute Stage 1 with one provider implementation and Stage 2 with another while retaining one Brain semantic authority, provided the same packet/profile/construct bindings are honored. Such provider replacement is not voting and does not create a second semantic authority. BP-5 need only preserve this compatibility; cross-checkpoint hot-swap conformance remains BP-7.

BP-4A conformance must therefore avoid provider/model/session identities in semantic output and fingerprints.

"No hidden extra context" means AIOS does not deliberately provide chat history, repository discovery, raw logs, machine-local state, or other unbounded material outside the bounded protocol payload. It is not a claim that an external model has no pretraining, system instructions or implementation-internal context.

## 12. Bounds and privacy

The implementation TASK must choose explicit byte/count bounds for:

- profile material;
- construct candidate;
- per-lens risk text;
- counterexample text;
- candidate anchors;
- Stage-2 aggregate response.

Invalid Unicode, NaN/non-JSON values, duplicate/unknown fields and over-bound payloads must fail closed without truncation.

The protocol must exclude chat history, chain-of-thought, raw logs, credentials, local paths/workspaces/remotes, hostnames, timestamps, random ids and provider/session/model metadata from semantic material.

## 13. Measurement

BP-4A itself does not create a telemetry database or persist Risk/Coverage Ledgers.

The current retrospective remains a planning baseline. Future effectiveness may be assessed from ordinary canonical engineering lineage such as:

- post-authoring TASK revision incidence;
- admitted RUN failure / REPAIR incidence;
- REVIEW CHANGES_REQUIRED / REMEDIATION incidence.

Detailed historical metrics such as "which lens caught which risk" require persistence and are therefore deferred. If future evidence justifies them, they require a separate bounded telemetry TASK that stores aggregate non-reasoning data only and does not become lifecycle or semantic authority.

No metric target becomes completion truth or automatic roadmap authority.

## 14. Recommended implementation boundary

The preferred implementation surface for the successor TASK is:

```text
.ai/brain-audit-profiles.yaml
src/aios_renew/brain_audit.py
tests/test_brain_audit.py
```

The implementation should remain pure parsing, normalization, content-addressed identity, fingerprinting and structural validation.

It should not require changes to:

```text
src/aios_renew/decision_packet.py
src/aios_renew/brain_context.py
.ai/flow-cards.yaml
src/aios_renew/unified_state.py
src/aios_renew/review.py
src/aios_renew/publication.py
src/aios_renew/runtime.py
src/aios_renew/operator.py
src/aios_renew/authoring_ingress.py
provider adapters
```

If satisfying the semantic contract requires widening into those authority surfaces, return the conflict to Brain rather than silently widening scope.

## 15. Conformance focus for the successor TASK

Verification should prove at least:

- one fixed v1 profile with exact deterministic applicability;
- stable profile digest across equivalent semantic content / line-ending representation;
- digest change for any material profile change;
- exact packet/profile/construct cross-binding;
- Stage-2 rejection of packet/profile/construct substitution;
- every required lens exactly once; missing/duplicate/unknown lens fails closed;
- no provider-controlled NOT_APPLICABLE escape;
- closure sweep covers the reconciled candidate, not only the construct candidate;
- any closure BLOCKER yields NO_DECISION and no handoff-ready candidate;
- no recursive stage/audit nesting;
- no provider/model/session identity in semantic fingerprints;
- no persistence, Git/GitHub/network discovery, Executor invocation, canonical verification, lifecycle mutation or publication action;
- no duplicated TASK/REMEDIATION/REPAIR validators;
- privacy/UTF-8/NaN/byte-bound fail-closed behavior.

## 16. BP-4A exit gate

BP-4A is complete only when reviewed/published evidence proves:

- one exact Decision Packet fingerprint across both semantic stages;
- one exact content-addressed audit-profile identity across both stages;
- Stage 2 binds the exact Stage-1 construct fingerprint;
- exactly two bounded semantic stages with no recursive audit;
- fixed repository-owned v1 lens coverage;
- transient construct candidate and Risk/Coverage Ledger claims;
- adversarial reconciliation plus mandatory closure sweep over the reconciled candidate;
- CANDIDATE or protocol-local NO_DECISION output only;
- no new semantic/lifecycle/review/publication authority;
- no persistent reasoning/conversation store;
- no multi-agent voting or consensus;
- no automatic lifecycle routing, correction selection, retry, fallback or Executor selection;
- provider-neutral pure protocol behavior;
- readiness for BP-5 to carry normalized audit-profile material plus immutable audit_profile_ref without changing Brain authority.

## 17. Planning decision

BP-4 is engineering-complete through TASK-178 r2, RUN-178-009, REVIEW-178-009 PASS, and publication of 157732551f0a549b9644688a48ddc9c7c730c96d.

BP-4A is engineering-complete through TASK-179 r1. RUN-179-001 produced the initial candidate and REVIEW-179-001 identified FINDING-179-001; the exact remediation was authorized at 343e9f34cda5e08cab5ce45dec88732da664160a. RUN-179-002 corrected that finding, REVIEW-179-002 returned DELTA PASS, and the reviewed candidate 1c324165f93285e8179f9b0d87ab9f1e9049aa6a was published to canonical main.

The reviewed/published implementation satisfies the BP-4A exit gate as a pure structural protocol: one content-addressed repository audit profile, Stage-1 construct binding, Stage-2 exact packet/profile/construct binding, fixed ordered lens coverage, bounded transient risk/coverage claims, reconciliation consistency, mandatory final closure, CANDIDATE/NO_DECISION outcomes, and no provider invocation, persistence, lifecycle routing, Reviewer/Publisher authority, retry/fallback, voting, or Executor selection.

BP-5 Brain Provider Protocol is the next Human/Brain planning milestone. BP-4A completion does not itself authorize provider invocation or define AIOS_BRAIN_REQUEST / AIOS_BRAIN_DECISION transport; those boundaries must be separately audited and canonically authored under BP-5.
