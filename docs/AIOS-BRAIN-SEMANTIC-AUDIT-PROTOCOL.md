# AIOS Brain Semantic Audit Protocol — Planning Evidence

Status: HISTORICAL BP-4A HUMAN/BRAIN PLANNING BASELINE; prospective successor contract in section 18
Canonical evidence cut: main 63dce92adb271ea78d04bcd151df91a8ed260ff7
Scope: read-only retrospective plus BP-4A architecture intent; no implementation authority

Sections 1–17 preserve the original BP-4A planning and completion claims in their
historical context. Their `v1` / `brain-high-value-v1` refers to that original
protocol, not `AIOS_ADAPTIVE_BRAIN_AUDIT_V1`. The current active prospective
profile remains `brain-high-value-v3`. Section 18 specifies a separately
identifiable bounded planned successor; registry presence does not activate it.

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

`AIOS_ADAPTIVE_BRAIN_AUDIT_V1` is the bounded planned successor described by the
Human-approved A+ design in [AIOS Adaptive Brain Audit v1](AIOS-ADAPTIVE-BRAIN-AUDIT-v1.md).
Its availability for exact structural conformance and comparison is distinct
from prospective production cutover. `brain-high-value-v3` remains active until
independently authorized cutover; V2/V3 in-flight and historical subjects retain
their own exact identities and meanings. No roadmap transition is implied.

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
to 32768 bytes and at most three distinct recognized profiles. Registry order
keeps V3 first; adding V1 is capability for planned conformance, not selection.

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

Apply alternatives to V1 itself: retaining V3 is valid, minimal V1 is a design
hypothesis, and a separate adaptive controller violates the approved non-goals.
Hard authority/safety/evidence floors precede any cost tradeoff. No fabricated
ROI, automatic winner, cutover or successor work follows from this basis.

### 18.5. Activation boundary

V3 remains the active prospective profile. Source/profile availability, structural
conformance, a desk audit, comparison output or merge does not authorize activation.
Prospective V1 cutover requires separate explicit Human/Brain reconciliation after
canonical Runtime verification, independent Reviewer judgment and eligible-source
Publisher publication, including relevant consumer compatibility. Missing measured
quality/cost evidence and unproven live-provider behavior remain visible as UNKNOWN.
This implementation changes no roadmap sequencing, selects no Executor/provider,
dispatches no execution and performs no automatic lifecycle advancement.

### 18.6. Human-approved exclusive prospective cutover (2026-10-12)

**Controlling prospective decision:** After TASK-340 r1 / RUN-340-001 RESULT, REVIEW-340-001 PRIMARY PASS and exact Publisher publication of source `3a06bdcd53065e7a83e455143b5cd3921b2b0ddc`, Human explicitly orders `aios-adaptive-brain-audit-v1` as the **only active Brain semantic audit profile for new ARCHITECTURE, TASK_AUTHORING, REMEDIATION_AUTHORING, REPAIR_AUTHORING subjects**. The approved bound profile is `version: 1` and normalized digest `982ba27964c78e7a7f79f5f268afb32eed27605be90b0642e4001b2edf116718`. This prospective Human/Brain selection supersedes only the earlier **V3 active** status in §18.5 and the registry's historical introductory comment; it does not rewrite any prior evidence, profile bytes or historical claim.

For a new eligible Brain semantic flow, pass **one** V1 profile package, run only `CONSTRUCT` and `ADVERSARIAL_AUDIT_AND_RECONCILE` with its eight required ordered lenses and final closure, and carry its exact ID/version/digest between stages. **Never also invoke V3** or produce a second semantic verdict, never silently fall back to V3 when a V1 profile or consumer binding is missing; in that case fail closed on that invocation or present a bounded explicit blocker. Existing V2/V3 subjects, including a V3 Stage 1 already in flight, retain their historical exact profile identity and may only finish under their exact bound old profile or use separately authorized restart; no mixed V3 Stage 1/V1 Stage 2. Keeping legacy validators/profile entries is **read compatibility, not active dual reasoning**.

Current repository code already validates and transports the exact V1 package without an additional audit router; the caller's existing explicit profile selection remains the single decision input. No new production-code mutation or Runtime/Reviewer/Publisher authority is authorized by this Human/Brain planning cutover. A caller that has not actually selected V1 must **not** claim live V1 invocation. TASK-341 remains a separate VP03 Runtime NEXT and is not an activation prerequisite, nor is its Executor implied by TASK-340. Human accepts activation while paired live V3/V1 comparative quality, latency, tokens and cost remain **UNKNOWN**; no North-Star superiority claim follows. A later optional evaluation must not run dual audits on ordinary production decisions or auto-advance any lifecycle.
