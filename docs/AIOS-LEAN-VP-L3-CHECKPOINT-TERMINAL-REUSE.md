# Lean VP L3-02: immutable checkpoints and terminal proof reuse

L3-02 adds an explicit prospective seam to the existing `RuntimeCompletion`:

- `seal_checkpoint(ProofCheckpointRequest(...))` authenticates the current
  admitted RUN and independently reviewed full proof plan, reserves one bounded
  native effect, captures the exact authored candidate proof through the existing
  Runtime verifier, and seals one immutable subordinate K_i.
- `terminal_reuse(request, checkpoint_digest=...)` freshly authenticates the
  exact final seal and returns an inspection decision. It issues no execution
  permission and writes no terminal artifact.
- `complete(package, policy, checkpoint=request, checkpoint_digest=...)` uses
  that same completion owner's existing structural, Git, scope, acceptance and
  canonical package gates and RESULT writer. It consumes the exact original
  current-RUN EVIDENCE from K_i, without invoking equivalent verification again.
  A decision object, parsed plan or caller-supplied checkpoint is never an input.

This seam is absent from operator/dispatch/policy routing. It admits no RUN,
Executor, operation, correction, lease or Human limits. KA-01 v1.2 remains
**NOT_ACTIVATED**. G3/G6 opt-in and integrated production cutover remain
**OUTSTANDING**. Explicit implementation/conformance use under an already admitted
TASK's authored verification is distinct from production opt-in.

## Native authority and full executed proof

`ProofCheckpointRequest` version 1 contains only the exact L3-01 scheduler
request, ordinal and predecessor digest. Its canonical ASCII JSON format is
`AIOS_L3_CHECKPOINT_REQUEST`. It inherits the closed, duplicate-rejecting 2 MiB,
depth-24, 200,000-node and bounded-text encoding. Exact types reject booleans as
ordinals, alternate encodings, added validity flags, supplied proof objects and
oversized nested locators before authority or execution effects.

The same L3-01 core independently authenticates the exact TASK/revision,
ACTIVE admitted RUN/PRIMARY operation/base, canonical run transport, current
candidate SHA/tree, scope, complete Human-selected independent mapping Review,
original Review artifacts/EVIDENCE and mutable native roots. The local owner
additionally binds its Task and Run objects and all state paths to that admitted
`.git/aios` ownership. A dirty candidate, base candidate, another worktree, a
terminal fact or pre-existing unaccounted verification files blocks sealing.
Neither L3-01's `PLANNED` status nor an Executor claim supplies proof satisfaction.

The supported fresh execution binding is an independently reviewed,
content-addressed `l3e:<sha256>` condition document at
`docs/aios-proof-contracts/l3-executed/<sha256>.json`. This is a prospective
provider; existing L1/L2/L3-01 formats and decoders are unchanged. Its closed
`AIOS_L3_EXECUTED_CONDITIONS` version 1 fields are:

| Field | Native check |
| --- | --- |
| proof_id, claims, conditions | Exact full reviewed descriptor, including every semantic condition; only the content-addressed test_ref is omitted to break its hash cycle. |
| command_index | Exact position in the unchanged TASK.verification.required tuple; never executable command text supplied by the caller. |
| profile, toolchain | Exact native current execution/environment/configuration/observer and installed toolchain observations. |
| collection | Lossless exact nodeids and native collection identity with the full reviewed population size. |

The document must be a changed file in the exact independently reviewed delta,
inside that Review TASK's modification scope and canonical RESULT.changed_files,
and identical in the selected published source, current main and decision tree.
A matching hash alone cannot authenticate it. All reviewed proof units must map
one-to-one onto the complete authored command population. Approved shared claims
remain within their single reviewed proof unit; distinct integration, worker,
concurrency, ordering and repetition units require separate native executions.
No partial unit or narrow successful command discharges another unit. Every
comparison unit remains BLOCKED because native BR-1..BR-6 attestation is not
available. No baseline materialization, reproduction, early probe, fallback,
command inference or alternate scheduler is introduced.
The supported capture is the existing exact pytest observer layout. Opaque
commands, measurement commands and the selected full-suite wrapper's different
capture layout are BLOCKED here before launch; their mandatory obligations are
retained. Ordinary exact pytest integration/full-population commands remain
eligible when their complete native binding fits the bounds.

