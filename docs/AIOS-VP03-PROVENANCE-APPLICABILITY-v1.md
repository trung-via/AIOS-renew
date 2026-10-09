# VP-03A canonical provenance and factual applicability v1

Status: bounded TASK-330 r1 implementation foundation. **NOT_ACTIVATED**.

This document specifies `src/aios_renew/proof_applicability.py`. It establishes
read-only source authentication and a factual source-to-target comparison. It
creates no Runtime integration, verification policy, admission status, permission,
checkpoint, feedback, lifecycle transition or production evidence reuse authority.
Runtime remains the canonical verification owner. Brain/Reviewer own semantic
proof adequacy; Publisher owns publication. This document makes no verification,
review, publication, performance or roadmap-completion claim.

The implementation reuses `proof-coverage-v1` / `proof-mapping-v1` from
[TASK-326's foundation](AIOS-VP02-PROOF-COVERAGE-CONTRACT-v1.md), and retains every
dependency dimension and BR gate from
[TASK-328's declaration](AIOS-VP02-INTRA-RUN-PROOF-AND-KERNEL-GATE-v1.md).
Policy is an additional explicit dependency dimension. These foundations remain
unchanged: their opaque provenance references still declare rather than
authenticate authority. Their contracts are decoded by their existing validators.
The existing canonical TASK, RUN, RESULT, EVIDENCE and V2 observation decoders are
also reused; no historical parser or status is extended.

## Trust boundary: authenticated content versus asserted authority

Git existence, equal digests, a PASS label and a review reference do not establish
canonical execution provenance. A caller can create an entire repository of
self-certified records. **An untrusted caller must never select the authority**.

The v1 interface separates two inputs:

1. The untrusted request identifies an immutable source EVIDENCE, an immutable
   witness, a source proof and an exact target obligation/candidate.
2. A trusted canonical-source boundary supplies a `ReadOnlyAuthority`: a trusted
   local repository location and an independently delivered exact catalog record
   identity. That catalog commits to the canonical admission/TASK binding,
   original RUN/RESULT/EVIDENCE/raw records, independently reviewed complete
   source and target proof mappings, complete dependency facts, and the separate
   applicability witness. It also pins the expected current main.

The authority catalog is not a request field. There is no caller `trusted=True`,
`approved`, `review=PASS`, changed-file selector, nodeid selector or declared
validity input. An unavailable authority returns UNKNOWN. Request attempts to
select another source/witness/target fail against the independent catalog.
Even a catalog-pinned witness must agree with the separately pinned review and
facts and the independently read Git objects; pinning its digest alone is
insufficient.

The trusted boundary must obtain its catalog pin from actual canonical source
authority, including the legitimacy of the reviewed closure and the original
observation. This module does **not** authenticate a Human/Reviewer signature,
discover a canonical root, confer review authority, or prove that an arbitrary
Python constructor argument came from Runtime. Passing an attacker-selected
`ReadOnlyAuthority` violates the interface's trust precondition. Constructor
availability is dependency injection, not a trust enrollment API. The returned
`authority_authenticated` describes authenticated content/provenance *within that
independent trust root*, not authentication of the root's external issuer.
No production caller currently supplies such a boundary. Future integration must
establish it independently; missing or untrustworthy authority must remain
UNKNOWN/BLOCK. Disposable test authorities exercise this boundary with offline
fixtures; they are not actual production observations or Reviewer verdicts.

## Versioned decoded interface

| Input / output | Version or type |
| --- | --- |
| Request and immutable factual result | `proof-applicability-v1` |
| Trusted catalog | `proof-applicability-authority-v1` |
| Independently reviewed applicability mapping | `proof-applicability-review-v1` |
| Separate applicability witness | `proof-applicability-witness-v1` |
| Canonical dependency-context snapshot | `proof-dependency-context-v1` |

`decode_record_identity(value)` accepts exactly `commit_sha`, `path`, `blob_sha`,
and `sha256`. Commits/blobs are full lowercase 40-character Git object IDs; the
digest is lowercase SHA-256 of the actual record bytes. Paths are bounded exact
repository-relative paths, with no traversal. A ref, path expression, shortened
SHA, arbitrary label or digest of caller-supplied content is not a source identity.

`decode_request(value)` accepts exactly:

```text
schema, repository_id, evidence, witness, source_proof_id,
target_candidate_sha, target_obligation
```

The last field is an exact `id`, positive `revision`, `digest` proof-obligation
pin. Decoders accept only plain decoded dictionaries/lists and exact primitive
types; extra/missing fields, duplicate identities, unsupported versions,
booleans as revisions, non-JSON objects and exceeded bounds are rejected.
Malformed requests raise `ApplicabilityInputError`. Direct construction of a
request cannot bypass revalidation. These are not new V1/V2 TASK decoding states.

```python
request = decode_request(decoded_request)
# Only a trusted canonical-source boundary may supply this independent pin.
authority = ReadOnlyAuthority(trusted_repository, trusted_catalog_identity)
facts = evaluate_applicability(request, authority=authority)
```

The evaluator accepts the concrete immutable authority type, not arbitrary
callbacks or a caller-defined provenance verifier. It reads bounded local Git
objects and committed UTF-8 JSON snapshots of decoded canonical records. Raw
logs remain bytes. JSON rejects duplicate keys. Existing YAML artifacts are
untouched; no YAML/file ingress or live source adapter is installed.

## Catalog, review and immutable witness

The catalog contains exactly:

```text
schema, repository_id, expected_main_sha, task_binding, base_sha,
source_candidate_sha, target_candidate_sha, source_proof_id, target_obligation,
task, run, result, evidence, raw, source_contract, source_mapping,
target_contract, target_mapping, review, witness
```

Each named record is an exact `RecordIdentity`. `task_binding` is the existing
proof-coverage TASK binding (`task_id`, `revision`, `envelope_digest`,
`acceptance_ids`) obtained from canonical admission authority. Its identity,
revision and full acceptance population must match the decoded canonical TASK,
both proof contracts and original V2 evidence binding. The envelope digest is
preserved from that authority; this module does not invent a replacement for
the active Runtime's admission digest. Record-byte SHA-256 is a separate identity.

The module independently checks:

- Object types, existence, actual Git object hashes and record-byte SHA-256;
  record paths resolve to the pinned regular-file blobs at the pinned commits.
- Authenticated raw commit parents: task source precedes the base, current-main
  pin precedes the base, base precedes the original candidate, and the original
  candidate is an ancestor of the exact target. The catalog includes that target
  and every referenced record in its authenticated history. Original observation
  records follow the original subject; target mapping/review/witness and context
  records follow their respective subjects. A sibling/foreign lineage is UNKNOWN.
- Exact canonical RUN/TASK/base/head, RESULT head and claim references, original
  EVIDENCE RUN/subject/raw path, V2 candidate digest, original tree, command,
  profile/toolchain and admitted TASK envelope. Reused/relabelled or early-probe
  records cannot impersonate an original proof.
- Complete, stable canonical phase outcomes and population; skipped, unstable,
  incomplete or independently conflicting observations cannot produce VALID.
  The reviewed source-observation binding also commits to the actual evidence
  digest, complete/stable/nonconflicting facts, observed count and actual outcome.
- Both complete reviewed proof contracts/mappings through TASK-326's existing
  validators. One selected target obligation must have an explicit adequate
  proof mapping and the source proof must actually cover its semantic claim.
  Comparison source proofs additionally need complete comparable authenticated
  base observations already present in their source record; none are executed.

The review has exactly `schema`, `task`, source/target contract and mapping pins,
`source_proof_id`, `source_proof_digest`, `target_obligation`, `target_proof_id`,
`source_observation`, and `dimensions`. The source descriptor digest includes its
full versioned conditions and provenance; foundation contract/mapping/obligation
digests retain their existing normalization and version/revision rules.

The separately committed witness includes that same proof binding, repository
identity, the original task/run/result/evidence/raw record identities, review
identity, both exact candidates/trees, admitted base and all dimension
comparisons. Its identity is distinct from the original EVIDENCE and the review.
All bindings must agree with the independent records. Altered TASK revision,
proof mapping, source record digest, tree, subject or target lineage is UNKNOWN.

The result preserves the original task, RUN, RESULT, EVIDENCE, raw record,
subject/tree, command, exit code and outcome in `OriginalObservation`. A separate
`ApplicabilityWitness` binds applicability to the exact target. No source is
relabelled as having executed on that target and no target EVIDENCE is emitted.

## Full dependency comparisons

Every dimension must have an independently reviewed plan and a witness
comparison. Omissions never default to unchanged:

```text
test_code, fixtures, helpers, shared_state, toolchain, environment, profile,
worker_mode, concurrency, ordering, collection, integration, repetition,
population, evidence_schema, policy
```

Fixtures include setup/teardown; population and collection include selection,
parameterization and observer behavior. Helpers/shared state retain the
foundation's shared-state condition identity and independent measured facts.
Policy is explicit even though the older condition schema has no policy field.

Each dimension plan has exactly `dimension`, `coverage`, `basis`, `paths`,
`source_context`, `target_context`. A COMPLETE plan commits to the **entire
reviewed relevant closure**, not a best-effort path list. Code, fixtures, helpers
and shared-state closures require actual explicit file dependencies. V1 supports
regular tracked files with measured blob identity/mode; missing source files,
directories, symlinks, submodules and unsupported observers are UNKNOWN.
A target deletion is a measured change, not an unchanged dependency.

Context snapshots have exactly `schema`, `dimension`, `subject_sha`, `proof_id`,
`evidence_digest`, `conditions`, `facts`. The source context binds the exact
original EVIDENCE digest; a target context has no executed target evidence digest.
Both bind their exact candidate/proof and the dimension's full condition
projection. Nonempty independently authenticated facts cover external state,
environment, policy, toolchain, collection and population without inspecting or
executing the host environment. Source profile/toolchain must match canonical
observations. Worker conditions must also match the actually observed explicit
worker mode/count; absent comparable worker facts remain UNKNOWN.

INAPPLICABLE is accepted only for fixtures/helpers/shared state, with an
independently reviewed basis, no file dependencies, affirmative inapplicable
facts at both subjects, and no nonempty source fixture obligation. It cannot
erase integration, ordering, concurrency, environment or actual worker
distinctions. Conditions/facts are still compared. UNKNOWN or unsupported
coverage is UNKNOWN, never a waiver.

For each dimension, the evaluator recomputes a canonical SHA-256 over its exact
condition projection, authenticated factual context and independently read
Git path/mode/blob identities. Both witness digests must match. A changed
filename, unchanged nodeid, disjoint diff or command equality supplies no
semantic closure. Relevance comes from the independently authenticated reviewed
mapping; the evaluator contains no filename classifier or automatic resolver.
Unknown/unreviewed footprints cannot become VALID merely because paths match.

## Three factual states and preserved outcome

| State | Meaning |
| --- | --- |
| VALID | All source authority/provenance, mappings, lineage, quality and complete dimension comparisons authenticate and preserve conditions. |
| INVALIDATED | Complete authenticated comparisons establish a relevant dependency/condition change. |
| UNKNOWN | Required authority, lineage, mapping, quality, footprint or witness facts are missing, inconsistent, unsupported or exceed bounds. |

Reasons are immutable typed `ReasonCode` values with the affected dimension
where applicable. Forgery/incompleteness dominates a partial changed comparison:
it cannot be presented as a fully authenticated witness. Results are deterministic
and idempotent for the same immutable records and observed currentness. Main is
checked before evaluation and again before returning applicability. Movement
fails closed. These checks do not provide an atomic lease/CAS against concurrent
or ABA movements; durable currentness and consumption belong to later work.

`applicable_obligation` identifies **only** the selected exact mapped obligation
when VALID. A VALID original PASS can be applicable to that adequate obligation;
a VALID original FAIL remains **FAIL**, including its original exit code.
INVALIDATED/UNKNOWN have no applicable obligation. The output has no inferred
target outcome and never treats FAIL as PASS. Every authorization/creation flag
is false, including acceptance discharge, evidence reuse, execution, target
execution EVIDENCE and checkpoint creation. Applicability is not an acceptance
aggregate or a scheduling plan.

The complete target contract is bound in the witness. Its independent integration,
serial/parallel, concurrency, ordering, repetition and mandatory comparison
obligations survive even when they share an acceptance ID. One narrow green
cannot discharge their conjunction. There is no duplicate proof execution,
automatic failure reproduction, base materialization, collection, narrow probe
or broad replay. Every later base replay remains subject to **all BR-1..BR-6**:
approved necessity, decision relevance, no valid alternative, minimum lawful
scope, exact comparability and authority/cost bounds. This API proposes none.

## Bounds, tests and non-activation gates

| Resource | v1 bound |
| --- | --- |
| Individual Git object / decoded record bytes | 1 MiB |
| Total authenticated object bytes per evaluation | 8 MiB |
| Objects / ancestors in a lineage traversal | 256 each |
| Git read calls / per-call timeout | 512 / 5 seconds; no retries |
| Decoded nodes / nesting depth | 40,000 / 20 |
| Reviewed paths per dimension | 64 |
| Identity / path characters | 128 / 512 |

Git reads use full OIDs, recompute hashes, ignore replacement refs, traverse raw
parents without graft/commit-graph ancestry shortcuts, disable lazy fetching and
inherited Git overrides, and never use checkout, fetch, update-ref or execution
commands. Object sizes are checked before content capture. There is no network,
pytest subprocess, host environment discovery, scheduler import/call or write
operation in the evaluator. Trusted local Git and repository storage are part
of the source boundary. Unsupported SHA-256-format Git repositories require a
future decoder; full v1 Git IDs are SHA-1.

`tests/test_proof_applicability.py` builds bounded disposable repositories with
real commits, trees, blobs and immutable record provenance. Negative cases cover
wrong source, spoofed pins/digests, missing/forged witnesses, corrupt/incomplete
observations, missing/conflicting mapping, main movement (including mid-read),
conflicting real parent lineage, replacement refs, unknown closures, changes in
every dimension, independent external facts, actual worker mismatch and narrow
versus integration/concurrency/ordering/comparison obligations. They use offline
canonical fixtures; no Runtime production scheduler or actual proof execution is
invoked. The admitted minimum-sufficient-v2 canonical verification remains owned
by Runtime, with the original single minimum-sufficient command unchanged.

VP-03B must independently admit and establish durable authenticated checkpoint,
history/currentness and consumption semantics. VP-04 must independently admit
the single Runtime-owned proof engine and actual reuse/scheduling authority.
VP-05, VP-06 and VP-07 retain their separate correction, measurement and real
production-conformance responsibilities. No live cutover or full VP-03/VP-02
closure follows from this foundation.

[KA-01](AIOS-KERNEL-KA01-PROSPECTIVE-AMENDMENT.md) is prospective ratified design
only; its **NOT_ACTIVATED** status and G1..G6 engineering, independent review,
publication, actual measurement, real AC-01..AC-36 conformance and explicit Human
activation gates remain in force. VP-01 stays **DEFERRED_BY_HUMAN**, not DONE or
waived. This slice supplies no VP-01 measurement, VP-06 savings or VP-07 cutover
evidence and starts no later package or roadmap transition.

The Manifesto, Constitution, frozen Kernel v0.1/freeze, historical artifacts,
current TASK parser, minimum-sufficient-v2 scheduler and legacy correction and
admission policy remain unchanged. Legacy remains the default until separately
authorized prospective activation. The supplied repository-default return route
is no origin-affine proof and creates no downstream default or dependency-pin
change. This candidate adds only the module, its focused tests and this document;
it creates or mutates no canonical proof, RESULT, EVIDENCE, RUN, checkpoint,
feedback, review, permission or lifecycle artifact.
