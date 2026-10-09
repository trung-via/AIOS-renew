# VP-03B immutable checkpoint lineage and replay v1

Status: TASK-331 r1 bounded offline foundation. **NOT_ACTIVATED**.

This document specifies `src/aios_renew/proof_checkpoint_lineage.py`. It describes
authenticated immutable history, factual proof applicability and duplicate/crash
observations. It grants no continuation, checkpoint append, evidence discharge,
feedback consumption, execution, terminalization or scheduling authority. It
writes no canonical or local persistent state. Runtime remains the sole
coordination, canonical verification, EVIDENCE and terminal owner.

The implementation imports the published
[VP-02 coverage/mapping foundation](AIOS-VP02-PROOF-COVERAGE-CONTRACT-v1.md),
[VP-02B declarations](AIOS-VP02-INTRA-RUN-PROOF-AND-KERNEL-GATE-v1.md) and
[VP-03A authenticated applicability](AIOS-VP03-PROVENANCE-APPLICABILITY-v1.md).
Their semantic/provenance rules and BR-1..BR-6 gates remain intact. Existing
canonical TASK, RUN, RESULT, EVIDENCE and observation decoders are reused. No
existing parser, Kernel, scheduler, Runtime, workflow or artifact is changed.

## Independent trust and issuer limits

The request is untrusted. A **separately trusted existing canonical-source
boundary** must supply `ReadOnlyLineageAuthority(repository, catalog, observed_at)`.
The repository, exact catalog identity and attested evaluation time must all
come from that boundary. They are never request fields. Missing authority returns
UNKNOWN. The API has no caller enrollment, `approved`, `trusted=True`, signature
substitute or declared checkpoint VALID grant.

Git authenticates objects, bytes and ancestry. It does not authenticate the
legitimacy of a Human, Runtime, Reviewer, catalog issuer, admission or semantic
review. Constructors are dependency injection, not an approval API. A caller
able to select the authority can manufacture an entire repository; such use
violates the interface precondition. `history_authenticated` describes content
and consistency **within the independently supplied root**. It is never evidence
that this module authenticated the external issuer or that live KA-01 is active.

The trusted boundary must independently establish canonical source legitimacy,
exact admission and current TASK CAS/blob/envelope identity, Human operational
consent, reviewed complete proof meaning/mapping/footprints, independently
attested candidate cleanliness, live lease and complete accounting. An opaque
SHA, digest, profile label, EVIDENCE identity, C1 PASS, synthetic approval or
mutable main cannot supply those facts. The module installs no such production
boundary and performs no authority discovery, credential/signature validation,
enrollment or host environment/proof inspection.

The offline fixtures simulate this external boundary with real disposable Git
objects and exact canonical record shapes. They are not actual Human consent,
Runtime evidence, production executions or Reviewer verdicts. In particular,
their explicit fake model/profile selection does not select TASK-331's model or
effort. TASK-330's sticky selection does not carry into TASK-331. Operational
delegation fields remain outside TASK schemas.

## Versioned bounded interface

| Record | Version |
| --- | --- |
| Request, sealed snapshot and factual result | `proof-checkpoint-lineage-v1` |
| Independently supplied catalog | `proof-checkpoint-lineage-authority-v1` |
| Prospective admission snapshot | `proof-checkpoint-admission-v1` |
| Human operational consent snapshot | `proof-checkpoint-human-v1` |
| Finite budget authority snapshot | `proof-checkpoint-budget-v1` |
| Independently attested checkpoint facts | `proof-checkpoint-facts-v1` |
| Currentness/crash/accounting snapshot | `proof-checkpoint-current-v1` |

`decode_request(value)` accepts exactly `schema`, `repository_id`, `checkpoints`
and `replay`. `checkpoints` is the exact finite cataloged sequence of immutable
record identities; `replay` is null or a received sealed record identity. There
is no callback, loader, instruction, command, validity label or authority field.
Malformed requests raise `LineageInputError`; directly constructed wrappers are
revalidated before any read.

