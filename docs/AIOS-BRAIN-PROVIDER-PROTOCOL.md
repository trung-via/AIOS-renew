# AIOS Brain Provider Protocol — Planning Baseline

Status: HUMAN/BRAIN PLANNING BASELINE
Canonical evidence cut: main 6901e29540bd98d1d5c4df0477b76594e5bcb4b7
Scope: BP-5 provider-neutral Brain request/decision architecture; no implementation authority

## 1. Purpose

BP-5 makes Brain semantic work portable across provider implementations without requiring the provider to read GitHub, inspect the repository, recover chat history, or become a new source of canonical truth.

The target composition is:

```text
fresh BP-3/BP-4 composition
        |
        v
AIOS_BRAIN_REQUEST v1
        |
        v
explicit BrainProvider adapter
        |
        v
one bounded untrusted structured response
        |
        v
deterministic Brain decision validation
        |
        v
AIOS_BRAIN_DECISION v1
        |
        v
BP-4A structural validation where applicable
        |
        v
existing canonical family validator / ingress
```

Provider identity is implementation metadata. Brain remains the semantic authority. Runtime, Reviewer, Publisher, Executor and Human authorities do not move.

## 2. Non-goals

BP-5 must not create:

- a second Brain, Planner, Reviewer or Publisher;
- a generic lifecycle router or next-action authority;
- automatic provider/model scoring, selection, retry, fallback or failover;
- a repository/GitHub browsing agent inside a provider adapter;
- provider-specific semantic prompts that change the Brain contract;
- a persistent prompt/conversation/reasoning store;
- a canonical semantic-decision database separate from existing artifact families;
- a real cross-checkpoint hot-swap proof, which belongs to BP-7;
- a controlled real non-default provider proof, which belongs to BP-8;
- Reviewer provider contracts, which belong to BP-6.

BP-5 does not authorize a provider response to mutate TASK, REMEDIATION, REPAIR, roadmap, Runtime state, review state or publication state directly.

## 3. Provider-self-sufficient audit profile

The published BP-4A profile proves structural lens identity, order, bounds and closure behavior. Its current `lenses` values are identifiers only. That is sufficient for BP-4A structural conformance but insufficient for a fresh provider that has no repository or chat context.

Before provider invocation, the repository-owned audit profile must be prospectively extended with bounded semantic descriptions for the same eight lens ids. The profile remains the single audit-policy authority; provider protocol code and adapters must not duplicate those descriptions.

The provider-visible lens semantics must remain bounded and procedural:

- `AUTHORITY_BOUNDARY`: test whether the candidate assigns semantic, implementation, execution, verification, review, publication or policy responsibility to the correct authority and does not silently merge authorities.
- `SCOPE_NON_GOALS`: test the candidate against explicit scope, constraints and non-goals; detect silent widening or hidden implementation beyond the authorized semantic subject.
- `PROVENANCE_LINEAGE`: test exact subject identity, immutable provenance, prior semantic decisions and correction continuity; detect substitution or lineage loss.
- `FAILURE_MODE_COUNTEREXAMPLES`: search bounded malformed, stale, conflicting, partial-state and substitution counterexamples that can violate the intended contract despite a happy-path success.
- `AC_CONSISTENCY_COMPLETENESS`: test that goal, constraints, non-goals and acceptance criteria are mutually consistent, cover identified material risks and do not require impossible or circular proof.
- `VERIFICATION_OWNERSHIP_ORDERING`: test that Runtime retains canonical verification authority and that the candidate does not require post-verification truth before verification or let Executor/Brain claims become evidence.
- `PORTABILITY_PRIVACY_BOUNDEDNESS`: test that the candidate does not depend on chat memory, provider GitHub access, hidden machine-local state, secrets, provider/session identity or unbounded context and remains portable under bounded inputs.
- `SIMPLIFICATION_DUPLICATE_AUTHORITY`: test for unnecessary repeated work, duplicated policy/state/semantic authority or redundant orchestration that can be removed without weakening guarantees.

