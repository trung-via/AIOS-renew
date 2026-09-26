# AIOS Reviewer Provider Protocol

**Status:** BP-6 planning architecture  
**Authority:** Human/Brain planning only  
**Audit basis:** `main@6f13dbcc29dc608a5eb089528e5728ac42ff2153`  
**Depends on:** BP-3, BP-4, BP-4A, BP-5  
**Defers:** BP-7 cross-context/provider hot-swap proof, BP-8 real second-provider proof, BP-9 downstream adoption

## 1. Purpose

BP-6 makes semantic Reviewer judgment provider-neutral without merging Reviewer authority with Brain, Runtime, Executor, Publisher, authoring ingress, or provider-selection policy.

The target is not "a model that can emit REVIEW-shaped YAML". The target is a bounded, self-sufficient Reviewer request/decision contract that lets one explicitly selected Reviewer provider judge the exact canonical review subject with no prior chat memory and no provider-specific GitHub/repository access.

Frozen Kernel semantics remain authoritative:

- Reviewer owns semantic judgment.
- PRIMARY review evaluates TASK + RESULT + implementation delta + evidence summaries.
- DELTA review asks whether the explicit prior finding was resolved and whether the correction directly introduced a material defect.
- Runtime owns deterministic verification and evidence.
- Existing `review.validate_review` remains the canonical REVIEW validator.
- Authoring ingress owns REVIEW canonicalization.
- Publisher owns publication of the exact eligible reviewed source candidate.

BP-6 must not create a second Reviewer, automatic remediation selector, generic provider/model router, lifecycle router, or persistent reasoning/session authority.

## 2. Audit finding: current SEMANTIC_REVIEW packet is necessary but insufficient

The current BP-4 Decision Packet correctly binds canonical SEMANTIC_REVIEW identity, TASK facts, RESULT claims, bounded EVIDENCE summaries and prior correction provenance. It deliberately does not carry implementation source material.

That is insufficient for a provider with no repository access because Kernel PRIMARY review requires the implementation delta itself, not merely `changed_files`, Executor claims, and verification summaries.

The problem is clearest after a pre-review REPAIR. A successful repair RUN may change only a tiny repair delta while the required PRIMARY semantic review still covers the complete implementation from the original PRIMARY semantic base to the final candidate. Reviewing only the successful repair RUN base-to-head delta can therefore omit the actual implementation being judged.

DELTA review has a related two-view requirement. It must retain the semantic predecessor needed to judge the prior finding while also exposing the latest correction delta needed to detect a material defect introduced by the latest correction.

Therefore BP-6 must establish exact deterministic review-scope identity and bounded implementation material before it defines provider request/decision envelopes.

## 3. Authority boundary

### Reviewer owns

- acceptance judgments;
- semantic interpretation of TASK requirements;
- assessment of implementation delta;
- assessment of bounded evidence summaries;
- PRIMARY vs DELTA semantic judgment according to the already-derived canonical review mode;
- verdict `PASS | CHANGES_REQUIRED | BLOCKED`;
- finding basis/action/location/issue/expected where the existing REVIEW contract permits them.

### Deterministic coordination owns

- canonical TASK/RUN/RESULT/EVIDENCE and correction-lineage identity;
- review mode derivation from canonical lineage;
- semantic review base(s) and reviewed head identity;
- bounded source-material extraction/binding;
- request/decision fingerprints;
- provider/model/session operational attribution;
- final REVIEW field binding for non-semantic identities;
- invocation-count enforcement;
- canonical validation and ingress handoff.

### Provider must not author

- canonical repository identity;
- RUN identity;
- reviewed SHA;
- review mode;
- prior finding identity for DELTA;
- provider/model selection policy;
- lifecycle next action;
- canonical mutation or publication authority.

A provider may emit the semantic REVIEW body, but deterministic materialization binds caller-owned identity fields before the existing `review.validate_review` gate.

## 4. BP-6 phase plan

