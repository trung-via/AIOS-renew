# AIOS Brain Provider Protocol — Planning Baseline

Status: HUMAN/BRAIN PLANNING BASELINE
Canonical evidence cut: main dcd75f33f91438ea346c032ba13109a19c107c1a
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

## 4. AIOS_BRAIN_REQUEST v1 identity

`AIOS_BRAIN_REQUEST` is a transient, bounded, read-only semantic request. Its fingerprint is deterministic over normalized request semantic material and excludes provider/model/session/endpoint/host/time/random invocation metadata.

A request binds at minimum:

```text
format/version/kind
request_mode
exact AIOS_DECISION_PACKET v1
packet_fingerprint
work_context_fingerprint
selected_flow
decision_family_ref
handoff_target
expected_return_shape
mode-specific semantic material
request_fingerprint
```

For audited modes, mode-specific material includes the exact normalized audit profile body plus `audit_profile_ref`. For Stage 2 it additionally includes the exact validated Stage-1 construct.

The provider does not calculate `request_fingerprint`; deterministic support does.

Changing provider/model/session without changing semantic material must not change `request_fingerprint`.

## 5. Closed request modes

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

## 6. Stage 1 — AUDIT_CONSTRUCT

The Stage-1 request carries the exact Decision Packet plus self-sufficient normalized audit profile material and its content-addressed ref.

The provider returns only the bounded semantic candidate required by the request schema together with the exact request fingerprint echo. It does not compute cryptographic identities.

Deterministic support then calls the reviewed BP-4A construct primitive and derives the exact `construct_fingerprint`.

A validated Stage-1 Brain decision is explicitly intermediate. It is not a handoff candidate and cannot mutate canonical state.

## 7. Freshness gate before Stage 2

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

## 8. Stage 2 — AUDIT_RECONCILE

The Stage-2 request carries:

```text
fresh Decision Packet with the same packet_fingerprint
same normalized audit profile material/ref
exact validated Stage-1 construct
exact construct_fingerprint
request_fingerprint
```

The provider returns one bounded semantic Stage-2 body containing construct audit, reconciliation, closure and protocol-local outcome fields required by BP-4A.

Deterministic support validates the response with the reviewed BP-4A Stage-2 validator. The provider does not calculate `construct_fingerprint`, reconciled-candidate fingerprint, Stage-2 fingerprint or final decision fingerprint.

For one successful audited semantic attempt there are exactly two admitted provider invocations: one `AUDIT_CONSTRUCT` and one `AUDIT_RECONCILE`.

## 9. DIRECT diagnostic request

`DIAGNOSTIC` remains Brain-owned but is not mandatory BP-4A two-stage work.

A DIRECT request carries one exact Decision Packet and returns one bounded diagnostic proposal. It may include a bounded uncertainty declaration such as:

```text
NONE
MATERIAL
```

with a bounded summary when MATERIAL.

A DIRECT result is a Brain semantic proposal, not Runtime evidence, canonical FAILURE, lifecycle BLOCKED state, review verdict or correction authorization.

## 10. AIOS_BRAIN_DECISION v1

Provider-native output is untrusted input. The adapter extracts only the declared bounded structured response; provider-native metadata stays operational and outside semantic validation.

Deterministic validation materializes `AIOS_BRAIN_DECISION v1`. It binds:

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
mode-specific validated semantic value
invalidation/currentness basis copied from the request
decision_fingerprint
```

For `AUDIT_CONSTRUCT`, the decision contains the validated intermediate construct and `construct_fingerprint`.

For `AUDIT_RECONCILE`, the decision contains the validated BP-4A outcome and is final only for the Brain semantic procedure. `CANDIDATE` exposes one handoff candidate; `NO_DECISION` exposes none.

For `DIRECT`, the decision contains the bounded diagnostic proposal and uncertainty declaration.

Provider/model/session identity never contributes to `decision_fingerprint`.

`AIOS_BRAIN_DECISION` is not canonical lifecycle state and is not itself permission to mutate a canonical artifact.

## 11. Canonical handoff

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

## 12. Thin BrainProvider adapter

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

## 13. Provider selection and attribution

Provider implementation choice belongs to Human/configuration, not semantic inference.

The exact provider/model/session/invocation identifiers may be recorded as subordinate operational attribution, but they do not identify Brain authority and do not affect semantic request/decision identity.

A new audited authorization may select a different provider implementation explicitly. BP-5 does not implement an adaptive model router.

The contract may permit Stage 1 and Stage 2 to use different explicit provider implementations while retaining one Brain authority, but BP-5 does not claim cross-stage or cross-checkpoint hot-swap conformance. That proof remains BP-7.

## 14. Provider failure semantics

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

## 15. BP-5 vs BP-7/BP-8

BP-5 proves contract interchangeability, not production hot-swap and not a real second-provider deployment.

BP-5 exit evidence may use at least two independent adapter/conformance implementations over the same exact request/decision contracts. They must demonstrate that provider-specific metadata does not change semantic identity and that adapters cannot add semantic authority.

BP-7 later proves fresh cross-context/provider continuation across real AIOS checkpoints without previous model memory.

BP-8 later performs one controlled real non-default Brain and/or Reviewer provider proof. BP-5 must not consume that milestone early merely to satisfy its adapter conformance gate.

## 16. Bounds and parsing

The successor TASKs must choose exact fail-closed bounds. Planning targets are:

```text
AIOS_BRAIN_REQUEST normalized bytes: <= 393216
raw provider structured response:    <= 655360
maximum structural depth:            <= 32
```

These are planning ceilings, not implementation truth until an executor-neutral TASK canonically authorizes them.

No silent truncation. Invalid Unicode, NaN/non-JSON values, duplicate normalized keys, unknown protocol fields, nested request/decision envelopes and forbidden private metadata fail closed.

Provider-native outer envelopes may contain operational metadata, but only the declared bounded semantic response is admitted into Brain decision validation.

## 17. Recommended BP-5 implementation sequence

Do not collapse BP-5 into one mega-TASK.

### BP5-P1 — Provider-self-sufficient audit profile

Prospectively extend the existing repository audit profile so every current lens id carries bounded normative procedural meaning in the content-addressed profile body. Update only the pure profile parser/conformance surface needed for that change. Preserve BP-4A authority and historical evidence.

### BP5-P2 — Brain request/decision contracts

Add pure `AIOS_BRAIN_REQUEST` / `AIOS_BRAIN_DECISION` construction and validation over exact Decision Packet, profile and BP-4A primitives. No provider network invocation is required in this phase.

### BP5-P3 — Thin adapter + bounded orchestration conformance

Add the thin provider adapter interface, typed provider failures, exact two-invocation audited-attempt orchestration and at least two independent adapter/conformance implementations. Fresh packet material is caller-supplied; the provider layer itself does not discover repository state.

Each phase requires a separately audited executor-neutral TASK and reviewed/published evidence before the next phase is relied upon.

## 18. BP-5 exit gate

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

## 19. Planning decision

BP-5 is the unique current Human/Brain planning milestone.

The first implementation obligation is provider-self-sufficient audit-profile material, because a provider with no GitHub or chat memory cannot reconstruct the intended eight audit lenses from identifiers alone. This is a prospective integration requirement discovered by BP-5 audit, not a reinterpretation of BP-4A historical completion.

No production mutation is authorized by this document. Each BP5-P1/P2/P3 implementation step requires fresh Brain Sync, a separately audited executor-neutral TASK, canonical admission, Runtime verification, semantic review and publication before later planning relies on it.
