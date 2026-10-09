# VP-02 Proof Coverage Contract v1

Contract version: `proof-coverage-v1`

Mapping version: `proof-mapping-v1`

Compatibility analysis version: `frozen-task-compatibility-v1`

Status: TASK-326 bounded design and validation artifact; no production activation.

## Authority and applicability

This document implements the bounded VP-02 foundation in the Human-approved
[verification architecture v1.1](AIOS-VERIFICATION-ARCHITECTURE-REPLACEMENT-v1.md).
Brain supplies semantic requirements and proof adequacy. Reviewed, versioned
mapping supplies explicit coverage relationships. Runtime remains the sole
canonical verification and future scheduling owner. Reviewer judges semantic
sufficiency; Publisher owns publication. A caller-provided approval/review
reference is a declaration, not authentication of that authority.

The module `src/aios_renew/proof_coverage_contract.py` validates decoded in-memory
data and returns immutable declarations or prospective decision descriptions.
It imports no executor or production verification module. It performs no pytest
execution, subprocess call, Git operation, base checkout, file discovery,
network access, credential access, evidence mutation, or production verification.
It installs no Runtime preflight and no admission or compatibility gate. No
production call site, TASK parser, authoring ingress, profile, scheduler, Kernel,
or historical artifact is changed. Validation cannot establish Runtime PASS,
Reviewer PASS, evidence validity, or production activation.

VP-01 remains `DEFERRED_BY_HUMAN`, not DONE or waived. VP-02 remains the sole NEXT;
this artifact neither advances the roadmap nor establishes VP-01 measurements,
execution savings, latency, VP-07 conformance, or a publication/cutover exit that
depends on those measurements. TASK-326 r1 continues under the already-admitted
`minimum-sufficient-v2` policy and its bound RUN. Its transport/model selection
and legacy return route do not become proof-contract fields or grant authority
for another execution.

## Bounded semantic declaration

All schemas reject missing and extra fields, unsupported versions, incorrect
types (including booleans used as integers), duplicate identities and bounds
violations. Inputs are decoded plain dictionaries and lists. There is no
file/YAML loader, callback, runner, command parser, plugin lookup, environment
probe, or automatic dependency resolver.

Opaque IDs/references contain 1–128 ASCII characters: a leading letter followed
by letters, digits, `_`, `.`, `:`, or `-`. A path, nodeid, shell composition or
space-separated command cannot be a reference. References denote reviewed
immutable/versioned identities; they do not contain executable commands. The
module does not dereference them or infer their meaning. Changing a relevant
fixture, population, helper, toolchain or other condition requires a new
identity and reviewed contract/mapping revision. A caller must never reuse an
identity for changed content. Authentication and actual applicability/validity
of those identities belong to later VP-03 work.

| Bound | v1 limit |
| --- | --- |
| Contract obligations / TASK acceptance IDs | 256 each |
| Mapping proofs / entries / total declared claims | 256 each |
| Fixture references per condition | 64 |
| Reference/ID characters | 128 |
| Semantic claim text characters | 2,048, nonempty single line |
| Revisions | Positive integer up to 2,147,483,647 |
| Declared proof population | 1–100,000 items; an identity and count, never a discovered list |
| Workers | 1–256; serial requires exactly one |
| Replay bounds | At most 100,000 items, 3,600 seconds and one attempt |

Every root contract has exactly these fields:

| Field | Meaning |
| --- | --- |
| `schema` | Exactly `proof-coverage-v1` |
| `id`, `revision` | Stable contract identity and positive version |
| `execution_default` | Exactly `candidate-first-no-reproduction`; a prospective rule, not an execution request |
| `task` | Exact `task_id`, `revision`, 64-character lowercase SHA-256 `envelope_digest`, and nonempty `acceptance_ids` |
| `provenance` | Opaque `authority_ref`, `review_ref`, `source_ref` |
| `obligations` | Nonempty bounded list of semantic obligations |

`validate_proof_contract(data, expected_task=...)` requires an independently
supplied exact TASK binding, including its complete acceptance ID set. It never
discovers TASKs or hashes repository files. Each acceptance ID must appear in at
least one obligation; unknown IDs fail closed. Coverage of an ID may require
multiple obligations with different conditions, and all must be mapped.

