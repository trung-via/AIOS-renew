# Lean VP L1 semantic proof intake

TASK-376 adds a bounded, non-operative declaration validator. It is an actual
L1 engineering seam, not the complete L1 exit, a new production TASK version or
KA-01 activation. KA-01 remains **NOT_ACTIVATED**. VP-01 measurements and any
comparative savings remain **UNKNOWN**, outstanding rather than waived.

## Explicit interface and exact inputs

`task.validate_semantic_proof_intake(data, *, expected_task,
expected_candidate_sha, expected_sources, expected_contract, expected_mapping)`
delegates to `proof_coverage_contract.validate_semantic_proof_declaration`.
Both APIs accept only decoded plain dictionaries/lists and return the same
immutable `SemanticProofIntake`, or raise `ProofCoverageError`. All five caller
arguments are required, including separate contract and mapping pins. There is
no file reader, textual TASK decoder, source resolver or implicit default.
Text, paths, callbacks, Task instances and caller-declared trust flags are not
accepted as intake documents. A future transport must reject duplicate textual
mapping fields before decoding; a Python dict cannot preserve overwritten keys.
Duplicate identities/entries within decoded declarations are rejected here.

The intake root has exactly these fields:

| Field | Exact declaration |
| --- | --- |
| `schema` | `task-semantic-proof-intake-v1`, the only supported intake version |
| `task` | `task_id`, positive bounded `revision`, `envelope_digest` and the complete `acceptance_ids` set, using the existing VP-02 TASK binding |
| `candidate_sha` | Exact lowercase 40-character SHA; every obligation must have this candidate |
| `sources` | Exactly `task`, `contract`, `mapping`; each contains exactly `source_sha` and `blob_sha`, both lowercase 40-character Git identities |
| `contract` | Complete `proof-coverage-v1` decoded declaration |
| `mapping` | Complete `proof-mapping-v1` decoded reviewed-mapping declaration |

`expected_task` supplies the independently established exact TASK binding,
including the full acceptance set and envelope digest; a partial acceptance
selection is not a substitute. `expected_candidate_sha` supplies the exact
current candidate. `expected_sources` supplies the separately pinned source
commit/blob identities in the same closed shape as `sources`.
`expected_contract` and `expected_mapping` each contain exactly `id`, `revision`
and canonical SHA-256 `digest`. They are separate independent pins, not values
inferred from an incoming contract, its mapping or its review labels.

All pins and source identities must come from a later trusted caller's
authenticated, current canonical TASK/source/review context. This implementation
has **no such production integration**: it checks equality and contradictions,
not Git object existence, commit/blob membership, bytes at those identities,
present-day freshness, Human authority or a reviewer's approval of the content.
The TASK envelope digest and normalized contract/mapping digests bind declared
content; the supplied source-to-content associations remain caller obligations.
A coherent fabricated document with matching fabricated pins can be structurally
valid. No boolean, hash, opaque reference or `STRUCTURALLY_COMPLETE_ONLY` result
can replace authentication or semantic review.

## Coverage, bounds and output

The existing `validate_proof_mapping` is the sole coverage/mapping algorithm.
It requires complete TASK acceptance coverage, exactly one explicit entry per
obligation, exact obligation revision/digest and matching semantic claims, kind
and full conditions. Explicit shared proofs remain permitted only under the
existing reviewed equal-condition rules; this is not a one-proof-per-acceptance
shortcut. Every distinct fixture, shared state, worker/profile, concurrency,
ordering, integration, repetition, toolchain/environment, population/collection
and evidence condition is retained. Every contract, obligation, mapping and
proof provenance record is preserved. Equal population counts cannot erase a
different condition or substitute an isolated proof for integration proof.