Record identities reuse VP-03A's exact `commit_sha`, `path`, `blob_sha`, `sha256`:
full lowercase Git SHA-1 object IDs, SHA-256 of actual bytes and bounded exact
repository-relative regular-file paths. Ref names, abbreviated SHAs, traversal,
shell expressions, extra/missing fields, unsupported versions, arbitrary Python
objects, bool-as-integer and unbounded input fail closed. Committed UTF-8 JSON
snapshots reject duplicate keys; original raw logs remain bytes. No existing YAML
artifact is rewritten or decoded under a new status.

```python
request = decode_request(decoded_request)
# All three authority values originate independently at the trusted boundary.
authority = ReadOnlyLineageAuthority(repository, exact_catalog, attested_time)
facts = evaluate_lineage(request, authority=authority)
```

| Bound | Limit |
| --- | --- |
| Checkpoints / corrections | 17 / 16, inherited from VP-02B |
| Proofs/obligations/acceptance IDs | 256 per foundation list |
| Total VP-03A evaluations | 32 across original authentication and transitions; exceeding this smaller offline envelope is UNKNOWN before proof evaluation |
| Human correction / wall / item / token ceilings | 16 / 3,600 seconds / 100,000 items / 1,000,000 tokens; explicit Human limits must independently be supplied within these design ceilings |
| Authorized scope | 128 exact files, up to 512 characters each |
| Candidate full-tree file observer | 512 files, also subject to VP-03A's tighter total object/byte bounds |
| Each Git reader | Published VP-03A limits: 256 objects, 512 calls, 256 ancestors, 1 MiB per object/record, 8 MiB total object bytes, 5-second subprocess timeout |
| Decoded JSON | Published VP-03A 40,000-node / depth-20 / exact primitive bounds |

Only bounded local Git read commands are issued, through the published
authenticated reader with replacement objects, lazy fetch, inherited Git
environment and automatic maintenance disabled. No network, checkout, write,
proof command, worker invocation or Executor re-entry occurs. Unsupported trees,
symlinks, submodules, unknown footprints and exceeded limits return UNKNOWN;
this v1 offline observer is deliberately not a general production adapter.

## Catalog and exact authority binding

The catalog has exactly:

```text
schema, repository_id, expected_main_sha, expected_head_sha,
task, run, human, budget, operational_authority, admission, current,
checkpoints, transitions
```

The seven named source records are exact `RecordIdentity` pins. Every referenced
record must resolve to its actual pinned blob/bytes and belong to the catalog's
authenticated Git history. TASK source precedes the immutable admitted base;
expected main precedes that base. Source-record commit order is not a substitute
for the trusted boundary's admission/consent facts.

The existing TASK decoder checks the original task. The existing RUN decoder
must establish the exact TASK/revision/base/Executor and canonical `ACTIVE`,
`head_sha=null` admission snapshot. A candidate is bound separately; a populated
or terminal RUN is not imported as live checkpoint admission. Historical V1/V2
records do not become KA-01 approval. Admission explicitly requires `PRIMARY`,
`ADMITTED`, `KA-01-PROSPECTIVE`, exact task/run/operational record pins, original
`admitted_at` and the reviewed candidate-independent `semantic_digest`.
This prospective offline version marker is not an activated TASK/policy version.

The Human record contains exactly `schema`, `task`, `run`, `executor`, `profile`,
`model`, `effort`, `lease_id`, `lease_generation`, `lease_issued_at`,
`lease_expires_at`, `operation`, `opt_in_version`, `allowed_paths`, `limits`.
Its actual source/byte identity supplies the `Delegation.human` SourceDeclaration.
All selections must be explicit and match every checkpoint and live snapshot.
The Human-approved exact path set must equal the original TASK modify scope.

The budget record contains exactly `schema`, `human`, `limits`, `allowed_paths`,
`applicable_resources`. It must preserve that Human source, scope and limits.
V1 requires explicit finite accounting for `corrections`, `seconds`, `test_items`,
`tokens`. Omission or unsupported applicable resource accounting is UNKNOWN;
the caller cannot silently omit token cost or extend the resource set/bounds.