Each obligation has exactly `id`, `revision`, `claim`, `acceptance_ids`, `kind`,
`blocking`, `conditions`, and `provenance`. The claim has exactly a stable `id`
and bounded semantic `text`. The same claim ID with conflicting text is invalid.
Recognizable leading pytest, Python, shell, Git and AIOS executable invocations
are rejected as claim text. This guard is not a semantic adequacy oracle;
upstream review must establish that the text describes a proof property.
`kind` is `candidate` or `comparison`. `blocking` declares whether a complete
failure of this obligation can be decisive. Nonblocking obligations still
require coverage; the flag does not erase required proof. Each obligation owns
approval, review and source provenance and a digest binding all its fields.
Duplicate equivalent semantic obligations must be explicitly consolidated with
their acceptance coverage preserved; different conditions are never collapsed.

## Full distinguishing conditions and equivalence

Every condition object explicitly contains all of the following fields. Missing
is unknown and invalid; there are no inferred/default condition dimensions.

| Fields | Distinction retained |
| --- | --- |
| `candidate_sha`, `base_sha` | Exact lowercase 40-character source identities; candidate has null base; comparison has a distinct exact base |
| `population_ref`, `population_size` | Exact approved population identity/count; no filename or nodeid inference |
| `test_ref` | Relevant test-code identity |
| `fixture_refs`, `shared_state_ref` | Fixtures, helpers, setup/teardown and shared-state conditions; an explicit empty fixture list means none |
| `profile_ref`, `worker_mode`, `workers` | Execution profile and serial/parallel worker requirements, including a parallel one-worker profile |
| `concurrency_ref`, `ordering_ref` | Concurrency topology and execution order requirements |
| `integration_ref`, `repetition_ref` | Unit/whole-suite/integration context and approved repetition or repeatability purpose |
| `toolchain_ref`, `environment_ref` | Interpreter/tools/observer and relevant environment identities |
| `evidence_kind`, `evidence_schema`, `collection_ref` | Required evidence type/schema and observation/collection conditions |

One contract binds one candidate subject and at most one comparison base.
`proof_conditions_equivalent(left, right)` is conservative equality over every
validated dimension. `obligations_equivalent` additionally requires identical
semantic claim ID/text, proof kind and blocking behavior. Obligation IDs,
revisions and provenance remain independently retained even when equivalence
is established; equality is not evidence validity or permission to drop an
acceptance entry. Unknown or merely similar conditions are never equivalent.

Unordered declarations (acceptance IDs, fixture IDs, claims, obligations,
proofs and entries) are sorted in immutable output; duplicates are rejected.
Actual execution ordering is the explicit `ordering_ref`, not incidental list
order. Canonical digests use SHA-256 over the validated normalized record encoded
as ASCII JSON with sorted keys, compact separators, `ensure_ascii=True` and
`allow_nan=False`. Schema versions, provenance, conditions and revisions are
included. Unsupported versions require a new decoder, not reinterpretation.

Example: two obligations may name the same population and semantic claim, but
one requires isolated serial fixtures and the other shared whole-suite fixtures
with twelve parallel workers. They require two mappings/proof descriptors. A
passing isolated delta cannot discharge the whole-suite obligation. Identical
command text or test nodeids would not change this result; neither is an
equivalence key in this contract.

## Deterministic reviewed mapping interface

`validate_proof_mapping(contract_data, mapping_data, expected_task=...,
expected_mapping=...)` requires both independently supplied pins. The mapping
pin has exactly `id`, `revision`, and `digest`; callers obtain its approved
value from reviewed immutable input, not by trusting an incoming document's
self-description. `proof_mapping_digest(data)` supports offline pin preparation
and validates structural shape only. It does not validate semantic coverage or
establish review authority.

The mapping root has exactly `schema`, `id`, `revision`, `contract_id`,
`contract_revision`, `contract_digest`, `provenance`, `proofs`, and `entries`.
`schema` is `proof-mapping-v1`. Contract identity, revision and full digest must
match the supplied contract. Each proof descriptor has exactly `id`, `kind`,
`claims`, `conditions`, and `provenance`. Claims are explicit semantic ID/text
pairs. Each entry has exactly `obligation_id`, `obligation_revision`,
`obligation_digest`, and `proof_id`.

Mechanical resolution requires:

1. Exactly one explicit entry per obligation. Missing, unknown or repeated
   obligation mappings fail; repeated identical entries are still ambiguous.
2. Exact obligation revision/digest, existing proof ID, compatible proof kind,
   exact conditions and the exact semantic claim. No inferred subsumption.
3. Every proof is used, and its claims are exactly the claims of its mapped
   obligations. Unsupported extra claims cannot manufacture coverage.
4. Equivalent proof descriptors share one explicitly declared proof. Distinct
   semantic claims may share that proof only with equal conditions, retained
   individual entries and an explicit reviewed claim list. Different fixture,
   integration, worker, order, concurrency, toolchain, environment or evidence
   conditions require separate descriptors.