The existing Runtime verifier runs each unchanged authored command exactly once
on its existing clean materialized candidate. Its existing native pytest
observer records exact full collection, setup/call/teardown reports, process and
worker topology, isolation, actual toolchain, diagnostics and completeness.
This capture instruments original authored proof; it does not invoke V2
attribution/derivation or run a V2/new-policy pair. Unobserved/opaque proof cannot
supply a seal. Sealing reopens original native observations and raw captures,
validates existing native execution contracts independently of retained flags,
and rechecks the full reviewed plan and mutable roots after execution.

A complete stable raw FAIL may be retained in K_i with explicit outstanding
obligations; it never grants terminal reuse. Incomplete, unstable, unknown,
changed or structurally inconsistent observations leave the original captures
and reserved intent and block. Raw ERROR and skipped/unsatisfactory phases
cannot become terminal PASS. L2 source locators cannot masquerade as current
execution receipts: this slice deliberately blocks their promotion through this
fresh-capture seam. Original same-RUN checkpoint proof is reused at terminal;
it is never transplanted to another subject or relabelled from another RUN.

## Subordinate durable identity and accounting

All storage derives from the existing native RUN/evidence owner:

```
.git/aios/verification/<RUN>/.runtime-mutation/intent.json
.git/aios/verification/<RUN>/l3-02/k001.json
.git/aios/verification/<RUN>/l3-02/k001/<original EVIDENCE id>.raw
.git/aios/verification/<RUN>/l3-02/k001/observation-001.json
.git/aios/verification/<RUN>/l3-02/terminal-intent.json
```

`kNNN.json` is a closed `AIOS_L3_RUNTIME_CHECKPOINT` version 1 subordinate
record, not a canonical RUN, RESULT, FAILURE or EVIDENCE type. Its digest is
SHA-256 of its single canonical encoding. There is no independent lifecycle
journal, trust root, status reducer or mutable checkpoint index. Runtime reads
at most 17 consecutive immutable records and their retained predecessor digests.
The existing admitted RUN and existing RESULT/FAILURE locations remain authority.

Each record retains the exact request, full authenticated scheduler plan and
Review provenance, every original mandatory obligation/claim/acceptance/condition
and assignment, original current-RUN EVIDENCE, original native observation/raw
hashes and sizes, observed completeness/stability/outcome/satisfaction, explicit
outstanding/affected populations and cumulative resource facts before/after.
EVIDENCE IDs are unique and consecutive across captures in the same RUN. The
canonical terminal preserves those original records byte-for-byte in projection;
its source subject, raw path, process facts and exit status are not fabricated.

K1 has ordinal 1 and no predecessor. A later K_i must use the next ordinal and
exact prior digest, a distinct clean committed descendant with a changed tree,
and an independently current reviewed binding for that candidate. It retains
the original TASK/base/RUN admission and mandatory semantic population. The
population identity erases only the candidate SHA and content-addressed
current-candidate execution locator, and their derived contract/entry digests;
claims, mapping assignments, all other conditions, purposes and provenance
remain exact. Native immutable-parent traversal and the TASK scope check bind
the nonempty descendant delta. This is observation of a scoped candidate, not
authority to dispatch a correction or to continue the Executor.
An additional execution-population identity preserves each proof's exact authored
command, lossless collection, native profile and toolchain across K_i. Changing a
reviewed execution locator cannot silently replace that original population or
worker/environment conditions with narrower proof.

Known cumulative counters are checkpoint count, actual authored command
executions, captured raw/native bytes and observed preseal work milliseconds.
Safety ceilings are 17 checkpoints, 256 executions, 32 MiB captures and 3,600,000
milliseconds. These are bounded implementation ceilings, never new Human
budgets. Missing, boolean, negative, decreasing, oversized or contradictory
counters block; an exhausted counter permits no new capture. Costs and ordinal
progress are retained with history, and the intent reserves an effect before
any native launch. A crash with unknown cost/effect never assumes zero or resets
these counters. Live Executor tokens, lease/correction budgets and complete
production resource enforcement remain L3-05 prerequisites; these counters
assert no facts about unobserved Executor resources or authority to continue.

