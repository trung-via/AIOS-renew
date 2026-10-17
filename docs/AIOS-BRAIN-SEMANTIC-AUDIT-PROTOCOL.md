# AIOS Brain Semantic Audit Protocol — Planning Evidence

Status: HISTORICAL BP-4A HUMAN/BRAIN PLANNING BASELINE; prospective V1 in section 18, isolated ROA support in section 19
Canonical evidence cut: main 63dce92adb271ea78d04bcd151df91a8ed260ff7
Scope: read-only retrospective plus BP-4A architecture intent; no implementation authority

Sections 1–17 preserve the original BP-4A planning and completion claims in their
historical context. Their `v1` / `brain-high-value-v1` refers to that original
protocol, not `AIOS_ADAPTIVE_BRAIN_AUDIT_V1`. The sole active prospective
profile is `aios-adaptive-brain-audit-v1`, version 1, normalized digest
`982ba27964c78e7a7f79f5f268afb32eed27605be90b0642e4001b2edf116718`, under
the explicit Human/Brain cutover in section 18.6. V2/V3 remain historical and
exact in-flight compatibility material; registry order does not select a profile.

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

## 18. Prospective adaptive V1 successor — TASK-340

`AIOS_ADAPTIVE_BRAIN_AUDIT_V1` is the bounded successor described by the
Human-approved A+ design in [AIOS Adaptive Brain Audit v1](AIOS-ADAPTIVE-BRAIN-AUDIT-v1.md).
TASK-340 delivered exact structural conformance and comparison capability before
the separately authorized prospective cutover in section 18.6. V1 is now the sole
prospective profile; V2/V3 in-flight and historical subjects retain their own exact
identities and meanings. Capability, comparison output and source changes alone
do not advance the roadmap or establish engineering completion.

### 18.1. One portable content-bound procedure

The normalized profile lives in `.ai/brain-audit-profiles.yaml`:

```yaml
audit_profile_ref:
  id: aios-adaptive-brain-audit-v1
  version: 1
  digest: 982ba27964c78e7a7f79f5f268afb32eed27605be90b0642e4001b2edf116718
```

All adaptive semantic instructions are self-contained in the existing eight
ordered `lenses[].check` fields. Their base meanings, four applicable flows,
two stages and closed bounds remain intact: 2048 UTF-8 bytes per check, 131072
candidate bytes, four risks per lens, 524288 Stage-2 bytes and depth 32, with the
existing text limits and TASK conformance policy. The registry remains bounded
to 32768 bytes (section 19 extends only the original three-profile count cap). Registry order is
irrelevant to selection: the exact canonical Human/Brain V1 ID/version/digest
selects the single prospective package. V3 appearing first confers no authority.

The validator recognizes one exact normalized V1 digest. Equivalent YAML line
endings and JSON member order preserve that identity. Changed or partially
adapted V1 content, V3 material renamed V1, unknown IDs/versions, substituted
conformance policy and over-bound material fail closed even if a caller rehashes
the supplied package. A future semantic revision requires separately authorized
profile identity support. The digest allowlist contains no second copy of lens
meanings; those meanings travel with the normalized profile package.

V2/V3 profile bodies, normalized digests and schemas are preserved. Exact V2/V3
historical packages remain valid in their own contexts; there is no fallback,
profile substitution or reminting of old evidence. Stage 1 and Stage 2 must share
the exact packet, subject, profile reference and construct fingerprint. A changed
profile or newly composed different packet cannot reconcile an earlier construct.
Pure validation detects identity mismatch; it does not discover canonical Git
currentness. The existing caller currentness/expected-main/CAS boundaries remain
responsible for supplying fresh context and invalidating a moved subject.

### 18.2. Mandatory lenses, impact-dependent depth

Every applicable subject uses `CONSTRUCT` then
`ADVERSARIAL_AUDIT_AND_RECONCILE`, all eight ordered checks, reconciliation and
final closure over the reconciled candidate. Focus adjusts semantic depth; it
never permits a lens `NOT_APPLICABLE`, a third stage or a new audit authority.
The profile carries these combined impact questions:

