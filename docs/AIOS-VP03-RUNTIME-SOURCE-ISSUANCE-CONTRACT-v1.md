# VP-03 Runtime source issuance contract v1

TASK-334 revision 1 defines prospective subordinate terminal source metadata.
It does not change canonical TASK, RUN, RESULT, EVIDENCE or REVIEW schemas. The
public repository is `trung-via/AIOS-renew`. Protected raw must remain at the
existing local Runtime boundary; publication of raw requires separate explicit
privacy authorization.

## Implementation blocker and authority boundary

**Trusted issuance is BLOCKED in this revision.** This is an implementation
blocker, not a pending verification or Reviewer judgment. The five-path scope
does not expose independent admission and authenticated transport authority to
the callable completion boundary. It must not be reported as authentic issuance
or completion of TASK-334 AC1/AC2/positive AC4/AC5/AC7.

The existing owner flow is:

1. `operator.py` admits the exact TASK, reserves/persists RUN, binds the approved
   execution profile and owns the invocation-local `_RunAttempt`. PRIMARY also
   acquires the dispatch lease through `RunLeaseRegistry`.
2. It constructs `RuntimeCompletion` using paths, TASK/RUN values, a verification
   runner and an observation tracker. These constructor inputs are public
   Python values; the invocation/admission/lease ownership is not transferred
   as an independently established capability.
3. `RuntimeCompletion.complete` gates the committed candidate, executes the
   admitted verification commands, binds canonical EVIDENCE, persists RESULT,
   records observation and invokes `transport_post_pass`.
4. Transport resolves a branch-configured upstream and publishes the existing
   candidate and artifact refs. Branch configuration and an upstream URL
   describe routing. They do not independently authenticate a Runtime issuer.

A caller can construct the same completion class and coherent repository/files.
Checking its class, `.git/aios` location, TASK blob, RUN, profile, Git SHA,
observation, successful verification or upstream configuration cannot prove
that the caller passed the real admission owner. A stack inspection,
self-certified `already_admitted` field, fixture, new private token minted by
the constructor or caller-supplied trust root would merely hide this gap.

The independent Reviewer boundary is separate. Existing REVIEW ingress and
`aios/review-decision/<RUN>` transport carry the semantic decision. The
read-only Git lifecycle projection provides RUN/decision SHA/REVIEW bytes, but
does not give this completion module a protected independent Reviewer ingress
attestation. A review-shaped blob, decision ref or verdict field is insufficient
to establish the decision author's authority or its exact admitted revision.
Runtime must never author Reviewer PASS. RESULT is not REVIEW.

**Required bounded authority revision:** authorize the existing admission owner
and its protected transport/admission configuration to deliver exact invocation
ownership, approved profile, admitted TASK commit/blob, RUN/base/lease and
repository/upstream identity to completion, and authorize read-only observation
of the independent canonical REVIEW ingress provenance. Admission and ingress
integration files lie outside this TASK's five modification paths. This request
does not admit, dispatch or launch another execution, enroll an issuer, establish
a new signing service, or grant a new permission. Actual authentication must be
grounded in the existing protected owner, rather than another repository record.

## Installed bounded completion capture

The real `RuntimeCompletion.complete` path now calls `_capture_completion`
**after** canonical RESULT and terminal observation persistence and **before**
existing transport. It performs no verification, scheduling, review or
terminalization. `source_observation` is invocation-local subordinate diagnosis.
All capture outcomes remain `BLOCK` with:

- `RUNTIME_ADMISSION_AUTHORITY_UNAVAILABLE`
- `AUTHENTICATED_TRANSPORT_CONTEXT_UNAVAILABLE`

A coherent content capture may additionally exist at the protected existing
verification location:

```text
<git-dir>/aios/verification/<RUN>/terminal-source-v1.json
```

This is a **content observation**, never a trusted issuance receipt. It captures
the original raw path, exact command and original outcome in its private section.
Its source section contains bounded safe metadata only. Neither section is wired
to public transport. It is not a new canonical state database or trust registry.

The capture compares persisted RUN to the completion's exact RUN (including a
remediation wrapper), exact task ID/revision and parsed TASK, TASK blob at base
and candidate, original verification subject, candidate tree, persisted canonical
ResultPackage and each original EVIDENCE descriptor. Execution-profile bytes
are bound to RUN/executor as content; their presence does not authenticate profile
approval. The observed TASK commit is RUN base, explicitly **not** a claim about
the independent admission owner's potentially distinct admitted TASK commit.

Record identities contain exact Git blob ID, SHA-256 and byte size. Individual
EVIDENCE identities hash the documented sorted compact ASCII JSON representation
with trailing newline of the original EVIDENCE item; the RESULT identity binds
the entire unchanged persisted package bytes. No original subject is relabeled,
command executed again, outcome converted, acceptance PASS invented or target
proof generated.

Raw is read only under protected existing verification storage, with symlink or
junction refusal, size bounds and before/after file identity checks. SHA-256 and
size describe those original bytes. Missing, external, oversized or changing raw
causes typed refusal. A digest is not an independent retrieval, proof reuse
permission or executable authorization. These local reads do not claim atomic
filesystem snapshots under a malicious concurrent storage writer.

Captured bytes are exclusively created, flushed and compared on replay. Exact
replay is idempotent. Conflicting replay is `SOURCE_CAPTURE_REPLAY_CONFLICT` and
never overwrites the existing capture. A torn write is not recovered into trusted
issuance. Raw, command text, summary, workspace and local paths never enter the
safe source section. Commands are represented by their SHA-256. No credentials,
subprocess output, environment values or raw payload are added to public Git
objects. Existing canonical result/transport formats are unchanged; this does
not grant privacy approval to publish sensitive existing canonical summaries.