The return value is `CoverageDeclaration(contract, mapping)`, representing
complete **declared** coverage. It contains no passed/discharged status and no
execution list. Structural validation does not prove a mapping's semantic
adequacy: Brain/Reviewer must establish that the declared tests and conditions
can prove the claims. Matching paths, filenames, changes, history, elapsed time
or nodeids never adds a mapping. There is no fallback or dependency guesser.

## Candidate-first failure semantics

`candidate_disposition` takes the same pinned declarations plus a synthetic,
explicit `candidate-disposition-v1` observation description. Its exact fields
are `schema`, `contract_digest`, `mapping_digest`, `obligation_id`,
`obligation_digest`, `subject_sha`, `observation_ref`, `outcome`, `complete`,
`stable`, `conflicting`, `conditions`, and `observed_items`. Source, obligation,
contract, mapping, complete population count and conditions must match. Actual
observation validity is outside VP-02; flags cannot authenticate evidence.
An incomplete description may report zero observed items; it cannot BLOCK or
establish PASS.

| Description | Prospective disposition |
| --- | --- |
| Complete, stable, non-conflicting FAIL of a blocking candidate obligation; no independent comparison | `BLOCK`, attribution `UNKNOWN` |
| Same failure with a required independent comparison | `UNRESOLVED`, with every outstanding comparison ID retained |
| PASS, UNKNOWN, incomplete, unstable, conflicting or nonblocking failure | `UNRESOLVED`; validation cannot establish PASS |
| Malformed, stale, mismatched or contradictory population/binding | `ProofCoverageError` |

`reproduction_requested` and `base_replay_requested` are always false. A
candidate PASS or FAIL never requests exact reproduction, base materialization,
base collection, a narrow probe, or a broad fallback. Independent candidate
integration proofs remain represented; an early blocking failure establishes
failure only and does not claim those proofs succeeded. There is intentionally
no API to discharge comparison obligations or reuse canonical observations:
VP-03 and the later single Runtime scheduler own those functions.

## Base replay eligibility: six affirmative facts

`evaluate_base_replay` accepts an explicit `base-replay-eligibility-v1` record
and the same independently pinned contract/mapping. Exact record fields are
`schema`, `contract_digest`, `mapping_digest`, `obligation_id`,
`obligation_digest`, `gates`, `decision`, `alternative_status`, `scope`,
`conditions`, `bounds`, and `already_covered`.

`gates` may contain only `BR-1` through `BR-6`. Each fact has exactly
`established` (true, false or null) and `basis_ref` (opaque ID or null). True
requires a non-null basis. A missing, false or unknown fact disallows replay.
Even six true labels cannot override contradictory prerequisites:

| Gate | Required fact and mechanically checked prerequisite |
| --- | --- |
| BR-1 APPROVED_NECESSITY | Approved baseline-dependent obligation; kind must be comparison, with exact base identity |
| BR-2 DECISION_RELEVANCE | `UNDECIDED_REQUIRED` or `MANDATORY_COMPARISON`; `DIAGNOSTIC_ONLY` denies eligibility |
| BR-3 NO_VALID_ALTERNATIVE | Immutable history has already been inspected without executing base; comparable evidence is `ABSENT` or proven `INVALID`, never `VALID` or `UNKNOWN` |
| BR-4 MINIMUM_LAWFUL_SCOPE | Reviewed minimum scope and bounded method; scope `population_ref` and `items` exactly equal the approved obligation's population, with opaque `method_ref` |
| BR-5 EXACT_COMPARABILITY | Full `conditions` exactly equal approved comparison conditions, including both source identities and all evidence/execution dimensions |
| BR-6 AUTHORITY_COST_BOUNDS | Bounds `authority_ref` matches obligation approval; opaque `cost_ref`, positive `max_items`/`max_seconds`, exactly one `max_attempts`; scope fits bounds and `already_covered` is false |

No-valid-alternative, true minimum scope, comparison relevance, comparability
and approved cost/authority must be affirmatively established by the rightful
upstream owner, with reviewed basis identities. This module checks their
declarations and contradictions; it neither reads history nor fabricates facts.
An overly broad scope cannot replace an approved smaller population. Cost
limits cannot waive a mandatory comparison or authorize widening after denial.

The result lists failed gates deterministically in BR-1–BR-6 order. Well-formed
ineligible input returns `eligible=False`; malformed/stale input raises.
`eligible=True` is only a design eligibility description;
`execution_authorized=False` is always retained. No result is PASS and no result
launches base. Denial retains unknown attribution or an existing decisive
failure; an independent mandatory comparison remains unresolved.