| Focus | Material impact and additional depth |
|---|---|
| F-A ARCHITECTURE_NORTH_STAR | Useful outcome, concrete necessity, A reuse/no-change, B minimum legal change, C new mechanism, dependency spirals and total episode/adoption/operations cost. |
| F-B CONSTITUTION_KERNEL | Human authority, prospective amendments, frozen Kernel, legacy decoding and non-retroactivity. |
| F-C PROOF_EVIDENCE | Exact applicability and original evidence subjects, complete obligations, INVALIDATED/UNKNOWN, no false PASS or duplicate lawful execution. |
| F-D SECURITY_TRUST | Approved threat model, actor, trust root, issuer/origin, isolated privileges, replay/forgery, private raw and actual live deployment. |
| F-E WORKFLOW_INTEGRATION | Affected consumers, transitions, races, currentness/CAS, idempotence, delegation and transport versus terminal truth. |
| F-F CORRECTION_DEFECT | Exact causal finding and immutable lineage, minimum legal supporting delta, negative regression, sticky Executor and valid-proof preservation. |
| F-G PERFORMANCE_EFFICIENCY | Comparable frozen baseline, per-stage and whole-episode time/tokens/test work/Human effort; observed, measured, estimated and unknown distinctions. |
| F-H RECOVERY_RETIREMENT | Exact authorized baseline, forward recovery, obsolete-hook removal, privacy hygiene, reversibility and independent downstream adoption. |

Strategic subjects require program necessity before decomposition. Ordinary TASKs
reuse still-valid settled architecture while challenging the exact new delta and
affected consumers. A bounded correction with sufficient canonical cause retains
two concise eight-lens stages without a default Research Audit or full architecture
study. A new issuer/service/proof boundary inside a nominal local fix requires the
union of its actual impacts. Research remains optional bounded read-only support
where uncertainty needs it; it does not become a stage, voter or local-fix gate.

Stage 2 independently reconstructs impacts from the exact packet, normalized
profile and Stage-1 bound candidate with valid canonical references. It must
challenge omitted or mislabeled architecture, governance, proof, security,
integration, correction, performance and recovery effects even if transient
Stage-1 notes mention only F-F. Unbound notes cannot certify coverage or provide
decisive assumptions. The closed envelopes accept no focus-note/selector fields;
V1 also excludes those reserved controls from candidates. Decisive permitted
semantic context belongs in bound inputs. Missing decisive context or authority
requires a local blocker/`NO_DECISION`, or a fresh authorized Stage-1 attempt;
identity mismatch is rejected rather than reconciled across subjects.

Focus risks use the existing bounded risk/counterexample/anchor and disposition
fields, followed by all eight closure checks on the reconciled candidate. There
is no focus router, numeric classifier, persistent ledger or provider-specific
semantic policy. Structural validation checks bindings, shape, coverage, privacy
and closure consistency; it cannot establish that a provider actually reasoned
independently or that a declared semantic `CLEAR` is true.

### 18.3. V3 protection and authority parity

V1 enables V3 candidate privacy/reserved-key exclusions and TASK conformance by
exact profile identity despite its numeric version being 1. All three bounded
`cross_authority_context`, `canonical_shape` and `terminal_lifecycle` sections
remain required for TASK Stage 2, with exact packet/reconciled-candidate bindings,
shape/path coverage and blocked-section closure requirements. Missing, swapped,
nested or private sections and machine-local paths are rejected. Provider request,
response and serialized decision validation carry and revalidate the same policy.
V2 retains its historical grammar without those new requirements.

TASK proof-phase declarations retain the bounded existing ledger. Brain must
reconcile every `PROOF_LATER` criterion out of final acceptance; final acceptance
coverage is exactly once and all `CLAIM_NOW`. Structural protocol validation
continues to normalize declarations and bind their identity; it does not infer
truth from prose or add a completion/ingress phase gate. `CANDIDATE`/`NO_DECISION`
remain local Brain outputs, never Runtime evidence, independent REVIEW or lifecycle
state. Serialized `NO_DECISION` replay retains the existing recoverability limit:
when a hidden reconciled candidate differs from the original construct, it cannot
be recovered from that request alone and revalidation fails closed.

AUTHOR_TASK remains direct final contract ingress. This successor adds no required
audit envelope, Decision Packet, transcript, Stage-1/Stage-2 artifact, conformance
section or acceptance ledger to TASK admission. Current identity/revision,
provenance, affinity, expected-main currentness and expected-old-main CAS remain
mandatory under existing contracts. Human intent/risk/delegation, Brain WHAT/WHY,
Executor HOW, Runtime verification/evidence, independent Reviewer judgment and
Publisher exact eligible-source ownership are unchanged. Frozen Kernel, KA-01,
correction ingress, roadmap and downstream pins receive no new authority.

### 18.4. Exportable paired comparison basis and measurement limits