Bounds are 1 MiB per original record, 64 KiB safe metadata, 32 EVIDENCE items,
8 MiB per raw and 32 MiB total raw. Local Git plumbing has a ten-second timeout,
no network commands, replacement objects, lazy fetch or inherited Git overrides.
Oversized or unsupported cases fail closed. The additive capture cannot replace
an original verification, cleanup or transport failure with a provenance error.

## Reserved safe source slot and public transport refusal

The prospective versioned slot is `.ai/transport/runtime-source-v1.json` in the
**existing** terminal artifact namespace. No new public ref or endpoint is
introduced. The current content format is `AIOS_RUNTIME_TERMINAL_SOURCE_CONTENT`,
version 1. It is deliberately distinguishable from a future authenticated
issuance format. The strict schema contains only:

```text
format, version, authority, phase, operation, task, run, candidate,
result, execution_profile, evidence, effects
```

`authority` must equal `CONDITIONAL_CONTENT_ONLY`; self-asserted authenticated
authority is `FORGED_ISSUER_ASSERTION`. Unknown/missing fields, duplicate JSON
fields, unsupported versions/phases, invalid IDs/digests, boolean integer fields,
duplicate evidence and enabled effects are refused. All lifecycle effect flags
are false with `NOT_ACTIVATED`.

`transport_post_pass` and the success artifact builder reject any supplied
`runtime_source` before remote reads or Git object writes, with typed reasons.
A helper caller therefore cannot launder this candidate into trusted public
issuance. `None` retains legacy behavior, ordering, recovery CAS/ref handling,
artifact replay and terminal attention delivery. FAILURE transport is unchanged.
There is no backfill or change to any historic artifact, including TASK-333 r1,
RUN-333-001, failed candidate `af16ec63c8700b9f7411434d338f044300392b86` and failure
artifact commit `53230d3eeac098a9350ce99669877b51623006e4`.

## Separate read-only observations

`observe_issued_source(run_id)` has no repository, issuer, trust-root, remote,
ref, callback or environment configuration argument. In the absence of the
required protected authority integration it returns UNKNOWN with the admission,
transport and independent Reviewer blockers. It has no fixture fallback.

`inspect_content_source(repo, run_id)` is a separately named **offline content
diagnostic**. An arbitrary repository remains conditional content only. It
reads local `refs/heads/main` and exactly the existing review, artifacts,
review-decision and failure-artifacts refs for that RUN. It cannot fetch, push,
choose other refs, read raw, enroll trust or update lifecycle. A missing source is
`TERMINAL_SOURCE_MISSING`; an old artifact without the reserved slot remains
`HISTORIC_SOURCE_UNISSUED`/UNKNOWN forever unless a separately authorized future
contract says otherwise. No retrospective backfill is provided here.

For a present content candidate it compares exact TASK/base/candidate/tree,
RUN/RESULT/profile bytes and every EVIDENCE/command/outcome binding. Conflicting
terminal refs, swapped records and stale main TASK blobs fail closed. It reads
the independent decision ref separately, requires one unambiguous PRIMARY
REVIEW, checks exact candidate and complete TASK acceptance through the existing
review validator, and preserves its recorded verdict only as content. A delayed
decision can change `UNREVIEWED` to `CONTENT_CONSISTENT`. It never changes
`reviewer_pass=False` or `issuer_authenticated=False`. DELTA history requires a
future independent history integration and is explicitly unsupported.

Missing/unreviewed/stale/forged/ambiguous decision content cannot yield Reviewer
PASS. Before/after ref reads detect observed drift; they do **not** establish
CAS, an atomic snapshot or ABA safety. Read-only facts retain observed immutable
main/artifact/decision SHAs, with `atomic_currentness=False`. Public digest bytes
are never treated as locally retrieved raw: `RAW_UNAVAILABLE` remains UNKNOWN,
including a plausible or swapped digest in otherwise coherent content.

## Focused fixtures and remaining gates

`tests/test_runtime_provenance_issuer.py` uses disposable local repositories and
a deterministic verification runner. It exercises the real completion hook,
protected raw/digest capture, no new public raw export, exact identity,
idempotence/conflict, outsider/preterminal refusal, missing and historic sources,
delayed REVIEW content, forgery, stale/torn refs, ambiguity, transport refusal,
failure-cause preservation and disabled flags. It performs no live GitHub reads
or writes. A local fixture is not authentic admission. Authentic issuance and
independent positive Reviewer provenance tests cannot be supplied truthfully
until the bounded authority revision connects the real owners. Runtime alone
executes the TASK verification and constructs EVIDENCE; the Executor does not.

Current `minimum-sufficient-v2` remains operative. TASK-333 lawful REPAIR requires
separate authority and is not launched or discharged here. Terminal provenance
is not live preterminal C1/C2. CAS/ABA remains open. VP-01 is deferred-not-waived;
whole VP-02/VP-03 exits remain open. VP-04 source policy/proof reuse/scheduling,
VP-05 correction/consumption, VP-07 production conformance and Human cutover,
and KA-01 G1..G6/continuation remain unauthorized and NOT_ACTIVATED. No frozen
Kernel or roadmap advancement follows from this metadata.

This delta modifies exactly `runtime.py`, `review_transport.py`,
`runtime_provenance_issuer.py`, `test_runtime_provenance_issuer.py` and this v1
document. It leaves the required out-of-scope admission and ingress changes as
an explicit implementation blocker instead of certifying a repository fixture.
