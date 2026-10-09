# VP-03C canonical proof handoff v1

Status: TASK-332 r1 bounded read-only foundation. **NOT_ACTIVATED**.

`src/aios_renew/proof_authority_handoff.py` specifies an exact handoff request,
the absent independent canonical source boundary, and a separate offline content
diagnostic. It creates no issuer, trust enrollment, Runtime integration,
canonical proof, target EVIDENCE, checkpoint, feedback receipt or lifecycle state.
Runtime owns canonical verification and EVIDENCE. Human owns delegation and risk,
Brain semantic obligations, Reviewer the independent verdict, and Publisher
publication. This document claims no readiness, verification PASS or roadmap exit.

## Independent issuer is a precondition

The published [VP-03A](AIOS-VP03-PROVENANCE-APPLICABILITY-v1.md) and
[VP-03B](AIOS-VP03-CHECKPOINT-LINEAGE-AND-REPLAY-v1.md) authenticate immutable
content **relative to a catalog**. Their concrete authority constructors do not
authenticate that catalog's issuer, Runtime admission, Human legitimacy or an
independent Reviewer. Anyone can manufacture a coherent local repository with
plausible-looking TASK/RUN, review, proof and checkpoint objects.

**BLOCKER: no independently established production canonical Runtime/source
producer for this handoff is installed.** The permitted three-file scope cannot
create that producer by changing existing Runtime. A real producer requires a
separately authorized, reviewed integration at the existing canonical Runtime
observation boundary. A fixture, Git ancestry, SHA, review-looking blob, local
path, self-certified permission or Python constructor cannot fill this gap.

Consequently `evaluate_handoff(request)` accepts **no** repository, authority,
catalog, issuer, expected-main, observation-time, callback or producer argument.
There is no registry, setter, environment configuration, dynamic adapter, issuer
key service or caller-selected trust anchor. The API performs no Git or source
reads: without the independent producer it returns factual **UNKNOWN**, with
`INDEPENDENT_CANONICAL_RUNTIME_ISSUER_UNAVAILABLE`. Duplicate selections return
**BLOCK** with that same residual. Every target's canonical state is UNKNOWN.
There is no positive canonical VALID branch in this version.

A future producer must independently deliver exact source/issuer legitimacy and
currentness from the existing canonical observation boundary, including authentic
admission, Human delegation/lease, source review and finite accounting. It must
establish repository identity and catalog pins outside the request. This v1 does
not guess an issuer, infer one from arbitrary Git objects, or install a temporary
production substitute. Supplying a plausible repository cannot change its result.

## Versioned, strictly decoded request

`decode_request(decoded)` returns immutable `HandoffRequest`. Both entry points
revalidate directly constructed wrappers before use. Only plain decoded JSON
objects/lists and exact primitive types are accepted. Unknown/missing fields,
wrong versions, shortened/ref-like SHAs, path traversal, boolean revisions,
non-JSON objects and exceeded bounds raise `HandoffInputError`. There is no YAML,
file loader, arbitrary duck-typed source or untrusted object hook.

The root has exactly:

```text
schema, admission, source, target, originals, obligations, replay
```

`schema` must be `canonical-proof-handoff-v1`. Nested fields are:

| Object | Exact fields |
| --- | --- |
| admission | task, task_id, task_revision, task_envelope_digest, run, run_id, base_sha, admission |
| source / target | candidate_sha, tree_sha, checkpoint, contract, mapping, contract_pin, mapping_pin |
| each original | proof_id, proof_digest, result, evidence, raw |
| each target obligation | obligation, source_proof_id, witness |
| each record identity | commit_sha, path, blob_sha, sha256 |
| each proof/obligation pin | id, revision, digest |

Record identities use the published VP-03A decoder: exact lowercase full 40-character
hex Git IDs, bounded relative paths and lowercase SHA-256 of record bytes.
Proof pins require positive integer revisions and exact digests. `proof_digest`
identifies the original full proof descriptor, including conditions/provenance.
`replay` is null or one exact record identity; it requests inspection only.
Source observations and target obligations are nonempty bounded populations.

These fields **select and bind requested facts**; they attest no admission,
issuer, acceptance verdict, validity or permission. Repository identity, expected
main, trusted issuer, catalog source, Human/Runtime/Reviewer authority, outcome,
acceptance PASS and caller-approved VALID are deliberately absent from the
request schema. The request digest is a deterministic selection identity, never
a receipt, authorization, signature or replay-consumption token.