`tests.test_brain_audit.export_comparison_basis()` returns a bounded, JSON-portable
comparison basis. It embeds preselected TASK-320 r1, TASK-329 r1, TASK-334 r2 and
TASK-336 r1 projections from frozen commit
`35e3fe6b382cf5e0a4f743c7dee18ae94403b7c3`, with source paths, original-byte SHA-256
digests and explicit projection fields. These projections are relevant bounded
authoring material, not complete ingress contracts or production replay. Historical
hindsight is disclosed. Six explicitly modeled ordinary/correction/adversarial
cases add local repair, omitted issuer/service/integration impacts, absent baseline,
mandatory high trust and forbidden duplicate controllers; they assert no canonical
TASK/FAILURE authority. Tests separately supply stale/cross-profile/digest/partial
adaptation and malformed privacy/conformance counterexamples.

Each pair receives identical packet, subject, candidate, Human questions and
canonical references. Only the exact profile package differs; each profile has
its own bound construct. Modeled constructs and supplied risk declarations are
fixture material, not live provider detections or measured quality. The exporter
reports actual normalized input byte counts as structural cost observations. V1
carries more procedural input bytes; this does not establish token cost, better
decisions or whole-episode efficiency. Adjudication, quality improvement, North
Star superiority, provider availability, stage time/tokens, test-item reuse and
Human cost are explicitly `UNKNOWN`; provider run status is `NOT_RUN`.

Before any superiority claim, use the stated paired measurement protocol: exactly
two stages per profile, controlled or disclosed provider/model/effort/order
confounds, per-stage time and source-reported tokens, whole-episode operations/test
work and Human minutes/interventions. Record sources, assumptions and horizon;
missing/unpaired measures and disputed tradeoffs remain `UNKNOWN`. Human/Brain
assess actionable material risks, missed known risks, false positives, unnecessary
work, scope/authority expansion and limitations. Finding counts are not quality
scores, and the comparison evaluator creates no independent Reviewer or automatic
semantic verdict. An external measured run is absent from this implementation.

The pre-cutover design comparison considered retaining V3 and minimal V1;
the Human subsequently selected V1 exclusively for prospective work. Minimal
V1's measured benefit remains a hypothesis, and a separate adaptive controller
violates the approved non-goals.
Hard authority/safety/evidence floors precede any cost tradeoff. No fabricated
ROI, automatic winner, cutover or successor work follows from this basis.

### 18.5. Historical TASK-340 activation boundary (superseded by 18.6)

At TASK-340 publication, V3 was the active prospective profile and V1 activation
was not authorized. Source/profile availability, structural conformance, a desk
audit, comparison output or merge did not authorize that transition. The separate
explicit Human/Brain reconciliation followed canonical Runtime verification,
independent Reviewer judgment and eligible-source Publisher publication, as
recorded in section 18.6. Measured quality/cost evidence and unproven live-provider
behavior remain UNKNOWN. TASK-340 did not select an Executor/provider, dispatch
execution or perform automatic lifecycle advancement.

### 18.6. Human-approved exclusive prospective cutover (2026-10-12)

**Controlling prospective decision:** After TASK-340 r1 / RUN-340-001 RESULT, REVIEW-340-001 PRIMARY PASS and exact Publisher publication of source `3a06bdcd53065e7a83e455143b5cd3921b2b0ddc`, Human explicitly orders `aios-adaptive-brain-audit-v1` as the **only active Brain semantic audit profile for new ARCHITECTURE, TASK_AUTHORING, REMEDIATION_AUTHORING, REPAIR_AUTHORING subjects**. The approved bound profile is `version: 1` and normalized digest `982ba27964c78e7a7f79f5f268afb32eed27605be90b0642e4001b2edf116718`. This prospective Human/Brain selection supersedes only the earlier **V3 active** status in §18.5 and the registry's historical introductory comment; it does not rewrite any prior evidence, profile bytes or historical claim.

For a new eligible Brain semantic flow, pass **one** V1 profile package, run only `CONSTRUCT` and `ADVERSARIAL_AUDIT_AND_RECONCILE` with its eight required ordered lenses and final closure, and carry its exact ID/version/digest between stages. **Never also invoke V3** or produce a second semantic verdict, never silently fall back to V3 when a V1 profile or consumer binding is missing; in that case fail closed on that invocation or present a bounded explicit blocker. Existing V2/V3 subjects, including a V3 Stage 1 already in flight, retain their historical exact profile identity and may only finish under their exact bound old profile or use separately authorized restart; no mixed V3 Stage 1/V1 Stage 2. Keeping legacy validators/profile entries is **read compatibility, not active dual reasoning**.

The audit and provider protocols validate and transport explicit exact V1 packages without an additional audit router; the caller's existing explicit profile selection remains the single semantic decision input. TASK-342 separately authorizes migration of the V3-dependent correction ingress consumer. A caller that has not actually selected V1 must **not** claim live V1 invocation. TASK-341 r1 and RUN-341-001's model-capacity FAILURE remain immutable; continuation requires separately authorized REPAIR after migration publication. TASK-342 performs no TASK-341 correction. Human accepts activation while paired live V3/V1 comparative quality, latency, tokens and cost remain **UNKNOWN**; no North-Star superiority claim follows. A later optional evaluation must not run dual audits on ordinary production decisions or auto-advance any lifecycle.