### BP6-P1A — Semantic Review Scope Identity — DONE

Establish one deterministic, read-only review-scope projection from exact current lifecycle lineage.

The projection must fail closed unless current canonical state has exactly one SEMANTIC_REVIEW obligation and exact current TASK/candidate identity.

It must derive, without model judgment:

- `review_mode = PRIMARY | DELTA`;
- current reviewed RUN id and reviewed head SHA;
- `semantic_base_sha`:
  - direct PRIMARY: original PRIMARY RUN base;
  - PRIMARY after one or more pre-review REPAIR continuations: the original PRIMARY semantic base, not merely the latest failed head;
  - DELTA: the exact prior semantic REVIEW reviewed SHA for the finding being corrected;
- `latest_delta_base_sha`:
  - current RUN base for direct PRIMARY/REMEDIATION;
  - immediate failed-head/current RUN base for a REPAIR continuation;
- exact `prior_review_id` / `prior_finding_id` only when mode is DELTA;
- exact correction family/origin identity needed to prove the classification.

For DELTA after repair-of-remediation, the projection must preserve both views:

1. semantic predecessor -> current candidate, to judge whether the prior finding is resolved;
2. latest correction base -> current candidate, to judge whether the latest correction introduced a material defect.

The projection must reuse the existing canonical lifecycle graph/lineage semantics rather than invent a second lifecycle reducer. Competing, cyclic, missing, stale, or ambiguous predecessor lineage fails closed.

P1A does not read or package source file contents and does not invoke any provider.

Engineering closure: TASK-184 r1 completed through RUN-184-001, Runtime verification `43 passed in 44.74s`, REVIEW-184-001 PRIMARY PASS (AC1–AC8), and exact publication of candidate `070964600f4ae6e4282cfd3b92d7411c4abcab4f` to `main`. The implementation added the read-only `observe_semantic_review_scope` projection while keeping `AIOS_UNIFIED_STATE v1` serialization unchanged.

### BP6-P1B — Bounded Reviewer Material Package — NEXT

Introduce `AIOS_REVIEW_MATERIAL_PACKAGE v1` as cognitive-support material, not lifecycle truth or a second EVIDENCE artifact family.

The package binds one exact P1A review scope to minimum-sufficient implementation material. It must include every changed source path relevant to the semantic review range and enough deterministic textual material to let a repository-blind Reviewer inspect the actual change.

Planning requirements:

- exact package version and content fingerprint;
- exact semantic base / latest correction base / reviewed head binding;
- closed path/status grammar;
- content-addressed per-file material;
- bounded UTF-8 textual source/diff material;
- explicit handling of added/modified/deleted/renamed files;
- no silent truncation;
- no silent file omission;
- binary/unrepresentable material fails closed or is explicitly unsupported before provider invocation;
- no raw logs, credentials, chat history, model/session state, or unbounded repository dump;
- package oversize fails closed rather than asking the provider to guess.

Exact byte ceilings require measured task-level design and are not fixed by this planning document.

## 5. BP6-P1B task-design audit decision

Focused audit after TASK-184 confirms P1B should be a **separate deterministic source-material package**, not an extension of Unified State and not a second lifecycle reducer.

The package consumes one exact `AIOS_SEMANTIC_REVIEW_SCOPE v1` mapping as caller-supplied semantic identity. It may validate that mapping's closed field set and fingerprint, but it must not independently decide review mode, prior finding identity, semantic origin, or lifecycle next action. Those remain P1A / Unified State authority.

The preferred production boundary is a new module:

```text
src/aios_renew/review_material.py
```

with focused tests in:

```text
tests/test_review_material.py
```

No P1B production change is required in `unified_state.py`, `decision_packet.py`, `review.py`, ingress, publication, Runtime verification, Brain provider code, or provider adapters unless implementation evidence proves a contract gap and Brain separately widens scope.

### Package contract