Existing VP-02 bounds apply: at most 256 acceptance IDs, obligations, proofs,
entries and total mapped claims; 64 fixtures per condition; 128-character ASCII
IDs/references; 2,048-character single-line semantic claim text; positive
revisions through 2,147,483,647; 1–100,000 population items and 1–256 workers
(serial requires one). Exact schema shapes bound nesting. Malformed, missing,
extra, duplicate, stale, swapped, cross-TASK/source or incompatible declarations
raise; there is no fallback to another version. The new intake also rejects
recognizable command-shaped semantic text/identities and shell substitution.
That lexical rejection is not proof of semantic adequacy. Nothing is evaluated
as a command, and no proof description or coverage relationship is guessed.

The frozen output contains the intake schema, candidate SHA, immutable
`IntakeSources`, separate `DeclarationPin` contract/mapping identities and the
complete existing `CoverageDeclaration`. All unordered acceptance, obligation,
fixture, proof, claim and entry declarations normalize through VP-02, so
equivalent declaration orders produce equal output and digests. Execution order
remains the explicit `ordering_ref`. No input collection is retained mutably.

`readiness` is always `STRUCTURALLY_COMPLETE_ONLY`.
`unestablished` explicitly lists source authentication/currentness,
author/review authority, semantic adequacy, evidence validity and actual
applicability. `canonical_verification` is `NOT_PERFORMED`;
`production_eligible` and `execution_authorized` are always false.
These are fixed output descriptions, not accepted authority assertions.

## Compatibility and remaining engineering

`parse_task` and `validate_task` keep their existing Task shape, V1/V2 policy
selection, rejection behavior, opaque command order and return affinity. They
never call this API or accept semantic reference fields. The intake schema is
not a verification policy, is not registered in `VERIFICATION_POLICIES` and
cannot be placed on a production TASK as a newly admitted version. Historical
opaque `verification.required` strings retain their existing parsing semantics;
this API does not reinterpret them as proofs or make them safe to execute.
The original VP-02 public validators retain their behavior and signatures.
No Runtime/verification caller, scheduler, proof invocation, I/O, clock lookup,
cache, lifecycle authority, ledger or historical mutation is added.

The remaining boundaries follow the [Lean VP plan](AIOS-LEAN-VP-INTELLIGENT-INTRA-PRIMARY.md),
[L1 baseline](AIOS-LEAN-VP-L1-BASELINE.md) and
[KA-01 v1.1](AIOS-KERNEL-KA01-PROSPECTIVE-AMENDMENT.md):

- **Remaining L1:** authenticated canonical source/currentness and reviewed
  mapping integration, independently admitted future production schema/policy
  and a complete readiness core. Full L1 and measured VP-01 are not complete.
- **L2 / VP-03:** authenticated original-subject evidence and full source-to-target
  applicability witnesses with conservative VALID/INVALIDATED/UNKNOWN handling.
  Structural declaration equality supplies neither real evidence nor that witness.
- **L3 / VP-04/05:** one Runtime scheduler, immutable checkpoints, actual proof
  coverage, bounded same-Executor feedback/ACK, consent/resource accounting and
  crash/replay/lease/stop/terminal guards. This intake supplies none of that authority.
- **G3:** actual eligible L1–L3 core implementation, focused independent Runtime
  safety verification, exact-source semantic review and publication remain
  necessary. This seam and its synthetic fixtures cannot establish the full gate.
- **G6:** after lawful G1/G2 and actual G3, authenticate exact Human future
  version/policy/profile choice and finite per-RUN opt-in with rollout/rollback.
  Roadmap priority and validated declarations cannot grant that opt-in.
- **L4 / G4/G5:** VP-01/VP-06 matched real measurements and full real VP-07
  positive/negative conformance remain mandatory unproved postactivation
  obligations under v1.1; they are not fabricated prerequisites or measured wins.

Brain retains proof intent; the admitted Executor implements; Runtime alone
owns canonical verification/EVIDENCE; Reviewer judges semantic sufficiency;
Publisher owns publication; Human retains operational selection and consent.
All regression source/review identities are explicitly synthetic. This change
claims no production activation, savings, future RUN authorization, downstream
adoption or automatic roadmap advancement.