The operational record uses VP-02B's unchanged authority digest structure:
`binding` (the exact `AUTHORITY_BINDING_FIELDS`), `delegation`, `opt_in`,
`budget_authority`, `limits`, `allowed_paths`. The task envelope digest is
preserved from canonical admission, not recomputed as a replacement for Runtime's
digest. Record-byte digest, task blob and envelope identity remain separate.
The opt-in and budget Human must match delegation. Its actual canonical content
digest must match every checkpoint's `Binding.operational_authority_digest`.

Both complete proof contracts/mappings are authenticated and decoded through
VP-02. The admission's semantic digest normalizes **only** each condition's
candidate SHA to the fixed base, plus candidate-derived mapping contract/edge
digests to null after their existing validation. Everything else remains bound:
IDs/revisions, claims, acceptance, provenance, kind, blocking, conditions,
mapping edges, invariants, all VP-03A dimensions and BR gates. Candidate-specific
pins may vary only by this normalization. A new meaning, profile condition,
mapping strategy, comparison base or revision stops with BLOCK; it is not a
Runtime semantic remapping decision.

## Sealed history and independently attested facts

Each catalog checkpoint entry has exactly `checkpoint`, `feedback`, `facts`,
`contract`, `mapping`, `originals`. The first five are record identities;
`originals` is the finite list of independently cataloged VP-03A authorities for
the checkpoint's original mapped proof observations.

A sealed record has exactly `schema`, `binding`, `delegation`, `checkpoint`,
`sources`, `seal_digest`. `sources` contains exactly the entry's immutable
`feedback`, `facts`, `contract`, `mapping`, `originals` pins. The outer seal digest
is canonical SHA-256 of all these fields except `seal_digest`, so reservation,
consumption, proof-catalog and feedback identities cannot drift under the same
seal. The separately computed existing checkpoint content digest still binds
its original VP-02B fields; feedback and facts bind that inner digest, avoiding
self-reference. Successor predecessor links and current tips bind the **outer
sealed content digest**. The returned `SealedFact` preserves both identities.
The nested types and their content hashes are the published VP-02B `Binding`,
`Delegation`, `Checkpoint`, `EvidenceDeclaration`. The original checkpoint and
factual-feedback parsers are reused, including full proof population, immutable
condition digests, unique original evidence sources and all nonpassing feedback
obligations. Factual feedback contains no strategy, edit instructions, diagnosis,
new model selection or executable command.

The evaluator checks K1..Kn ordinals strictly `1, 2, ...`. K1 has no predecessor;
each successor binds the previous outer sealed content digest and known ordinal.
Every candidate is a distinct authenticated committed object, differs from the
admitted base and descends from the prior candidate using raw authenticated Git
parents. Siblings, gaps, rollback, reused base/candidate, forks and competing tips
cannot be accepted. Actual full-tree path/mode/blob differences are compared
with the fixed scope; missing, empty or independently unattested deltas stop.
No request-supplied path list establishes actual scope compliance.

The independent facts record has exactly:

```text
schema, checkpoint_digest, ordinal, candidate_sha, candidate_tree_sha,
committed, clean, observed_at, changed_paths, progress, risk, semantic_choice,
spent, reserved, accounting_state, effects_state,
consumed_feedback, causal_obligation_ids
```

Its seal/candidate/tree must match. Committed/clean facts are independently
attested and corroborated by actual committed objects; a caller checkpoint
boolean alone is insufficient. Risk/semantic-choice must be unchanged; complete
accounting and established effects are mandatory. K1 has INITIAL progress and
no consumed feedback/causal obligation. Every successor consumes exactly the
prior authenticated feedback identity once and has established nonempty scoped
progress tied to an actual complete stable failed **candidate** obligation.
Comparison failure alone does not supply this basis. This identity/fact check
does not choose HOW or adjudicate the semantic quality of the correction.

Cumulative spent counters never decrease. Corrections equal ordinal minus one;
wall seconds equal checkpoint observation time minus original admission time.
Each successor's charged delta is nonnegative, includes exactly one correction
and fits the predecessor's explicit reservation. Spent plus outstanding reserved
resources must fit the original finite Human limits at every seal and live
observation. Item accounting must cover all authenticated original populations,
including the already recorded base population of a comparison. Required proof
is never waived by budget. Reservations are not
reset on crash or replay. Accounting must include proof, infrastructure, cleanup,
checkpoint/applicability/evidence/feedback/re-entry and applicable token work,
as independently established by the trusted issuer; this evaluator neither
measures nor updates actual production accounting.