```python
request = decode_request(decoded_request)
facts = evaluate_handoff(request)
# facts.status == "UNKNOWN"; independent producer is absent.
# facts.issuer_authenticated is False; all target states are UNKNOWN.
```

## Separate offline diagnostic

`inspect_content_handoff(request, fixture_authority=...)` returns the distinct
`canonical-proof-handoff-content-v1` schema. Its concrete
`ReadOnlyLineageAuthority` parameter selects **offline content only**. It is not
a canonical source input, cannot enroll trust, and is never consumed by
`evaluate_handoff`. An arbitrary duck-typed object or missing fixture yields
UNKNOWN. Output cannot be decoded as another request or activated as a receipt.

This diagnostic demonstrates the bounded handoff shape while making the issuer
gap explicit. Even an entirely coherent fixture retains `issuer_state=UNAVAILABLE`,
`producer_state=NOT_INSTALLED`, `issuer_authenticated=False`, UNKNOWN canonical
target states and the mandatory issuer residual. No fixture is promoted to live
authority. Nested VP-03 `authority_authenticated` / `history_authenticated` facts
describe only content authentication within that fixture's catalog; they do not
authenticate its external issuer. Factual CONTENT_CONSISTENT is not authorization.

The diagnostic calls published `evaluate_lineage` exactly once. VP-03B alone
invokes `evaluate_applicability`; the handoff neither repeats those evaluations
nor executes proof. The complete immutable `LineageResult` is preserved, with its
original observations, checkpoint order, seals, feedback records, Human/native
delegation, lease and finite spent/reserved resources. Costs are never reset.
Partial history is retained for diagnosis, never projected as complete coverage.

For a complete history, the handoff selects the final adjacent source/target
checkpoints, whose target is the catalog's exact current candidate. It matches:

- Exact TASK record/revision/envelope, admitted RUN record/ID/base and admission
  record against the published authenticated history. The diagnostic's ADMITTED
  label describes the fixture's decoded admission; canonical admission stays
  UNKNOWN in `evaluate_handoff` when the independent source is missing.
- Both committed candidates/trees, checkpoint records and exact source/target
  contract/mapping records and versioned pins. There is no movable-ref proof
  identity or unrelated Git object approval.
- The entire original proof population, each descriptor digest and unchanged
  original RESULT/EVIDENCE/raw records. Different proofs cannot alias an original
  EVIDENCE; one original RESULT may legitimately refer to multiple EVIDENCE.
- Every target obligation, its exact explicit VP-02 mapping edge and descriptor,
  original source proof, applicability catalog, review and immutable witness.
  Witnesses and target slots must be unique; omitted obligations fail closed.

Only the existing [VP-02](AIOS-VP02-PROOF-COVERAGE-CONTRACT-v1.md)
`validate_proof_mapping` parses coverage semantics. Its expected mapping pin is
the checkpoint's already validated binding, not a caller-recomputed trust claim.
There is no filename/nodeid classifier, implicit semantic subsumption, scheduling
engine, extra acceptance aggregate or new validity algorithm.

## Per-obligation facts and original outcomes

Each `TargetFact` retains its selection, full immutable `ProofObligation`, exact
mapped `ProofDescriptor`, original `OriginalObservation`, and the **verbatim**
published `ApplicabilityResult` in `content_applicability`. No source record is
rewritten, relabeled or replaced with target execution evidence.

| Field | Meaning |
| --- | --- |
| TargetFact.state | Canonical applicability; UNKNOWN because no independent issuer is installed. |
| content_applicability.state | Original VP-03A VALID / INVALIDATED / UNKNOWN relative to the fixture catalog. |
| content_applicability.witness | Original immutable source-to-target witness, or absent when authentication could not establish it. |
| original.outcome / exit_code | Original observed PASS/FAIL and exit code, including an applicable original FAIL. |

VALID source-relative applicability **never turns FAIL into PASS**. The original
subject/tree/command, TASK/RUN, RESULT/EVIDENCE/raw, proof identities and outcome
remain distinct from the target candidate/witness. UNKNOWN facts may retain an
authenticated original observation while lacking an authenticated witness.
Neither an original PASS nor a relative VALID discharges an acceptance criterion.
No aggregate acceptance PASS is produced.

Every target contract entry survives individually, in contract order, including
entries sharing an acceptance ID or explicitly mapped proof. Blocking status,
claim, kind, acceptance population and all condition fields remain intact:
integration, serial/parallel workers, concurrency, ordering, profile, fixtures,
helpers/shared state, environment/toolchain, population/collection, mandatory
comparison/base identity and repetition obligations. The published complete
dependency dimensions, including policy, remain in each immutable witness.
Missing/conflicting mapping, incomplete footprints or unsupported contexts remain
UNKNOWN/BLOCK; they never become unchanged, waived or discharged.