### 18.7. Prospective correction consumer migration — TASK-342

Historical migration record: the following describes TASK-342's published consumer
gate. Its prospective serialized admission requirements are superseded by the
published TASK-348 repair boundary and, after TASK-360 publication, section 18.8 below.
The original audit/profile and historical lineage assertions remain unchanged.

`AUTHOR_REPAIR` and `AUTHOR_REMEDIATION` reconstruct their existing exact canonical
correction packet. The same composition path reads the canonical main roadmap's
`adaptive_brain_audit_v1_priority_20261012.exclusive_prospective_cutover_20261012`
policy, requires its active authorization and exact approved V1 identity, and
requires agreement with the containing policy's active profile. It selects exactly
one normalized canonical registry package by ID/version/digest. Missing, malformed,
changed or contradictory policy/profile material fails closed without V3 fallback.

Both supplied stages must carry that exact reference; a V3-only handoff or either
mixed V3/V1 pairing receives an explicit profile mismatch before semantic handoff
validation or authoring mutation. The existing Stage-2 validator runs once over
the two-stage handoff. Packet/construct bindings, all eight ordered lenses and
final closures, privacy/reserved-key rules, return-contract identity, canonical
TASK and failed RUN/finding REVIEW lineage, current-main input proof and pre-mutation
and pre-publication freshness checks remain required. Freshness reconstruction
does not invoke another provider or audit.

The provider protocol remains explicit-package transport, with one V1 package for
each new covered flow and precisely `AUDIT_CONSTRUCT` then `AUDIT_RECONCILE`.
Historical V2/V3 parsing, exact bound replay and in-flight completion retain their
original content/digests and safety grammar, without prospective substitution.
Identical canonical correction replay remains read-only and adds no audit.
BO-1 `AUTHOR_TASK` remains direct final-contract ingress without a serialized
audit prerequisite. No schema field, selector/router, persistent reasoning ledger,
Runner/Reviewer/Publisher authority or lifecycle transition is added. Runtime owns
canonical verification and EVIDENCE for the migration.


### 18.8. Direct final correction contracts: TASK-348 / TASK-360

The published AUTHOR_REPAIR boundary admits the final REPAIR contract directly.
After TASK-360 publication, AUTHOR_REMEDIATION likewise admits one final
`parse_remediation` / `validate_remediation` family contract with explicit source RUN,
finding and expected reviewed-source SHA through the existing one-owner ingress.
Runtime reconstructs TASK revision, RUN/RESULT/EVIDENCE, correction predecessor,
exact CHANGES_REQUIRED REVIEW decision/ref and chosen outstanding finding from
authenticated canonical objects and current refs. Repaired PRIMARY and REMEDIATION
sources preserve their native historical result base and DELTA ancestry. Action,
bounded scope, original constraints, exact TASK verification policy and authorized
affected commands remain subject to the existing family and canonical source checks.
New V1 corrections use `minimum-sufficient-v1`; historical V1/V2 decoding and immutable
replay retain their original bindings.

Brain still selects only the exact approved `aios-adaptive-brain-audit-v1`, version 1,
digest `982ba27964c78e7a7f79f5f268afb32eed27605be90b0642e4001b2edf116718`, for new
REMEDIATION_AUTHORING decisions. It performs `CONSTRUCT` then
`ADVERSARIAL_AUDIT_AND_RECONCILE`, with all eight ordered lenses and final closure.
This semantic obligation does not require Runtime to revalidate serialized stages,
Decision Packet fingerprints, derived support hashes or global context recomposition
as another mutation authority. Supplied legacy correction `audited_handoff` is inert
compatibility data, never an alternative contract, canonical metadata or receipt
content; differences in its hashing, profile or serialization cannot change final
contract admission. Historical Brain audit validators retain their read semantics.

AUTHOR_REMEDIATION checks subject-specific currentness before the metadata commit
and at local ref CAS and push: exact source, REVIEW decision/ref, outstanding finding
and canonical correction state, with captured upstream and expected-missing destination
lease. It rejects moved, resolved, superseded or conflicting subjects without a hidden
finding selector. Identical canonical replay, including equivalent final-family
formatting, is read-only and preserves the immutable stored bytes. Main movement
with unchanged correction state and broad unrelated local `.ai` serialization/parity
are outside this witness. An older
clean checkout missing unrelated TASK files is usable, while every consumed local
TASK must still equal authenticated canonical main. Dirty executable checkout,
unauthenticated ancestry, substituted local Runtime state, malformed canonical content
or missing exact authenticated/fetched sources fail closed.