These descriptions are audit procedure, not predetermined verdicts or answer keys. They must be included in the normalized content-addressed profile body, so any semantic change changes `audit_profile_ref.digest`.

Historical BP-4A completion is not reopened by this prospective profile extension. The exact historical profile remains attributable to its published SHA.

## 4. Provider-self-sufficient decision-family return contract

The BP5-P2 audit found a second self-sufficiency boundary after BP5-P1.

An exact Decision Packet exposes `selected_flow`, `decision_family_ref`, `handoff_target` and `expected_return_shape`, but those values are identifiers rather than a complete provider-facing candidate grammar. A fresh provider with no repository access cannot safely reconstruct the exact TASK/REMEDIATION/REPAIR proposal shape from tokens such as `TASK_AUTHORING_PROPOSAL` or `review.validate_remediation`.

Provider adapters must not solve this by hard-coding hidden prompt instructions or repository knowledge. Before the request/decision envelope is implemented, BP-5 therefore needs one bounded repository-owned return-contract projection.

The preferred v1 shape is a content-addressed `AIOS_BRAIN_RETURN_CONTRACTS` registry with one current contract for each Brain-owned flow:

```text
ARCHITECTURE
TASK_AUTHORING
REMEDIATION_AUTHORING
REPAIR_AUTHORING
DIAGNOSTIC
```

`SEMANTIC_REVIEW` is excluded because Reviewer authority belongs to BP-6.

Each return contract binds at minimum:

```text
selected_flow
decision_family_ref
expected_return_shape
provider-facing candidate contract
content digest
```

The provider-facing candidate contract must contain enough bounded procedural/shape information for a fresh provider to construct the intended proposal without reading repository code or docs. It is cognitive support only. It must not become a second canonical validator, semantic verdict, lifecycle authority, or substitute for the existing TASK/REMEDIATION/REPAIR validators.

For TASK/REMEDIATION/REPAIR, the existing family validator remains authoritative after Brain output. For ARCHITECTURE and DIAGNOSTIC, the return contract defines only the bounded proposal shape owned by the Human/Brain planning or diagnostic surface.

Any semantic return-contract change changes its digest. Provider/model/session/checkout representation does not.

## 5. AIOS_BRAIN_REQUEST v1 identity

`AIOS_BRAIN_REQUEST` is a transient, bounded, read-only semantic request. Its fingerprint is deterministic over normalized semantic material and excludes provider/model/session/endpoint/host/time/random invocation metadata.

Request construction requires an actual freshly compiled `DecisionPacket` object supplied by the caller. The protocol layer performs no repository/Git/GitHub discovery. A serialized request may later be revalidated as a strict-equivalent full Decision Packet mapping, but a request constructor must not synthesize one from partial fields.

The common request body binds exactly:

```text
format/version/kind
request_mode
exact AIOS_DECISION_PACKET v1
exact selected return-contract package
external_bindings
audit_profile package or null
stage1_lineage projection or null
request_fingerprint
```

The selected return-contract package contains the exact normalized selected contract, its effective bounds and `return_contract_ref`. Request construction cross-validates that package against the exact Decision Packet.

`external_bindings` exists only for return-contract fields marked `EXTERNAL_REQUEST_BINDING_REQUIRED`. Its key set must equal that exact set of candidate paths; no missing or additional binding is allowed. In v1 this supplies identities such as TASK target `task_id/revision` and REPAIR `repair_id` without letting the provider invent them. These values are semantic request material and therefore contribute to `request_fingerprint`.

For audited modes the request additionally carries the exact normalized `brain-high-value-v2` profile material and `audit_profile_ref`. DIRECT requires the audit-profile package to be null.

For `AUDIT_RECONCILE`, `stage1_lineage` is a bounded projection of one already validated Stage-1 Brain decision, not a nested request/decision envelope. It carries only the Stage-1 request fingerprint, Stage-1 decision fingerprint and exact validated BP-4A construct material required to continue the same semantic attempt.