## Frozen TASK compatibility analysis v1

The frozen [Kernel specification](AIOS-RENEW-KERNEL-v0.1-SPEC.md), section 4.1,
defines a minimum `verification.required` list containing a “targeted
verification requirement.” It does not make a proof-obligation object part of
that minimum schema. Section 7 requires progressive verification, justified
scope and reuse until invalidated. Semantic requirement identities can fit the
minimum **scalar-list form** without changing acceptance's `id`/`condition`
entries or the canonical roles. That structural observation grants no policy
cutover or Kernel amendment authority.

The active parser in `src/aios_renew/task.py` admits only `policy`, `required`
and `full_suite_reason` under verification; `required` is a nonempty string
list and admitted policy names are V1/V2. Current
[authoring rules](AIOS-RENEW-BRAIN-TASK-AUTHORING-CONTRACT.md) require executable
commands there. `verification_contract.py` and `verification.py` consume those
commands through the published V2 machinery, including reproduction and base
comparison. An opaque string may survive existing command validation; that
does **not** make it a semantic reference understood by Runtime. Feeding a
semantic reference into active V2 would send it to the command runner.

| Alternative | Compatibility and required authority |
| --- | --- |
| This TASK: external design contract, synthetic tests, compatibility document; admitted command list untouched | Fits frozen Kernel and current policy. No Runtime integration, preflight or amendment is introduced. |
| Future semantic reference strings in the existing nonempty `verification.required` scalar list, e.g. a versioned contract requirement ID; separate immutable reviewed mapping resolves implementation mechanics | Preserves the frozen minimum form. Requires explicit prospective policy/authoring/decoder authorization and one Runtime scheduler cutover in separate TASKs, plus rightful compatibility resolution of operative semantics. Never treat these strings as V2 commands. Brain would author semantic requirements rather than executable commands. |
| Future externally derived commands while preserving a lawful TASK required-list envelope | Potential form-preserving engineering option, but cannot silently rewrite immutable TASKs or pretend Brain stopped command authoring if commands are still the operative authored requirements. Requires separately authorized prospective authoring/Runtime design; this artifact does not choose it. |
| Remove/empty `verification.required`, replace the string list with obligation dictionaries, or make a new operative TASK field replace that required minimum | Changes the frozen normative TASK contract. Requires explicit Human Kernel authority/amendment, separately admitted implementation, versioned decoding and historical preservation. Roadmap approval is insufficient. |
| Override frozen roles, allow mappings/Executor to create canonical evidence or review authority, or reinterpret frozen historical TASK/RUN artifacts | Changes frozen authority/lineage semantics. Requires rightful Human constitutional/Kernel governance where applicable; cannot be an engineering shortcut. |

An inline operative proof field rejected by the current exact-field parser is
a TASK schema extension, not an available compatibility escape. Its normative
status must be resolved by Human governance before any separate implementation;
do not infer that a “minimum schema” permits an unapproved active contract.
If governance finds that a proposed semantic interpretation changes a frozen
normative requirement despite identical YAML shape, it also requires explicit
Kernel amendment rather than reinterpretation in place. Conversely, the
external design artifacts here and a future form-preserving policy proposal
are not themselves silent Kernel mutations.

Human declined another runtime compatibility preflight due to possible latency
cost. This is a versioned **analysis**, not such a gate. It performs no runtime
check, repeated verification stage or admission policy change. Future
eligibility/applicability and scheduling remain VP-03/VP-04 work, instrumentation
VP-06 and cutover VP-07, with ordinary Human authority and canonical lifecycle.
No V1/V2 decoder or historical lineage is migrated.

## Focused conformance surface

`tests/test_proof_coverage_contract.py` uses synthetic in-memory declarations.
It covers valid/immutable/deterministic contracts; malformed versions/types,
missing coverage, contradictory workers, unbounded input and command-shaped
metadata; independent TASK/mapping pins; missing/ambiguous/stale/conflicting
mapping; explicit shared proof and distinct integration conditions; every
missing/false/unknown BR gate and contradictory prerequisite; exact scope,
authority and cost; decisive candidate BLOCK; outstanding comparison; PASS,
incomplete/unstable/conflicting observations; and absence of automatic replay.
Effect traps cover process, pytest entry, file, discovery, network and
environment access while exercising all public validators/decision helpers.
They assert zero effect calls, including when replay is design-eligible.

These tests are foundations for later conformance work. They are not real
historical measurements, actual Runtime observation validity, canonical
verification EVIDENCE, VP-01 completion, or production scheduling validation.
