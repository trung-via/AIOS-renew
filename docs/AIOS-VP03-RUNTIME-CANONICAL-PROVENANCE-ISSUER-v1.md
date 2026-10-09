# VP-03D Runtime canonical provenance issuer/consumer bridge v1

Status: TASK-333 r1 read-only bridge. **NOT_ACTIVATED**. This document asserts
neither canonical verification PASS nor a VP-03 exit.

`runtime_provenance_bridge.py` connects the VP-03C canonical handoff to the
existing Runtime source boundary. `runtime.py` exposes the bounded non-mutating
`observe_canonical_provenance_availability()` entry point. It takes no arguments,
does no I/O, and changes neither `RuntimeCompletion.complete` nor any admission,
verification, transport, terminalization or publication behavior.

## Independent issuance and the supplied contract limit

Real independently sourced terminal provenance requires an existing, independently
authenticated Runtime admission/terminal issuer and independently submitted
Reviewer source, with exact raw bytes and authoritative main/publication
observations. The Runtime/transport code inspected for this delta supplies
canonical lifecycle **content**, but exposes no independent producer attestation
for the VP-03 bridge. Its upstream resolver starts from repository configuration;
its remote lifecycle snapshot is a value object, not a producer credential.
The existing RESULT transport packages RUN/RESULT and optional subordinate
artifacts in an isolated commit; it does not issue the raw verification blobs.

A `RuntimeCompletion` constructor, committed Git history, configured upstream,
remote-looking ref, TASK/RUN/REVIEW-shaped value, SHA or fixture catalog cannot
independently authenticate its own legitimacy. Selecting the installed module's
checkout as a trusted root would also add an unsupported trust assumption.
There is no second registry, key/signature service, setter, environment override,
caller-provided producer/callback, trust-enrollment constructor or persistent store.

**Contract limit:** the requested positive, real independently authenticated
terminal issuer cannot be established from these existing exposed boundaries.
Creating an attestation or transporting missing raw sources would require an
external source-issuance gate or changes to admission/transport beyond the
permitted delta. This implementation does not claim that a conditional fixture
closes that gap. The canonical route therefore reports typed UNKNOWN with
`INDEPENDENT_CANONICAL_RUNTIME_ISSUER_UNAVAILABLE` and
`INDEPENDENT_REVIEWER_ISSUER_UNAVAILABLE`. A spoofed returned Runtime observation
reports BLOCK with `UNTRUSTED_RUNTIME_SOURCE_OBSERVATION`; duplicate selections
also BLOCK. It never asserts authenticated producer rights. No real positive
source is fabricated, and no legitimate terminal artifact is rewritten.

## Versioned immutable selection and content facts

`decode_selection` accepts only exact bounded plain JSON. The root schema is
`runtime-terminal-selection-v1` and contains exactly:

```text
schema, task, task_id, task_revision, run, run_id, base_sha,
candidate_sha, tree_sha, result, reviewed_source, review, evidence
```

TASK/RUN/RESULT/reviewed-source/REVIEW and raw references use VP-03A's exact
`RecordIdentity`: commit SHA, path, blob SHA and SHA-256. Each evidence selection
has `evidence_id`, `evidence_digest` (the canonical digest of the embedded
existing EVIDENCE record), and `raw`. These are selection facts, never source
credentials. The canonical APIs accept no repository, remote, ref, catalog,
expected-main, Runtime instance, issuer, fixture authority or rights argument.
Versions, booleans as revisions, extra/missing fields, traversal, ref-like SHAs,
caller object hooks, excessive depth/nodes/bytes and oversized populations fail
strict decoding. Directly constructed typed selections are revalidated.

`observe_terminal_provenance(selection)` returns
`runtime-canonical-provenance-v1`. It reads only Runtime's closed availability
boundary. No root is guessed when issuance is unavailable. The observation has
no terminal/currentness facts in that case. Equality/repeated delivery describes
the same read-only observation, never replay consumption or a durable receipt.

`inspect_terminal_content(selection, fixture_repository=..., fixture_main_sha=...)`
is an explicitly separate offline diagnostic with
`runtime-terminal-provenance-content-v1`. Its successful byte validation means
**CONDITIONAL_FIXTURE_CONTENT**, not independently authenticated Runtime/Reviewer
issuance. Caller-selected fixture roots cannot feed either canonical API.

The diagnostic uses the existing bounded VP-03A Git reader, canonical TASK,
transport RUN/RESULT identity decoder, RESULT/EVIDENCE validators and REVIEW
parser/binding validator. It checks exact TASK identity/revision/blob/commit,
admitted RUN identity/base and candidate SHA/tree, complete unique EVIDENCE
population, embedded record digest and raw identity/path, independent decision
selection and exact reviewed terminal source. RUN/RESULT/raw must belong to one
terminal source commit; altered or torn bindings fail closed. Existing transport
artifact and review commits are isolated; invented candidate-to-artifact
ancestry is deliberately not required. TASK-to-base and base-to-candidate
ancestry still use authenticated raw Git parents.