The full Decision Packet already cryptographically binds `packet_fingerprint`, `work_context_fingerprint`, `selected_flow`, `decision_family_ref`, `handoff_target` and `expected_return_shape`. These fields are not re-supplied as independently authoritative request fields.

The provider does not calculate `request_fingerprint`; deterministic support does. Changing provider/model/session without changing semantic material must not change it.

Return-contract bindings whose source is `DECISION_PACKET` remain provider-facing semantic guidance and are ultimately checked by the existing family validator/ingress after a final CANDIDATE. BP5-P2B must not duplicate TASK/REMEDIATION/REPAIR validators merely to make those instructions executable. P2B does, however, enforce all `EXTERNAL_REQUEST_BINDING_REQUIRED` candidate paths exactly because those values are request identity rather than family semantics.

## 6. Closed request modes

BP-5 v1 has exactly three Brain request modes:

```text
AUDIT_CONSTRUCT
AUDIT_RECONCILE
DIRECT
```

Applicability is deterministic:

```text
ARCHITECTURE          -> AUDIT_CONSTRUCT then AUDIT_RECONCILE
TASK_AUTHORING        -> AUDIT_CONSTRUCT then AUDIT_RECONCILE
REMEDIATION_AUTHORING -> AUDIT_CONSTRUCT then AUDIT_RECONCILE
REPAIR_AUTHORING      -> AUDIT_CONSTRUCT then AUDIT_RECONCILE
DIAGNOSTIC            -> DIRECT
SEMANTIC_REVIEW       -> forbidden in AIOS_BRAIN_REQUEST
```

The provider cannot select the mode. There is no `AUTO`, `DEEP`, `FAST`, `REFLECT`, recursive audit, or semantic profile router.

## 7. Stage 1 — AUDIT_CONSTRUCT

The Stage-1 request carries the exact Decision Packet, exact selected return-contract package, exact external bindings, and the self-sufficient normalized audit-profile package.

The admitted provider semantic response shape is exactly:

```text
request_fingerprint
candidate
```

No packet/profile/contract/provider/model/session/construct identity may be supplied by the provider. Deterministic validation first requires an exact request-fingerprint echo, enforces any external-binding candidate paths, and then calls the reviewed BP-4A construct primitive to derive the exact `construct_fingerprint`.

A validated Stage-1 `AIOS_BRAIN_DECISION` is explicitly intermediate. It contains no handoff permission and cannot mutate canonical state.

## 8. Freshness gate before Stage 2

After Stage 1, the caller must freshly compose/revalidate the Decision Packet through the existing BP-3/BP-4 path.

The BP-5 protocol layer itself must not discover Git/GitHub state. Its Stage-2 request builder receives the caller-supplied fresh Decision Packet and compares exact fingerprints.

If:

```text
fresh_packet_fingerprint != stage1_packet_fingerprint
```

then the transient Stage-1 construct is invalid for continuation:

```text
discard transient Stage-1 state
do not invoke Stage 2
do not merge reasoning across subjects
```

A later attempt, if Human/control flow still requires one, starts again from a fresh Stage 1. BP-5 does not auto-retry it.

If the fingerprint is unchanged, Stage 2 may be built.

## 9. Stage 2 — AUDIT_RECONCILE

The Stage-2 request builder receives a caller-supplied freshly compiled `DecisionPacket`, the same selected return-contract/profile material, the same external bindings and one validated Stage-1 Brain decision.

It must prove before request construction:

```text
fresh packet_fingerprint == Stage-1 packet_fingerprint
same return_contract_ref
same audit_profile_ref
same external_bindings
validated Stage-1 construct lineage
```

The request carries only the bounded Stage-1 lineage projection needed by the provider; it does not nest the complete Stage-1 request or decision envelope.

The admitted provider semantic response shape is exactly:

```text
request_fingerprint
construct_audit
reconciled_candidate
closure
outcome
```

The provider does not re-supply packet/profile/return-contract/construct lineage. Deterministic support requires the exact request-fingerprint echo, enforces external bindings again on any final candidate, injects the already-known exact BP-4A lineage fields, assembles the Stage-2 input and validates it with the reviewed BP-4A validator.

The provider does not calculate `construct_fingerprint`, reconciled-candidate fingerprint, Stage-2 fingerprint or final decision fingerprint.

For one successful audited semantic attempt there are exactly two admitted provider invocations: one `AUDIT_CONSTRUCT` and one `AUDIT_RECONCILE`. Invocation itself remains BP5-P3.

## 10. DIRECT diagnostic request

`DIAGNOSTIC` remains Brain-owned but is not mandatory BP-4A two-stage work.

A DIRECT request carries one exact Decision Packet plus the exact DIAGNOSTIC return-contract package, has empty external bindings and has no audit-profile or Stage-1 lineage material.

The admitted provider semantic response is exactly:

```text
request_fingerprint
candidate:
  proposal
  uncertainty:
    status = NONE | MATERIAL
    summary = null | bounded text
```

`MATERIAL` requires a non-empty bounded summary; `NONE` requires null summary. Because DIAGNOSTIC has no downstream canonical artifact-family validator, this closed diagnostic body is validated by the provider protocol itself.

A DIRECT result is a Brain semantic proposal, not Runtime evidence, canonical FAILURE, lifecycle BLOCKED state, review verdict or correction authorization.

## 11. AIOS_BRAIN_DECISION v1

Provider-native output is untrusted input. Only the declared bounded semantic response is admitted to deterministic validation; provider-native metadata remains operational and outside semantic identity.

After exact request-fingerprint binding and mode-specific validation, deterministic support materializes one `AIOS_BRAIN_DECISION v1` with exactly:

```text
format/version/kind
authority_owner = BRAIN
selected_flow
decision_mode
request_fingerprint
packet_fingerprint
work_context_fingerprint
decision_family_ref
handoff_target
expected_return_shape
return_contract_ref
audit_profile_ref or null
external_bindings
mode-specific validated semantic_value
decision_fingerprint
```

`packet_fingerprint` plus `work_context_fingerprint` are the currentness/invalidation basis; no second independently supplied currentness object is needed.

For `AUDIT_CONSTRUCT`, `semantic_value` is the exact validated BP-4A Stage-1 construct and the decision is intermediate.

For `AUDIT_RECONCILE`, `semantic_value` is the exact validated BP-4A Stage-2 result. `CANDIDATE` exposes exactly its validated `handoff_candidate`; `NO_DECISION` exposes none.

For `DIRECT`, `semantic_value` is the validated bounded diagnostic candidate.

The decision fingerprint covers every semantic field except itself, including external bindings and all content-addressed refs. Provider/model/session identity never contributes to it.

`AIOS_BRAIN_DECISION` is not canonical lifecycle state and is not itself permission to mutate a canonical artifact.

## 12. Canonical handoff

A validated Brain decision is handed only to the existing authority-specific surface:

```text
TASK_AUTHORING
  -> existing TASK validator
  -> authoring ingress

REMEDIATION_AUTHORING
  -> existing remediation validator
  -> authoring ingress

REPAIR_AUTHORING
  -> existing repair-authorization validator
  -> authoring ingress

ARCHITECTURE
  -> explicit Human/Brain planning mutation boundary

DIAGNOSTIC
  -> read-only Human/Brain diagnostic output
```

BP-5 must not duplicate these semantic validators or write their canonical artifacts directly.

## 13. Thin BrainProvider adapter

A provider adapter may only:

```text
receive exact AIOS_BRAIN_REQUEST
serialize it to provider-native transport
invoke one explicitly selected provider implementation
extract one bounded structured response
return provider-native operational metadata separately
```

It must not:

- inspect Git/GitHub or repository files to fill missing context;
- add provider-specific semantic policy not present in the request/profile;
- choose another provider/model because of task difficulty;
- retry or fail over automatically;
- repair malformed semantic JSON by inventing content;
- alter the candidate;
- invoke Executor, Runtime, Reviewer, Publisher or authoring ingress;
- persist prompts, reasoning or conversation history as semantic truth.

Credentials/API tokens are adapter configuration only and never enter request/decision semantic material or fingerprints.

## 14. Provider selection and attribution

Provider implementation choice belongs to Human/configuration, not semantic inference.

The exact provider/model/session/invocation identifiers may be recorded as subordinate operational attribution, but they do not identify Brain authority and do not affect semantic request/decision identity.

A new audited authorization may select a different provider implementation explicitly. BP-5 does not implement an adaptive model router.

The contract may permit Stage 1 and Stage 2 to use different explicit provider implementations while retaining one Brain authority, but BP-5 does not claim cross-stage or cross-checkpoint hot-swap conformance. That proof remains BP-7.

## 15. Provider failure semantics

Provider transport/API failure and semantic-contract failure are pre-handoff operational failures.

They must not fabricate:

```text
RUN
RESULT
FAILURE
REVIEW
NO_DECISION
BLOCKED
REMEDIATION
REPAIR
publication success
```

At minimum BP-5 distinguishes:

```text
PROVIDER_TRANSPORT_FAILURE
PROVIDER_RESPONSE_INVALID
STALE_BEFORE_STAGE2
```

These are bounded provider-protocol outcomes/errors only. They do not trigger automatic retry, fallback, provider change or lifecycle mutation.

`NO_DECISION` is reserved for a structurally valid BP-4A Stage-2 semantic result with at least one closure blocker.

## 16. BP-5 vs BP-7/BP-8

BP-5 proves contract interchangeability, not production hot-swap and not a real second-provider deployment.

BP-5 exit evidence may use at least two independent adapter/conformance implementations over the same exact request/decision contracts. They must demonstrate that provider-specific metadata does not change semantic identity and that adapters cannot add semantic authority.

BP-7 later proves fresh cross-context/provider continuation across real AIOS checkpoints without previous model memory.

BP-8 later performs one controlled real non-default Brain and/or Reviewer provider proof. BP-5 must not consume that milestone early merely to satisfy its adapter conformance gate.

## 17. Bounds and parsing

The successor TASKs must choose exact fail-closed bounds. BP5-P2B planning ceilings are:

```text
AIOS_BRAIN_REQUEST normalized bytes:       <= 393216
provider semantic response normalized bytes: <= 655360
AIOS_BRAIN_DECISION normalized bytes:      <= 655360
one external-binding value:                <= 4096
maximum structural depth:                  <= 32
```

External-binding count is additionally limited by the selected return contract and in v1 cannot exceed its existing `bindings_count` ceiling.

These are planning ceilings, not implementation truth until an executor-neutral TASK canonically authorizes them.

No silent truncation. Invalid Unicode, NaN/non-JSON values, duplicate normalized keys, unknown protocol fields, nested request/decision envelopes and forbidden protocol metadata fail closed.

Provider-native outer envelopes and transport-specific metadata belong to BP5-P3. P2B bounds only the semantic response admitted into Brain decision validation.

## 18. Recommended BP-5 implementation sequence

Do not collapse BP-5 into one mega-TASK.

### BP5-P1 — Provider-self-sufficient audit profile — DONE

TASK-180 r1 / RUN-180-001 / REVIEW-180-001 PASS published `5e97e7b8d79d2f0e8b2d725ac4e9e7175a60fd4e`. The current `brain-high-value-v2` profile carries the eight bounded provider-visible audit-lens procedures in its content-addressed body.

### BP5-P2A — Provider-self-sufficient return contracts — DONE