The current snapshot binds the same task/run/admission/operational/delegation,
PRIMARY/ACTIVE/no terminal, exact independently supplied `observed_at`, same LIVE
lease/expiration, unchanged risk/semantics, SEALED tail, ESTABLISHED effects,
COMPLETE accounting, monotonic spent, identical outstanding reservation, exactly
one latest seal digest in `current_tips` and the exact already consumed feedback
prefix. Missing/torn tail, uncertain effects/transport, missing tip or extra
unreconciled consumption is UNKNOWN. Changed authority, terminal state, expired
or lost lease, budget reset/overrun or competing tips is BLOCK. Absence is never
treated as zero cost or permission to retry.

## Original proof versus target applicability

There is **one** proof validity engine: the published
`proof_applicability.evaluate_applicability`. The module invokes it through each
independently pinned VP-03A catalog; it never reads a caller VALID as authority.
It preserves the original TASK/RUN/RESULT/EVIDENCE/raw/subject/command/outcome and
authenticates their exact source contract/mapping identity.

The `originals` catalogs use exact same-subject applicability to authenticate
each original checkpoint observation through that existing engine, including
complete stable phase/population quality and original comparison evidence where
required. They do not claim C1 executed on C2. These existing immutable original
RESULT/EVIDENCE snapshots are read as source observations; the module creates no
interim or terminal canonical RESULT, EVIDENCE or RUN status. Each checkpoint
evidence declaration must agree with the independently evaluated original;
a missing/forged original cannot be rescued by its declared validity label.

Each consecutive transition has exactly `source_ordinal`, `target_ordinal`,
`catalogs`. It must bind Ci to distinct Ci+1, preserve the source checkpoint's
exact original EVIDENCE/RESULT and supply a separate exact VP-03A applicability
witness for **every target obligation**. Source/target task/run/base,
contracts/mappings and actual trees must agree with the lineage. Duplicate or
collapsed witness/obligation coverage cannot discharge independent integration,
concurrency, ordering, worker or comparison obligations, including nonblocking
ones. All existing VP-03A dependency dimensions and BR-1..BR-6 remain mandatory.
No base materialization, collection, reproduction, broad fallback or proof
execution is performed.

Per-proof results remain VALID / INVALIDATED / UNKNOWN as returned by VP-03A.
An actual original FAIL stays FAIL and retains its exit code even when VALID.
Changed fixtures/helpers/profile/worker/integration conditions invalidate the
affected proof or leave it UNKNOWN. Missing or corrupt witnesses are UNKNOWN.
No acceptance aggregate, inferred target PASS, regenerated source proof or new
target execution EVIDENCE exists. INVALIDATED does not schedule a replacement;
UNKNOWN does not permit resolution beyond separately authorized future work.

## Factual result, replay and durable CAS boundary

`LineageResult` is immutable and always has `status=BLOCK` or `UNKNOWN`.
Consistency is expressed separately as `observation=CONSISTENT`, never permission.
`SAME_FACT` requires the exact already cataloged sealed `RecordIdentity` and a
still-consistent/current whole history. The returned seals, proof sources,
feedback and spent/reserved facts are unchanged by re-observation.

| Observation | Effect |
| --- | --- |
| Complete consistent history | Factual CONSISTENT; BLOCK / NOT_ACTIVATED |
| Exact duplicate sealed receipt | SAME_FACT with identical facts; no second consumption, correction, proof, append or terminal artifact |
| Different record/content at an ordinal, duplicate, fork or divergent replay | BLOCK / CONFLICT; no automatic repair or enrollment of another seal |
| Missing predecessor/tail, torn write, unknown crash effects or missing replay bytes | UNKNOWN / INCOMPLETE; preserve known facts without inventing a checkpoint or FAILURE |
| Changed current head/main | UNKNOWN / INCOMPLETE; immutable sources do not authorize a stale current action |
| UNKNOWN upstream applicability | Preserve the separate per-proof UNKNOWN and return UNKNOWN; no discharge |