## Separate currentness, replay and effects

`CurrentnessFact` records the fixture catalog's expected main/head, actual fixed
`refs/heads/main` and `HEAD` reads before and after the diagnostic, exact current
snapshot record, and the fixture's observation time. CONSISTENT here means only
matching observed refs; live admission/lease/accounting checks remain VP-03B's
separate facts. No request chooses currentness and no local clock invents it.
An unavailable/moved/torn ref makes the handoff UNKNOWN and removes the complete
target projection. Partial content facts remain visibly diagnostic.

Missing/corrupt sources, forged/extra issuer fields, conflicting catalog pins,
swapped TASK/RUN/evidence, altered mapping/conditions, stale main/head/lease,
competing tips, duplicated seals, feedback drift, budget resets and torn effects
all fail closed through the existing validators and exact handoff bindings.
Repeated delivery reads the same facts. Cataloged replay preserves SAME_FACT;
uncataloged/conflicting replay BLOCKs and missing/torn replay is UNKNOWN. Neither
creates a second checkpoint, consumes feedback again, reserves budget, mutates
history, repeats proof or grants continuation. There is no persistent receipt
cache or anti-replay authority in this module.

Before/after reads provide no atomic CAS, durable currentness or ABA protection.
True issuer authentication, live lease ownership and atomic consumption/currentness
must be established at the future independently reviewed Runtime boundary.
Read-only diagnostic consistency cannot substitute for those gates.

Every exposed effect/permission flag emitted by this module stays false:
issuer authentication, authorization, Runtime continuation, lifecycle mutation,
checkpoint append, feedback consumption, acceptance discharge, evidence reuse,
verification execution, terminalization, scheduler/publisher activation, target
execution evidence, canonical checkpoint creation, persistence and budget
reservation. They are immutable non-constructor fields; `activation` remains
NOT_ACTIVATED. Nested published results retain their original false authorization
flags. All BR-1..BR-6 gates remain mandatory for any later base replay; none is
proposed or performed here.

## Bounds and remaining gates

Each request has at most 32 original and 32 target selections, a 1 MiB canonical
JSON bound, 40,000 nodes and depth 20. Published VP-03B retains at most 17
checkpoints and 32 applicability evaluations. Each authenticated reader retains
VP-03A's 1 MiB object, 8 MiB total bytes, 256 objects/ancestors, 512 Git calls and
5-second per-call timeout bounds. The wrapper adds one reader for catalog,
coverage and projection reads; it does not retry proof evaluation. The total is
bounded by the wrapper reader, one VP-03B reader and at most 32 VP-03A readers.
Trusted local Git/storage are preconditions. Reads disable lazy network fetch,
replacement objects and inherited Git overrides through the published reader.

`tests/test_proof_authority_handoff.py` shares only the minimal disposable
real-Git fixture helpers from VP-03B. Its immutable specimen objects exercise
independent-root absence, coherent fake repositories, forged issuer/catalog,
strict decoding, exact source/admission/subject identity, original FAIL, mixed
relative applicability, mapping/condition drift, distinct obligations,
stale/torn currentness, duplicate delivery and replay/crash/accounting negatives.
Evaluation checks permit only local Git read commands and compare repository
bytes before/after. Tests issue no network, live execution, production admission
or authoritative checkpoint/persistence writes. Runtime alone owns this TASK's
focused canonical verification; these fixtures are not canonical EVIDENCE.

Separate future authorization is required for the real producer and its issuer
legitimacy/currentness integration. VP-04 must establish the single Runtime proof
engine and lawful reuse/discharge/scheduling. VP-05 retains separate correction,
attribution and Runtime/CAS/consumption gates. VP-07 must establish real production
conformance (AC-01..AC-36) and explicit Human cutover after the other gates.
VP-06 measurements, VP-01 deferred-not-waived obligations, whole VP-02/VP-03 exits,
and [KA-01](AIOS-KERNEL-KA01-PROSPECTIVE-AMENDMENT.md) G1..G6 remain open.

The frozen Kernel, Constitution, current minimum-sufficient-v2 policy and legacy
routes remain operative. No automatic promotion to VP-04 or roadmap advancement
follows. This delta contains only this versioned document, the bounded handoff
module and its focused tests; production Runtime/Kernel/workflows/contracts,
canonical history and downstream pins are untouched.