Complete observations retain immutable original TASK/RUN/RESULT/REVIEW bytes,
each raw byte string, the embedded EVIDENCE representation and digest bound to
the exact enclosing RESULT bytes, original subject/command/exit code and PASS
or FAIL, and original REVIEW verdict/acceptance facts. Original FAIL is not
relabelled PASS. Carrying an existing REVIEW is not issuing a semantic verdict.

Separately observed currentness records before/after values for fixed main,
candidate review, terminal artifact and review-decision refs. Both reads must
match the diagnostic's expected exact refs. Missing, moved, stale or torn refs
produce UNKNOWN and suppress terminal projection. Publication is separately
PUBLISHED, UNPUBLISHED or UNKNOWN based on authenticated candidate/main ancestry;
the terminal remains terminal in either publication state. These local fixture
reads do not authenticate the upstream, provide atomic CAS, exclude ABA between
reads, or confer Publisher rights. Future real source observations need their
own independently grounded authoritative currentness, not these fixture checks.

## VP-03C consumption and unchanged proof facts

`evaluate_handoff(request)` now consults `observe_handoff_provenance(request)`
at the Runtime bridge. It accepts no observation or fixture-authority parameter.
It checks exact version/selection digest and closed source state; content-schema,
spoofed, altered or permission-bearing observations cannot be promoted. The
current Runtime boundary supplies availability facts only, so canonical target
states remain UNKNOWN and no positive terminal/C1/C2 projection exists.

The original VP-03C offline `inspect_content_handoff` stays separate. It retains
the complete versioned VP-03A/B `LineageResult`, original proof population,
contract/mapping pins, distinct semantic obligations, all dependency/condition
digests, applicability witnesses, checkpoint order/seals, replay identity,
original FAIL subjects, feedback and finite resource accounting. It calls the
existing lineage engine once. It neither manufactures those witnesses from
terminal RESULT nor runs proof. Replayed content retains SAME_FACT; duplicate,
missing, conflicting, torn and drifted content fail through existing typed gates.

Persisted RUN may encode ACTIVE alongside a terminal RESULT; that encoding is
retained as an original fact, never projected into live preterminal status.
There is no authentic preterminal C1/C2/currentness/consent/lease producer.
`preterminal_state`, `lease_state`, `cas_state` remain UNKNOWN with explicit
`LIVE_PRETERMINAL_SOURCE_UNAVAILABLE`, `HUMAN_LEASE_UNAVAILABLE` and
`ATOMIC_CAS_ABA_UNAVAILABLE` gates. Before/after ref reads cannot discharge them.

All activation, producer authentication, authorization, correction, continuation,
mutation, checkpoint append/consumption, feedback, acceptance discharge/PASS,
proof reuse, verification, semantic-verdict issuance, terminalization,
scheduler/publisher, target-EVIDENCE, persistence and budget effects remain
immutable false. Activation is **NOT_ACTIVATED**. BR-1..BR-6 remain required.

## Focused verification and remaining production gates

The focused verification surface is `tests/test_runtime_provenance_bridge.py`.
The sole new focused command for Runtime is:

```text
python -m pytest -q tests/test_runtime_provenance_bridge.py
```

Executor does not run that canonical command or generate EVIDENCE. Tests use
small deterministic disposable Git graphs and reuse upstream fixture helpers,
not expensive suites. They cover availability, fake coherent repositories,
Runtime-looking constructors, exact identity/evidence/raw/review swaps,
isolated artifact topology, original PASS/FAIL, independent publication state,
stale/torn refs, duplicate/replay/missing sources, absent preterminal authority,
mapping/condition drift and unchanged repository bytes/effects. Test observation
permits only local `cat-file`/`rev-parse` reads with lazy fetch disabled. There
are no network calls or live Runtime executions.

The following external gates still block production evidence reuse:

- Independently grounded existing Runtime/Reviewer source issuance, canonical
  raw availability and authoritative main/publication currentness.
- Authentic preterminal checkpoints, Human consent/lease and durable atomic
  compare-and-swap/ABA-safe consumption.
- VP-01 **DEFERRED_NOT_WAIVED**, VP-02 and whole VP-03 exit **NOT_ESTABLISHED**.
- KA-01 G1..G6 and explicit activation: **NOT_ACTIVATED**.
- VP-04 single Runtime proof engine, lawful reuse/discharge and scheduling.
- VP-05 correction/attribution and CAS/consumption gates.
- VP-07 production conformance and explicit Human cutover, with VP-06
  measurements and other prerequisite gates intact.

Only the five TASK-333 modify paths change. Frozen Kernel, current
minimum-sufficient-v2 verification/lifecycle contracts, workflow/transport,
historical TASK-330/331/332 artifacts, publication authority, roadmap and
downstream pins remain unchanged. This bridge creates no lifecycle state and
provides no automatic progression to later gates.