`AIOS_REVIEW_MATERIAL_PACKAGE v1` is cognitive-support material only. It binds:

```text
format / version / kind
review_scope_fingerprint
review_mode
semantic_base_sha
latest_delta_base_sha
reviewed_head_sha
semantic_view
latest_delta_view | null
sources
package_fingerprint
```

`semantic_view` always describes the exact diff from `semantic_base_sha` to `reviewed_head_sha`.

`latest_delta_view` is null when `latest_delta_base_sha == semantic_base_sha`; otherwise it describes the exact diff from `latest_delta_base_sha` to `reviewed_head_sha`. This preserves P1A's two-view semantics without duplicating identical direct-PRIMARY/DIRECT-DELTA material.

Each view contains a deterministically ordered closed list of change records. V1 normalizes rename detection **off** so a rename is represented as one DELETE of the old path plus one ADD of the new path rather than relying on similarity heuristics.

Allowed v1 change statuses are:

```text
ADD
MODIFY
DELETE
```

Unsupported Git object/type transitions fail closed rather than being guessed into those statuses.

Each change record binds at least:

```text
path
status
base_content_sha256 | null
head_content_sha256 | null
review_source_ref | null
unified_diff
```

The shared `sources` table carries each unique complete review text at most once. For ADD/MODIFY the review source is the complete reviewed-head text; for DELETE it is the complete base text. The exact unified diff preserves removed/changed context, while the complete relevant source lets a repository-blind Reviewer inspect surrounding implementation context without dumping unrelated repository files.

Every source record is strict UTF-8 text, content-addressed with SHA-256, and referenced by change records. NUL-containing/binary data, invalid UTF-8, submodules, symlinks or other unsupported non-regular source types fail closed in v1 before provider invocation. No silent text replacement, lossy decoding, omission or truncation is allowed.

### Deterministic Git/material boundary

P1B may perform read-only Git object/diff access required to materialize the exact SHAs already chosen by P1A. It must not discover lifecycle truth, select a different candidate, reinterpret canonical refs, update working-tree files, create commits, run verification, or mutate AIOS state.

Material extraction must be immune to repository-configured external diff/textconv behavior and must operate on exact bound commit/blob objects. Path ordering is lexicographic over exact repository-relative paths. Paths are closed to normalized repository-relative Git paths; duplicate or malformed paths fail closed.

The package must cross-check that the semantic view's exact changed-path set equals the materialized change records and that every non-null source reference resolves to the exact content hash carried by that record.

### Bounds

Recent reviewed BP-5/P1A scopes provide the sizing baseline:

- TASK-183 PRIMARY introduced 621 changed lines across two new files; final file sizes were about 13.6 KiB and 14.5 KiB.
- TASK-184 PRIMARY changed 211 lines while the two reviewed-head files were about 71.8 KiB and 122.1 KiB.
- Recent P2 remediation deltas were materially smaller.

P1B v1 therefore uses explicit ceilings large enough for current real workloads while preventing repository dumps:

```text
max change records per view: 64
max unique source text:       262144 UTF-8 bytes each
max unified diff per view:    262144 UTF-8 bytes
max complete package:        1048576 UTF-8 bytes
```

These are admission ceilings, not truncation targets. Exceeding any bound fails closed with a typed material-package error. A future Human/Brain change may revise bounds only through a new reviewed TASK if real workloads justify it.

### Fingerprints

`package_fingerprint` is lowercase SHA-256 over canonical deterministic JSON of the complete normalized package excluding only `package_fingerprint` itself.

Each source's `content_sha256` is lowercase SHA-256 of the exact UTF-8 bytes represented in that source record.

Changing scope fingerprint, base/head identity, path/status, source text/hash, diff text, view membership or ordering must change the package fingerprint. Provider/model/session/operational metadata, chat history and raw verification logs are excluded.

### Failure and authority semantics

P1B failures are typed cognitive-support/material failures, not semantic REVIEW verdicts and not RUN/RESULT/FAILURE artifacts. At minimum implementation must distinguish:

```text
REVIEW_SCOPE_INVALID
GIT_MATERIAL_UNAVAILABLE
UNSUPPORTED_MATERIAL
MATERIAL_BOUND_EXCEEDED
MATERIAL_INCONSISTENT
```

None may fabricate `BLOCKED`, `CHANGES_REQUIRED`, REMEDIATION, REPAIR, publication or provider fallback.

P1B must not invoke any Reviewer/Brain provider and must not call `review.validate_review`; those belong later phases.

### P1B acceptance shape for successor TASK

The successor TASK should prove at least:

1. direct PRIMARY / direct DELTA with equal semantic/latest bases emits one semantic view and no duplicate latest view;
2. repaired PRIMARY and repair-of-remediation emit two exact bound views when bases differ;
3. ADD/MODIFY/DELETE material is complete, deterministic, content-addressed and rename-normalized as DELETE+ADD;
4. complete reviewed-head context is present for ADD/MODIFY and complete base context for DELETE, with exact diff text;
5. invalid UTF-8, binary/NUL, unsupported object types, missing Git objects, malformed/tampered P1A scope and all bounds fail closed;
6. package/source fingerprints are stable under equivalent material and change under any semantic/material mutation;
7. no lifecycle derivation, verification, provider invocation, canonical mutation, hidden repository dump or REVIEW authority is introduced.

### BP6-P2 — Reviewer Procedure + Return Contract

Create repository-owned, bounded, content-addressed Reviewer procedure material and REVIEW return-contract material.

Do not reuse the BP-4A Brain audit profile as Reviewer authority. Brain construction/reconciliation and Reviewer judgment are distinct semantic roles even if the same physical model may later occupy both roles at different checkpoints.

The provider-visible Reviewer procedure must encode the frozen semantics:

PRIMARY:
- assess every TASK acceptance criterion;
- inspect the full semantic implementation delta;
- cross-check Executor claims against source and evidence;
- detect TASK violations and material delta-introduced defects;
- do not audit the entire repository by default.

DELTA:
- determine whether the exact prior finding is resolved;
- determine whether the correction directly introduced a material defect;
- do not re-review the entire original TASK unless the correction invalidates prior conclusions.

The procedure may require an internal closure sweep in one invocation but must not require persisted chain-of-thought or a second semantic Reviewer call.

The provider semantic output owns only the semantic REVIEW body:
- verdict;
- acceptance assessments;
- findings.

Deterministic request bindings own:
- review_id;
- reviewed_sha;
- mode;
- prior_finding_id where applicable.

The existing `review.validate_review` remains authoritative after deterministic materialization.

### BP6-P3 — Pure Reviewer Request / Decision Protocol

Introduce exact provider-neutral `AIOS_REVIEW_REQUEST v1` and `AIOS_REVIEW_DECISION v1`.

Request material must bind at least:
- exact SEMANTIC_REVIEW Decision Packet;
- exact P1A review scope;
- exact P1B review material package;
- exact P2 Reviewer procedure package;
- exact P2 REVIEW return-contract package;
- external caller bindings such as review_id;
- request fingerprint.

Provider/model/session/invocation identity is operational metadata and must not enter semantic request/decision fingerprints.

Provider response must be closed and bounded. Deterministic validation must:
- verify exact request echo/binding;
- validate semantic response grammar;
- bind non-provider-authorable REVIEW identity fields;
- reconstruct the canonical REVIEW candidate;
- call existing `review.validate_review` with exact TASK/RESULT/prior-review context;
- produce only a transient `AIOS_REVIEW_DECISION`.

A transient Reviewer decision is not a canonical REVIEW. Only existing `SUBMIT_REVIEW` ingress may canonicalize it.

Provider/protocol failures must not fabricate `BLOCKED`, `CHANGES_REQUIRED`, REVIEW, REMEDIATION, REPAIR, FAILURE, or publication state.