Main and HEAD are checked before and after evaluation against independently
pinned expected values. HEAD must equal the last candidate, not the catalog's
metadata commit. These are bounded currentness observations. They provide no
repository lock, distributed lease, durable CAS, atomic append, ABA protection
or guarantee against movement immediately after the read. Git SHA equality alone
cannot guarantee atomicity.

Future separately admitted Runtime engineering must authenticate the external
issuer/currentness boundary and durably compare-and-swap the expected prior seal,
feedback consumption and reservation/charge facts together with terminal
exclusion. It must reconcile ambiguous execution effects before any write or
re-entry, use the original bounds and one sticky lease, and return the same
already durable fact on retry. This foundation implements none of those writes,
delivery mechanisms, polling loops, persistence stores or production adapters.

Every output authorization/mutation/creation flag is FALSE, including
continuation, feedback consumption, checkpoint append, evidence reuse,
acceptance discharge, proof execution, target EVIDENCE, terminalization,
scheduler activation and artifact persistence. `activation=NOT_ACTIVATED`.
The inherited six base gates are descriptive requirements, never replay approval.

## Terminal, REPAIR, REMEDIATION and deferred gates

Only the existing Runtime can establish exactly one mutually exclusive terminal
RESULT or FAILURE. A checkpoint FAIL is preterminal factual proof, not REVIEW,
CHANGES_REQUIRED or FAILURE. A terminal record cannot re-enter this lineage.
Partial/missing transport cannot fabricate a RUN or terminal artifact.
Canonical terminal FAILURE retains the existing AUTHOR_REPAIR route, separately
authored correction and a distinct newly admitted REPAIR RUN. REVIEW
CHANGES_REQUIRED retains narrow REMEDIATION and delta review. Neither operation
inherits this prospective PRIMARY loop. Reviewer and Publisher keep their
independent final-source judgment/publication boundaries.

[KA-01](AIOS-KERNEL-KA01-PROSPECTIVE-AMENDMENT.md) remains a published prospective
normative specification, not activation. All six D.1 gates remain conjunctive:
G1 Human ratification, G2 exact normative publication, G3 separately admitted
reviewed/published engineering, G4 actual VP-01/VP-06 measurement reconciliation,
G5 real VP-07 conformance, and G6 explicit prospective Human activation and finite
operational opt-in/delegation. This module establishes none of the later gates.

VP-01 remains DEFERRED_BY_HUMAN, not waived or DONE. VP-02 pending exits and VP-03
whole-package exit are not established. VP-04's single Runtime scheduler, VP-05
delivery/continuation, VP-06 measured whole-episode benefit and VP-07 real
AC-01..AC-36 conformance/rollback/cutover remain separate obligations. The current
V1/V2 command-driven `minimum-sufficient-v2` policy and frozen Kernel v0.1 remain
active and unchanged. There is no duplicate scheduler or simultaneous policy
execution. No roadmap advancement, review PASS, publication or production-ready
claim follows from offline consistency.

## Focused disposable conformance surface

`tests/test_proof_checkpoint_lineage.py` authors bounded real-Git cases for an
ACTIVE/null-head admission, C1->C2, hash-linked seals, exact replay/conflicting
duplicate, fork/gap/reused base/candidate, moved currentness, partial crash,
single feedback consumption, sticky Human/native/lease identity, finite
spent/reserved accounting, missing/forged source evidence, upstream UNKNOWN and
changed fixture/helper/profile/worker/integration dependencies. Separate
integration/concurrency/ordering/comparison witnesses and read-only effects are
explicit cases. Object-only variants share a fixture graph; no network or proof
execution occurs. These are authored offline conformance tests, not VP-07 real
execution evidence or production authority.

Runtime's focused command under the existing policy is:

```text
python -m pytest -q tests/test_proof_checkpoint_lineage.py
```

Runtime owns execution of that canonical verification and construction of
EVIDENCE. Executor-local parsing/smoke observations do not assert its outcome.
The candidate changes exactly this versioned document, the new module and its
focused tests. No production call sites, scheduler, historical artifacts,
TASK/parser/workflow or authoritative checkpoint/proof/RUN persistence are added.