## Replay, concurrency and terminal ownership

Native writes are exclusive-create, flushed and file-fsynced. POSIX also
fsyncs parent directories; Windows uses file FlushFileBuffers through fsync.
The exclusive per-RUN Runtime fence covers prospective captures, terminal
consumption and the existing terminal/failure entry points. Directory and
original-file readers reject aliases, symlinks/junctions, traversal, hardlinked
files, changing reads and malformed/oversized state. The legacy V1/V2 command
and verdict branches are unchanged; they cannot silently rerun proof on a RUN
that already has a prospective checkpoint effect.

An exact current checkpoint replay reauthenticates the native fact and returns
the same immutable bytes/digest. It appends nothing and causes no execution,
ordinal increment or additional resource debit. Same ordinal with different
content/locator, a gap, a stale predecessor, reused/base candidate, changed
mandatory population or competing writer blocks. An interrupted reservation,
partial capture or interrupted checkpoint write retains its first intent and
native captures. There is no automatic unlock, retry, failover, counter repair
or continuation. Even a fully written checkpoint with an unresolved mutation
fence cannot grant another effect.

A prospective terminal decision accepts only the exact final seal after fresh
full native plan/proof/raw/environment/currentness authentication and satisfactory
complete stable mandatory proof. Outdated checkpoints, invalidated or unrelated
proof, UNKNOWN, flaky proof, raw FAIL/ERROR and outstanding obligations are
ineligible. Equivalent current checkpoint proof replaces only the same terminal
proof; independent broad/integration/worker/order/repetition/comparison coverage
is never waived. RESULT claims must exactly preserve the reviewed claims and
acceptance population, and the unchanged canonical package validator still
checks the exact current RUN/subject and authored command population.

Before the existing RESULT writer acts, it reauthenticates once more and writes
an immutable subordinate terminal intent binding the final checkpoint, structural
package, completion policy and complete canonical RESULT bytes. Duplicate
finalization reconciles only that exact persisted terminal fact, without proof,
transport, observation or resource effects. Conflicting package/policy, partial
or absent terminal bytes, competing FAILURE, a terminal RUN or late checkpoint
cannot create a second terminal or reopen an execution. If a terminal write is
interrupted after its exact bytes were durably written, replay only reads that
fact; ambiguous bytes remain blocked. Original FAILURE diagnostics are preserved.

The explicit prospective completion ends at the existing local canonical
terminal writer. Its admission-ref/terminal transport integration remains
OUTSTANDING: L3-01's prospective run-only transport locator must not be silently
rewritten as terminal transport. Default legacy completion continues its
existing transport behavior. No publication, review or roadmap advancement is
inferred by the prospective local terminal effect.

## Conformance and remaining gates

`tests/test_lean_vp_l3_checkpoint_terminal_reuse.py` defines isolated real-Git
native authority and actual subprocess proof cases for first/second seals,
full integration/worker/order/repetition coverage, terminal proof reuse with no
equivalent second execution, exact replay, collisions/concurrent delivery,
checkpoint/terminal write interruption, native raw/observer/root changes,
unknown/incomplete/unstable proof, raw failure, all BR gates, terminal exclusion,
closed input bounds and independently missing/conflicting correction lineage.
Only HTTP transport is replayed. Negative fault injection cannot confer proof
authority. Runtime owns executing conformance and producing canonical EVIDENCE.

REPAIR and REMEDIATION remain BLOCKED, including exact-looking predecessor,
action, finding, authorization, source, affected-verification and DELTA locators.
No PRIMARY source, prior RUN consent, parser assertion or current candidate hash
substitutes for independent operation-specific native authority.

F_i delivery (L3-03), live leased same-Executor continuation (L3-04), full finite
Human resource/progress/accounting enforcement (L3-05), integrated readiness and
negative production safety (L3-06), G3/G6 opt-in, and integrated production cutover
remain **NOT_ACTIVATED/OUTSTANDING**. L4 VP-01/VP-06 measurements are
**OUTSTANDING**. No Kernel/history, published L1-L3-01 contract, policy registry,
operator launcher, roadmap or downstream adoption is changed by this slice.