### BP6-P4 — Thin Reviewer Provider Conformance

Add a Reviewer-specific thin invocation shell over P3.

One review attempt uses one already-selected immutable Reviewer provider/model identity and exactly one semantic provider invocation.

BP-6 deliberately does not copy BP-5's two-stage `AUDIT_CONSTRUCT -> AUDIT_RECONCILE` loop. Reviewer is the semantic judge; a second model call to review the Reviewer would create redundant semantic reasoning / multi-reviewer behavior contrary to the frozen Kernel unless separately justified later.

P4 must prove:
- exactly one provider invocation per admitted review attempt;
- bounded operational attribution separate from semantic identity;
- provider/model attribution drift fails closed;
- no retry/fallback/voting/provider switch;
- no hidden-session semantic dependency;
- no repository/GitHub discovery inside provider adapters;
- no automatic remediation or publication;
- typed transport/response/material failures remain pre-REVIEW operational failures;
- at least two independent non-network adapters consume/produce the same P3 contract.

Low-level transport primitives may be structurally similar to BP5-P3, but BP-6 must not turn them into a generic provider/model router or merge Brain and Reviewer semantic authorities merely to remove small code duplication.

## 5. REVIEW identity and semantic-body boundary

The canonical REVIEW contract remains unchanged unless a later separately authorized task proves a real contract gap.

The Reviewer provider may decide:
- `verdict`;
- acceptance outcomes;
- findings using only existing remediation actions such as `CODE_FIX` and `EVIDENCE_ONLY`.

The Reviewer provider may not decide:
- `review_id`;
- `reviewed_sha`;
- `mode`;
- `prior_finding_id`.

Those values are canonical/caller-bound identity material.

A provider timeout, malformed response, material-package overflow, identity mismatch, or adapter failure is not a semantic `BLOCKED` verdict. `BLOCKED` remains a Reviewer semantic verdict under the existing REVIEW contract.

## 6. Evidence and source-material boundary

Runtime EVIDENCE remains canonical proof. `AIOS_REVIEW_MATERIAL_PACKAGE` is not another evidence authority.

The package may project bounded evidence summaries already validated by the Decision Packet/request, but must not rerun verification, reinterpret Runtime evidence as new canonical truth, or embed unbounded raw logs.

Implementation source material is supplied only so Reviewer can perform the semantic source/delta inspection required by the frozen Kernel.

## 7. Session and provider independence

Reviewer correctness must be self-sufficient from the exact request. Prior chat, hidden model memory, previous provider responses, or session continuation cannot be required semantic inputs.

The same physical provider/model may be selected for Brain at one checkpoint and Reviewer at another, but role authority is determined by the exact contract:
- Brain request => BRAIN authority;
- Reviewer request => REVIEWER authority.

Co-location never merges authorities.

Cross-provider continuation between checkpoints is BP-7, not BP-6. A real non-default Reviewer provider invocation is BP-8, not BP-6.

## 8. Failure semantics

BP-6 implementation must keep at least these classes conceptually separate:

- caller/canonical input invalid;
- review scope missing/ambiguous/stale;
- review material unavailable/unsupported/over-bound;
- provider transport failure;
- provider response invalid;
- provider/model attribution mismatch.

None of these are REVIEW verdicts or engineering RUN failures.

No BP-6 layer may automatically retry, fail over, select another provider, author remediation, publish, or mutate lifecycle state.

## 9. BP-6 exit gate

BP-6 is complete only when reviewed/published evidence proves:

- exact deterministic review-mode/scope derivation across direct PRIMARY, repaired PRIMARY, REMEDIATION DELTA, and repair-of-remediation DELTA;
- repository-blind Reviewer receives complete bounded implementation material for the exact semantic review range;
- no required source file is silently omitted or truncated;
- Reviewer procedure and return-contract material are self-sufficient and content-addressed;
- one exact provider-neutral REVIEW request/decision contract exists;
- provider cannot author canonical review identity fields;
- final transient decision validates through existing `review.validate_review`;
- PRIMARY and DELTA preserve frozen Kernel semantics;
- one review attempt performs exactly one Reviewer provider invocation;
- provider/model/session attribution is operational-only;
- provider/material failures cannot fabricate REVIEW/BLOCKED/remediation/publication;
- at least two independent non-network adapters conform to the same Reviewer contract;
- no Brain/Reviewer authority crossover, persistent reasoning/session store, model router, lifecycle router, automatic remediation, or new mutation authority is introduced;
- BP-7 hot-swap proof and BP-8 real-provider proof remain unconsumed future milestones.

## 10. BP6-P1A task-design audit decision

Focused overlap audit finds that the exact correction-lineage semantics already exist in `src/aios_renew/unified_state.py`: decoded lifecycle nodes, operational-parent traversal, REPAIR-to-origin traversal, DELTA predecessor resolution, cumulative lineage ordering, and fail-closed handling for competing/cyclic/missing lineage.

Therefore BP6-P1A must **reuse that lifecycle graph semantics** rather than re-parse publication/ingress lineage in a second Reviewer-specific reducer.

The preferred contract boundary is a separate read-only projection:

```text
AIOS_SEMANTIC_REVIEW_SCOPE v1
```

It is not a new lifecycle state and must not change the serialized `AIOS_UNIFIED_STATE v1` contract merely to carry Reviewer cognitive support.

The exact closed semantic fields should bind:

```text
task: {id, revision}
reviewed_run_id
review_mode: PRIMARY | DELTA
semantic_origin_run_id
semantic_base_sha
latest_delta_base_sha
reviewed_head_sha
prior_review_run_id: string | null
prior_review_id: string | null
prior_finding_id: string | null
scope_fingerprint
```

Derivation rules:

- direct PRIMARY: semantic origin is the current PRIMARY RUN; semantic base and latest-delta base equal that RUN base;
- repaired PRIMARY before any semantic review: traverse only exact REPAIR parents to the PRIMARY origin; semantic base is the original PRIMARY base; latest-delta base is the current successful REPAIR RUN base;
- direct REMEDIATION DELTA: semantic origin is the REMEDIATION RUN; semantic base is the exact predecessor REVIEW reviewed SHA; latest-delta base is the current REMEDIATION RUN base;
- repair-of-remediation DELTA: traverse exact REPAIR parents to the REMEDIATION origin; semantic base/prior review/finding come from that remediation predecessor; latest-delta base is the current successful REPAIR RUN base;
- integrated/cumulative remediation keeps the semantic predecessor reviewed SHA distinct from the operational execution base.

The scope projection is valid only for one exact successful, unreviewed current candidate whose canonical lifecycle next action is SEMANTIC_REVIEW. Any candidate mismatch, competing tip, cycle, broken parent, missing predecessor review, inconsistent finding identity, or ambiguous semantic origin fails closed.

P1A does not package source content, inspect implementation semantics, invoke a provider, produce a REVIEW verdict, run verification, author remediation, or mutate canonical state.

The focused successor TASK should keep its production mutation surface inside the existing Unified State/lifecycle reduction boundary plus focused tests. It should not modify Decision Packet, REVIEW schema, authoring ingress, publication, Brain provider code, or provider adapters in P1A.

## 12. Planning decision

BP-6 is the unique current Human/Brain planning milestone.

BP6-P1A is reviewed/published complete through TASK-184 / RUN-184-001 / REVIEW-184-001 at `070964600f4ae6e4282cfd3b92d7411c4abcab4f`.

The next implementation obligation is BP6-P1B Bounded Reviewer Material Package under the task-design audit above. A separately authored executor-neutral TASK is required before production implementation. Do not jump to Reviewer procedure/provider request/provider invocation before P1B is reviewed and published.

No production mutation is authorized by this planning document.