The existing GitHub Issue actor, origin/provenance and allowlist checks remain.
Authoring creates no RUN and grants no Human A3 / REMEDIATION INTENT or self-host
execution delegation. No Executor/model selector, correction router, automatic
correction or publication is introduced. Runtime alone executes verification and
constructs EVIDENCE; independent Reviewer and Publisher authority are unchanged.

## 19. R2.1 / R1: bounded ROA-1 R2 conformance support

This slice implements **isolated conformance**, not the R2.1 production cutover.
The exact Adaptive V1 production selection in section 18.6 remains effective for
all four prospective flows. The current roadmap NEXT, direct `AUTHOR_REPAIR`
ingress, Runtime admission/verification/EVIDENCE/CAS, independent Reviewer and
Publisher, and frozen Kernel retain their existing authorities. No audit support
envelope, source digest or provider result authorizes mutation or execution.
MOA/TCA/AOA, Human V1 opt-in, retirement and Lean VP require separate authorization.

### 19.1. One exact portable profile

`.ai/brain-audit-profiles.yaml` includes this fourth, explicitly recognized identity:

```yaml
audit_profile_ref:
  id: roa-1-r2
  version: 2
  digest: aa6a5b60997849d6a00f9e4174ca7ef20fbc62ed64da645ee938a94f8e2639e4
```

ROA applies only to `REPAIR_AUTHORING`. Its normalized `checks[].check` material
defines exactly five ordered checks: `EXACT_FAILURE_BOUNDARY`,
`EVIDENCE_DISCRIMINATING_CAUSE`, `MATERIAL_REPAIR_LEVER`, `FALSIFYING_CHALLENGE`,
and `LAWFUL_READINESS`. The single `REPAIR_OUTCOME_AUDIT` procedure makes one
decision with one final closure. It has no CONSTRUCT/reconcile stages, universal
eight-lens ledger, second normative V1 audit or implicit V1/V3 fallback.

At R1 the registry count ceiling was exactly four recognized identities, at most
32768 UTF-8 bytes. ROA requires its exact normalized digest, including procedure,
instructions and bounds. Rehashing modified instructions, adding a check/stage,
renaming another profile, changing bounds or supplying another version cannot
remint ROA. Equivalent line endings and mapping order preserve identity. V1/V2/V3
bytes, digests, two-stage schemas and exact historical/in-flight replay remain
unchanged; registry order grants no selection authority.

ROA bounds are closed: 2048 UTF-8 bytes per profile instruction, 131072 candidate
JSON bytes, 4096 bytes per check basis and final summary, four source references
per check, 196608 semantic-output JSON bytes and depth 32. Existing request,
decision and native response bounds remain. Native JSON rejects duplicate keys;
mapping/JSON wrappers accept one response, never a list of decisions. Privacy
excludes local paths, raw logs/evidence, secrets, provider/model/session identity,
reasoning traces and nested protocol/support material from candidate/judgments.

### 19.2. Bound subject and five-check closure

`construct_roa_subject` derives `AIOS_ROA_SUBJECT` from the supplied exact Decision
Packet. It binds packet/work-context fingerprints, selected flow, profile reference,
return-contract reference, external `repair_id`, failed RUN/head and TASK revision.
It does not discover Git or accept model-authored source replacements. Its `sources`
contain only deterministic `{path, digest}` references into the packet:

- `failure`: `canonical_facts.correction_subject`, including the immutable FAILURE
  artifact identity and exact correction/TASK provenance, or the existing bound
  `subject` carrying that artifact SHA for explicit REPAIR supersession.
- `evidence`: the bounded phase, error class and structural facts in
  `bounded_observations`. These are source observations, not new Runtime EVIDENCE.
- `authorization`: the original `canonical_facts.task_contract`, with current
  repair-authoring state and no canonical blocker.
- `prior_authorization`: `prior_semantic_decisions` for explicitly bound,
  unexecuted REPAIR supersession, preserving its authorization and FAILURE SHA.

Unavailable decisive sources stay null. Mismatched source identity fails closed.
The caller retains canonical source authenticity/currentness; a content digest
only binds supplied material and cannot authenticate a forged packet or prove a
causal diagnosis. Brain must distinguish discriminating cause from structural
eligibility or a generic error class. Missing causal support must remain UNKNOWN.