TASK-181 r1 / RUN-181-002 / REVIEW-181-002 DELTA PASS published `6901e29540bd98d1d5c4df0477b76594e5bcb4b7`. The current `AIOS_BRAIN_RETURN_CONTRACTS v1` registry carries bounded provider-facing candidate guidance for the five Brain-owned flows and remains subordinate to existing canonical family validators.

### BP5-P2B — Pure Brain request/decision envelopes — DONE

TASK-182 r1 completed through RUN-182-001 / REVIEW-182-001 CHANGES_REQUIRED, the narrow FINDING-182-001 remediation, RUN-182-002, and REVIEW-182-002 DELTA PASS. The exact reviewed remediation candidate `09b2d099dd90abae12083d6b0f7f5f4abbdd377d` is published on `main`. The current `brain_provider_protocol.py` therefore establishes the provider-neutral `AIOS_BRAIN_REQUEST v1` / `AIOS_BRAIN_DECISION v1` semantic envelopes, exact request-owned external bindings, fresh Stage-2 lineage checks, DIRECT diagnostic grammar and fail-closed serialized Stage-2 decision revalidation without provider invocation or lifecycle authority.

### BP5-P3 — Thin adapter + bounded orchestration conformance — NEXT

P3 adds only an authority-neutral invocation shell over the already-reviewed P2B semantic protocol. The design boundary is:

```text
caller explicitly selects one BrainProvider for the attempt
caller supplies initial DecisionPacket + exact P2B packages/bindings
orchestrator constructs exact P2B request
adapter invokes provider exactly once
P2B validates semantic response
for audited flows only:
  caller-owned fresh_packet_supplier() recomposes current DecisionPacket
  stale packet => STALE_BEFORE_STAGE2 and zero Stage-2 invocation
  unchanged packet => construct exact AUDIT_RECONCILE request
  same selected provider is invoked exactly once more
  P2B validates final semantic response
return transient Brain decision + separate bounded operational attribution
```

The injected `fresh_packet_supplier` is a zero-argument caller-owned freshness boundary. P3 may call it exactly once after a validated Stage-1 decision, but P3 does not implement repository/Git/GitHub discovery, does not pass Stage-1 semantic content into the supplier, and does not persist its result. Supplier failure or invalid caller material fails closed before Stage 2.

For BP5-P3 conformance, one audited attempt uses the same explicitly selected provider implementation for both admitted invocations. Cross-stage/provider substitution is intentionally deferred to BP-7 hot-swap conformance. DIAGNOSTIC DIRECT performs exactly one provider invocation and no freshness callback.

The selected BrainProvider instance exposes immutable configured provider/model identity. Every invocation's bounded operational attribution must match that configured provider/model identity exactly; provider or model drift is fail-closed and must not be interpreted as fallback. Per-invocation/session identifiers may differ, but remain attribution only. P3 does not import or reuse Executor execution-profile selection to establish this identity.

Semantic correctness must not depend on hidden adapter conversation/session state. Each invocation is self-sufficient from its exact `AIOS_BRAIN_REQUEST`; Stage 2 receives the reviewed Stage-1 lineage through P2B request material rather than adapter memory. A conforming adapter may maintain bounded operational counters or transport handles, but no prior prompt/response/session content becomes semantic input to the next invocation.

The thin adapter contract may return only:
- one extracted semantic-response mapping to the P2B validator; and
- one separate bounded operational-attribution mapping.

Provider/model/session/invocation attribution never enters request/decision fingerprints. The adapter cannot choose a provider/model, alter semantic material, inspect repository state, retry, fallback, invoke another adapter, call authoring ingress, or produce lifecycle artifacts.

P3 must expose typed fail-closed outcomes at least for `PROVIDER_TRANSPORT_FAILURE`, `PROVIDER_RESPONSE_INVALID`, and `STALE_BEFORE_STAGE2`. Caller/input or freshness-supplier defects must remain distinguishable from provider failures rather than being mislabeled as transport or semantic-provider failures. None of these outcomes may fabricate RUN, RESULT, FAILURE, REVIEW, BLOCKED, NO_DECISION, REMEDIATION, REPAIR or publication state.