One semantic response contains exactly `request_fingerprint`, `subject_fingerprint`,
`checks`, `closure` and `candidate`. `checks` has the five ordered records, each
with exactly `id`, `outcome` (`CLEAR|BLOCKER`), `knowledge`
(`OBSERVED|INFERRED|UNKNOWN`), bounded `basis` and `evidence_refs`. References must
match the bound packet sources exactly, with no duplicates. A non-UNKNOWN judgment
must cite its relevant source: failure for boundary, evidence for cause/lever/
challenge, authorization for readiness. OBSERVED and INFERRED are typed Brain
judgments, not Runtime proof; an inference still needs discriminating support and
a concrete falsifying challenge in the focused basis.

`closure` contains exactly `outcome` (`CANDIDATE|NO_DECISION`), `knowledge`, bounded
`summary` and `candidate_fingerprint`. Any BLOCKER requires NO_DECISION. Any UNKNOWN
requires a check BLOCKER and final UNKNOWN; missing decisive failure, evidence or
authority requires the affected check to be UNKNOWN/BLOCKER. NO_DECISION requires
both candidate and candidate fingerprint null. CANDIDATE requires all five checks
CLEAR, the exact final REPAIR identity, original scope/constraints and candidate
digest. Final knowledge is UNKNOWN if any check is UNKNOWN, otherwise INFERRED if
any is INFERRED, otherwise OBSERVED. No PASS or lifecycle status is emitted.

### 19.3. Provider-neutral isolated transport

The existing request/decision functions accept ROA only as explicit
`request_mode: ROA_CONFORMANCE`, with one `roa_subject` and null `stage1_lineage`.
Ordinary DIRECT/CONSTRUCT/reconcile modes reject ROA; ROA conformance rejects every
other flow/profile and any old Stage-1 decision. Historical modes keep their exact
serialization. Package lists, additional normative audits, mixed stages and mode
substitution fail closed. Request and decision revalidation reconstruct the bound
subject and closure, including references and fingerprints; rehashing outer
envelopes does not bypass stale return/packet/subject or forged-reference checks.

`attempt(..., conformance_only=True)` explicitly requests isolated ROA support.
It invokes one existing mapping or JSON provider once, validates the five-check
closure, then requires a fresh packet and the same rebuilt request fingerprint
before returning it. Source/authority/intent movement or malformed fresh context
fails without returning a decision, retrying, switching profiles or mutating
lifecycle state. Ordinary `attempt` retains DIRECT or historical two-stage behavior
and rejects an ROA package before invocation. The isolated flag is no production
policy override; direct correction ingress continues to validate its own final
family contract and canonical authority independently.

Focused regressions cover valid/UNKNOWN outcomes, absent or forged source support,
malformed closure/profile, digest reminting, wrong flow/mode, stale packet/return/
subject, native duplicate output, mixed/dual audits, unauthorized live selection,
no fallback, both native transports and unchanged historical replay. Runtime owns
the focused canonical verification and EVIDENCE. Conformance support and one-call
structure establish no measured quality, token, latency or episode-cost gain.

## 20. R2.1 / R2: isolated MOA-1 remediation conformance

MOA-1 adds bounded semantic support for the existing `REMEDIATION_AUTHORING` flow.
It does not activate the R2.1 successor policy. Sections 18.6 and 18.8 still govern
prospective Adaptive V1 selection and direct final `AUTHOR_REMEDIATION` ingress.
Human A3, Runtime admission/currentness/verification/EVIDENCE/CAS, independent
Reviewer judgment and Publisher authority retain their existing owners. Neither
a conformance decision nor its hashes resolve a finding or grant mutation rights.

### 20.1. Exact fifth profile and two focused passes

The finite registry recognizes exactly one additional identity:

```yaml
audit_profile_ref:
  id: moa-1
  version: 1
  digest: 49d1c4c3edefe3d3cefcd66e186b7d7b89738516129957c18415d55760ffe903
```

It applies only to `REMEDIATION_AUTHORING`. `passes` contains exactly:

1. `CONSTRUCT`: `EXACT_REVIEW_FINDING`, `FINDING_CAUSE_AND_MINIMUM_DELTA`,
   `ORIGINAL_TASK_AUTHORITY`.
2. `ADVERSARIAL_AUDIT_AND_RECONCILE`: `SOURCE_AND_CURRENTNESS_CHALLENGE`,
   `FINDING_CLOSURE_CHALLENGE`, `NON_INTERFERENCE_CHALLENGE`,
   `SINGLE_AUDIT_AND_AUTHORITY_CHALLENGE`.

The first pass frames the exact finding, evidence-supported cause, minimum delta
and original authority. It returns a non-final draft and typed judgments, with no
closure or handoff. The second pass independently challenges and reconciles that
draft on the same exact subject, then returns one final `CANDIDATE` or
`NO_DECISION` closure. Concrete counterexamples address wrong/stale/duplicate or
cross-review findings, action/SHA mismatch, unsupported shared cause, unrelated
edits, interference, missing evidence, misapplied EVIDENCE_ONLY, false/mixed PASS,
changed predecessor or verification, stale/revoked authority, forged profile,
mixed stages and duplicate normative audits. These are remediation-specific
checks, without a universal eight-lens ledger, V1 follow-up or third audit.

The registry now permits one to five recognized identities within the unchanged
32768-byte ceiling. Exact historical V1/V2/V3 and ROA contents/digests and their
existing serialization/replay remain intact. MOA requires its pinned normalized
digest over all instructions, stages and bounds; changing content and rehashing
an outer package cannot remint it. Registry order carries no policy authority.

MOA bounds: 2048 UTF-8 bytes per profile instruction, judgment basis,
counterexample and closure summary; 131072 candidate JSON bytes; four exact
source references and sixteen finding identities per judgment; sixteen source
REVIEW records and findings per source REVIEW; 65536 source-context JSON bytes;
196608 bytes per pass's semantic material; depth 32. Existing request, decision,
native response and attribution bounds remain. Closed fields, duplicate JSON/YAML
key rejection, private-path/metadata exclusions and protected candidate
normalization apply. No raw logs/evidence, credentials, chat/reasoning traces,
provider/model/session identity or nested protocol/support envelopes may enter
judgments, source projections or candidates.

### 20.2. Existing packet plus bounded canonical source projections

`construct_moa_subject(packet, profile, return_contract_ref, external_bindings,
source_context=None)` accepts the existing `UNIQUE_FINDING` or
`CORRECTION_FRONTIER` packet. It binds packet/work-context fingerprints, exact
profile and return-contract references, original TASK id/revision, every supplied
finding's RUN/REVIEW/id/reviewed SHA/basis/action/location/issue/expected, immutable
correction refs, outstanding status and available prior correction provenance.
It uses the existing compiler's `UNIFIED_STATE` selection basis. The packet's
`requires_fresh_context_for_continuation: false` is the ordinary flow-resolution
value; isolated MOA attempts independently require fresh composition between
passes and before return. No Decision Packet or return-contract schema changes.
External bindings are empty: they cannot smuggle in a selected frontier target.

The ordinary packet can lack full canonical REVIEW and RUN material. The optional
`moa_source_context` is an isolated, read-only protocol input supplied by the
caller before provider invocation. It has exactly `reviews`, an array of records
with exactly:

- `run`: `run_id`, `task` (`id`, `revision`), `head_sha`, `kind`
  (`PRIMARY`, `REPAIR` or `REMEDIATION`) and `prior_correction`.
- `review`: the existing canonical REVIEW fields `review_id`, `reviewed_sha`,
  `mode`, `verdict`, `acceptance`, `findings`, and optional `prior_finding_id`.
  Finding records have exactly `id`, `basis`, `action`, `location`, `issue`,
  `expected`. The existing REVIEW parser supplies structural grammar.
- `artifacts_sha` and `review_decision_sha`: the immutable source refs.

PRIMARY has null prior correction and PRIMARY review mode. REPAIR and REMEDIATION
carry the existing bounded prior-correction projection (kind, authorization SHA,
authorization and failed RUN/head or exact predecessor); REMEDIATION uses DELTA mode
and preserves its prior finding. Any available packet prior decision must agree.
The package must cover exactly the packet's source RUN set, with no duplicate or
unrelated source records. Each selected finding must occur exactly once in its
CHANGES_REQUIRED REVIEW and agree with every finding field, failed acceptance,
TASK identity, RUN head, canonical REVIEW ref and available original provenance.
The packet's canonical correction projection must bind the exact outstanding set,
source artifact, REVIEW decision and current authoring authority. A selector in
that pre-existing provenance does not select a frontier target.

The pure protocol reads no Git and cannot authenticate a caller's canonical
projections. Authenticated canonical reads, source-family validation and live
freshness remain the supplying caller's responsibility. Model-written strings,
source-looking names or content hashes provide no authenticity. Derived
`{path, digest}` references only bind those supplied values to this procedure;
the model cannot add or replace sources in its response. Missing or conflicting
material leaves `review`, `task` or `lineage` unavailable and records an explicit
bounded blocker. Parsing malformed input fails closed. Neither outer rehashing
nor an optimistic model declaration can discharge an unavailable role.

### 20.3. Typed judgments, frontier limit and one closure