The final successful P3 surface returns transient semantic decisions only. It must not automatically hand a CANDIDATE to TASK/REMEDIATION/REPAIR validators or ingress. A valid transient NO_DECISION that intentionally omits a changed reconciled candidate is not made persistent merely to satisfy replay; P3 must not invent missing witness material or weaken the P2B fail-closed revalidation rule.

P3 conformance requires at least two independent non-network adapter implementations over the same exact interface, exercised separately against the same semantic requests. They must use distinct provider-native wrapper/extraction mechanics, while proving identical validated semantic identity for equivalent provider semantics and proving that different operational attribution cannot affect request/decision fingerprints. A real non-default provider deployment remains BP-8.

A successor TASK must choose exact bounded native-response and operational-attribution ceilings, must prohibit unbounded raw provider payload retention, and must prove invocation counts and stop conditions explicitly. It must not reuse Executor `codex_adapter.py` / `antigravity_adapter.py` or Executor execution-profile routing as Brain-provider authority.

Each phase requires a separately audited executor-neutral TASK and reviewed/published evidence before the next phase is relied upon.

## 19. BP-5 exit gate

BP-5 is complete only when reviewed/published evidence proves:

- provider-visible audit-profile material is self-sufficient and content-addressed;
- one exact provider-neutral `AIOS_BRAIN_REQUEST v1` contract exists;
- one exact provider-neutral `AIOS_BRAIN_DECISION v1` contract exists;
- provider/model/session identity is excluded from semantic request/decision fingerprints;
- audited flows use exactly `AUDIT_CONSTRUCT` then fresh-packet gate then `AUDIT_RECONCILE`;
- successful audited attempts have exactly two admitted provider invocations and no recursive semantic loop;
- stale packet before Stage 2 prevents the Stage-2 invocation;
- DIAGNOSTIC uses bounded DIRECT semantics;
- SEMANTIC_REVIEW is rejected and remains BP-6 Reviewer authority;
- provider failures cannot fabricate semantic/lifecycle outcomes and never auto-retry/fail over;
- adapters do not inspect GitHub/repository state or add provider-specific semantic policy;
- validated final candidates still pass existing canonical family validators/ingress;
- at least two independent adapter/conformance implementations consume/produce the same semantic contract;
- no persistent reasoning/chat store, model router, lifecycle router, Reviewer crossover, Executor selection or new semantic authority is created;
- BP-7 hot-swap proof and BP-8 real non-default provider proof remain unconsumed future milestones.

## 20. Planning decision

BP-5 is the unique current Human/Brain planning milestone.

BP5-P1 is engineering-complete through TASK-180 r1, RUN-180-001, REVIEW-180-001 PASS and publication of `5e97e7b8d79d2f0e8b2d725ac4e9e7175a60fd4e`.

BP5-P2A is engineering-complete through TASK-181 r1, RUN-181-002, REVIEW-181-002 DELTA PASS and publication of `6901e29540bd98d1d5c4df0477b76594e5bcb4b7`.

BP5-P2B is engineering-complete through TASK-182 r1, RUN-182-001 / REVIEW-182-001 CHANGES_REQUIRED, FINDING-182-001 remediation, RUN-182-002, REVIEW-182-002 DELTA PASS and publication of `09b2d099dd90abae12083d6b0f7f5f4abbdd377d`.

The next implementation obligation is BP5-P3 thin adapter + bounded orchestration conformance under the audited boundary above. P3 is not provider selection policy, not repository discovery, not a persistent session, not canonical handoff, not a model router, not Reviewer protocol and not lifecycle state.

No production mutation is authorized by this document. BP5-P3 requires a fresh Brain Sync, a separately audited executor-neutral TASK, canonical admission, Runtime verification, semantic review and publication before later planning relies on it.