`construct_moa` accepts exactly `subject_fingerprint`, `checks`, `draft_candidate`.
The three ordered checks each contain `id`, `outcome` (`CLEAR|BLOCKER`),
`knowledge` (`OBSERVED|INFERRED|UNKNOWN`), bounded `basis`, `evidence_refs` and
`finding_refs`. References must be unique and exactly match the derived sources
and authorized four-field finding identities. Source checks cover the complete
outstanding set. Non-UNKNOWN judgments require their discriminating sources;
UNKNOWN requires BLOCKER. A missing source or authority forces UNKNOWN/BLOCKER
for every dependent check. A draft with unavailable authority is forbidden.

`validate_moa` accepts exactly `subject_fingerprint`, `construct_fingerprint`,
`challenges`, `closure`, `reconciled_candidate`. The four ordered challenge records
add non-empty `counterexample` and `disposition` (`REJECTED_WITH_BOUNDED_BASIS`,
`ADDRESSED_BY_RECONCILIATION` or `BLOCKER`). A blocking disposition matches a
blocking judgment. Reconciliation claims must agree with an actual change from
the bound draft to the final candidate. The final closure contains exactly
`outcome`, `knowledge`, bounded `summary`, `candidate_fingerprint`; it binds the
reconciled candidate. Any challenge BLOCKER forces NO_DECISION with null candidate
and fingerprint. Final knowledge is UNKNOWN if any challenge is UNKNOWN,
otherwise INFERRED if any is INFERRED, otherwise OBSERVED. No PASS is emitted.

For `UNIQUE_FINDING`, only a closed second pass can expose a typed final proposal
matching the selected finding's exact id, required CODE_FIX/EVIDENCE_ONLY action
and reviewed SHA. It uses the existing preferred six-field candidate body:
`finding_id`, `action`, `reviewed_sha`, `modification_scope`, `verification`
(`policy`, `affected`, optional original `full_suite_reason`), `constraints`
(`hard`). Scope is a unique subset of original TASK scope; CODE_FIX requires a
non-empty supporting scope and EVIDENCE_ONLY has no code modification scope.
Original hard constraints and verification policy are
preserved; affected commands are unique, non-empty and authorized by the original
TASK's required commands. The existing remediation parser validates family
grammar. Semantic cause and non-interference remain Brain judgments about bound
sources, never machine proof of repair or canonical Reviewer finding closure.

A `CORRECTION_FRONTIER` with two or more outstanding findings supports bounded
cross-finding causal analysis only within that exact set. No ordered element,
ranking, guessed priority, shared-cause claim, external binding or proposed fix
can select a target or enlarge one correction's scope. Both draft and final
candidate remain null; the authority challenge must be UNKNOWN/BLOCKER and the
one closure NO_DECISION. This slice requires a separate exact selected canonical
subject before final remediation authoring. It introduces no frontier selector.

### 20.4. Isolated transport, freshness and unchanged downstream authority

`construct_request` accepts MOA only in explicit `MOA_CONSTRUCT` then
`MOA_RECONCILE` modes, carrying one exact profile, `moa_subject` and
`moa_source_context`. The second request revalidates the first request/decision
fingerprints and complete construct. All packet, profile, return, source and
stage bindings are reconstructed during request/decision revalidation. Rehashed
stale, swapped, mixed or malformed material fails closed. Historical modes reject
MOA; MOA modes reject V1/V2/V3/ROA. Lists of profiles/decisions, a prior audit on
construct, extra normative audits or a third pass are forbidden.

`attempt(..., conformance_only=True, moa_source_context=...)` uses the same mapping
or JSON provider for exactly two focused calls. It requires a fresh packet before
the second pass and again before returning the final decision. It rebuilds the
same first request with captured normalized packages and requires the identical
fingerprint. These structural freshness reconstructions invoke no provider or
semantic audit. Source, intent, original verification or authority movement,
unavailable freshness, malformed output and duplicate native JSON keys abort
without a decision, retry or profile fallback. Ordinary prospective attempts
cannot select MOA from registry membership; Adaptive V1 policy is unchanged.

The source package, stages and closure are transient conformance material, never
a second correction validator, persistent evidence store or new ingress
prerequisite. Existing direct AUTHOR_REMEDIATION independently checks its exact
canonical source RUN, REVIEW, outstanding finding, correction lineage, final
contract and current authority. Human A3/REMEDIATION INTENT, admitted Executor,
Runtime verification/EVIDENCE, Reviewer and Publisher are not inferred from MOA.
This slice changes no roadmap or downstream adoption and introduces no full-suite
or V2 verification gate. Focused modeled tests cover positive/UNKNOWN unique and
frontier cases, concrete adversarial challenges, profile/byte/depth bounds,
malformed/currentness and dual-audit cases, both native transports and historical
and ROA regressions. They establish no live provider quality or cost superiority;
Runtime owns canonical verification and EVIDENCE on the committed candidate.
