# Lean VP L3-04: prospective same-Executor local consumption and STOP

L3-04 provides one explicit, nondefault preactivation API on the existing
`RuntimeCompletion` owner:

`consume_same_executor_feedback(FeedbackConsumptionRequest(...), boundary=..., lease=..., adapter=...)`

The request contains only version 1, an original `FactualFeedbackRequest`
locating the exact latest K_i, and the digest of its original prepared F_i.
It cannot carry a projected feedback record, proof result, callback, command,
patch, progress declaration, lease assertion, consent, policy or budget.
Malformed typed requests and unsupported wire fields fail closed.

This API must be called synchronously from inside the originally selected
adapter's existing `execute(task=..., run=...)` call. It never calls `execute`,
constructs an adapter, chooses an Executor, invokes a provider, dispatches a
workflow, executes verification, creates a RUN or writes RESULT/FAILURE. The
existing Dispatcher still claims its sole adapter once. The prospective caller
must explicitly bind that original Runtime object with `prospective_runtime`
before the sole invocation. This local plumbing is not Human opt-in or an
execution permit. Its `active_boundary` is available only during that explicit
original PRIMARY invocation; default dispatch retains the original path without
prospective frame capture. Normal PRIMARY,
REPAIR and REMEDIATION dispatch, terminal ownership, historical V1/V2 decoding,
verification policies and the L3-01/02/03 APIs retain their existing behavior.

Published native checkpoint and factual-feedback types and codecs remain
defined in `intra_run_proof_contract`, alongside the new local receipt codecs.
They are directly callable on a fresh independent import before Runtime has
initialized. Ordinary codec-local imports call the existing strict canonical
`verification_contract` and `artifacts` validators; there is no duplicate
validator, hidden loader, host binding or global initialization prerequisite.
The historical VP-02B offline declarations retain their original pure
dependencies and zero-effect public calls. The scoped AST regression permits
only named native codec-local canonical imports and the annotation-only
`TYPE_CHECKING` declaration, while retaining negative dependency, loader,
side-effect and malformed-input cases. A cold subprocess round-trips original
native K_i/F_i and typed requests and rejects malformed format/parse inputs
before any Runtime import.

## Live process-local holder and adapter observation

`ExecutorBoundary` retains a private invocation frame only while the original
`execute` call is on its original thread. Consumption requires that exact
boundary, TASK/RUN, adapter object and original `RunLease` object, still held
by the original `RunLeaseRegistry`. The registry checks original holder identity
before and after the synchronous observation; release remains operative on any
thread, and reacquisition cannot hide loss of the original token. A second Runtime object,
adapter, invocation, callback implementation, profile/model/effort or thread
cannot substitute after binding. An available immutable execution profile is
checked against its original value and Dispatcher binding, including RUN and
Executor identity. Missing profile data is never filled from a default.

The adapter must already implement the optional synchronous
`confirm_local_feedback(feedback=..., receipt_id=...)` capability when its
original invocation begins. Only its direct, exact typed
`LocalFeedbackConfirmation(receipt_id, feedback.digest)` return can establish
the local acknowledgment. No ACK is accepted as an API input. Unsupported
adapters block. The existing production adapters do not implement this
capability; no default path calls this API or automatically sends F_i.

An active wrapper, stored token or historical receipt alone is insufficient:
the call must originate on the original active execution stack and the same
adapter must actually confirm the exact F_i in that invocation. This is a
bounded local adapter observation, not proof of a remote production session or
transport delivery. There is no durable lease generation, timeout renewal,
post-crash reconstruction or fallback invocation. Released/absent/copied or
cross-RUN tokens, detached threads and calls after `execute` block. Registry
and invocation identity cannot be reconstructed from serialized material.

Before this explicit native invocation starts, the same Runtime reserves one
immutable `local-invocation.json` intent under the existing per-RUN store and
fence. It binds the original admitted RUN and private Runtime/invocation
identities. It asserts no LIVE state and is never reconstructed as a lease.
Any existing intent, including a partial write or an invocation that produced
no feedback receipt, blocks another native invocation before `execute` is
called. An unresolved startup fence also blocks. Removing or changing that
original intent cannot create consumption authority in the live owner. This
negative crash guard adds no independent registry, generation or lifecycle.

## Original complete native proof and one existing fence

Before exposure, after confirmation, after receipt persistence and on replay,
Runtime reuses `_authenticate_checkpoint`: exact ACTIVE native admission,
TASK/revision/PRIMARY/base/committed candidate/tree, clean in-scope source,
original current Human-selected full reviewed mapping, latest immutable K_i,
native complete stable proof population and original raw/observer captures.
It rereads the original durable F_i and compares its exact bytes and digest
with the deterministic full K_i projection. Caller hashes select no authority.
PREPARED requires all mandatory proofs decisive PASS/FAIL and at least one
native candidate call FAIL. ERROR, UNKNOWN, corrupt/incomplete capture, a
narrow passing selection, changed mapping/semantics or stale source blocks.
Unaffected PASS, shared claims and distinct integration, worker, ordering,
repetition and other original mandatory conditions remain covered unchanged.

The local receipt is subordinate to that K_i and F_i in the same protected
store, using the existing exclusive-create/flush/fsync helper:

```
.git/aios/verification/<RUN>/.runtime-mutation/intent.json
.git/aios/verification/<RUN>/local-invocation.json
.git/aios/verification/<RUN>/l3-02/kNNN.consumption.json
```

The existing per-RUN Runtime fence excludes checkpoint, feedback and terminal
writers through authentication, callback and persistence. A competing mutation
observed during startup or a local callback leaves a bounded immutable `contested.json`
negative witness under that fence, including preflight rejections by checkpoint,
feedback and terminal-assessment gates, even if its caller swallows the conflict.
No second journal, queue, ACK broker, lease registry or lifecycle owner exists.
The original intent and all captures survive uncertain effects. The bounded
history recognizes at most one receipt per checkpoint and validates its
original K_i/F_i binding; reading that history grants no liveness or permission.

Receipt identity binds exact TASK/revision/RUN/operation/base/candidate/tree,
admitted Executor/RUN digest, original reviewed authorization, current K_i/F_i
identity/content, full population/execution identity, private Runtime and
invocation identities and an available profile digest. Process-local retained
receipt bytes additionally prove that this owner observed the actual callback.
An exact replay freshly authenticates those original sources and current live
holder, returning the original immutable bytes without another callback,
proof execution, adapter invocation, resource debit or receipt append. Changed
source, conflicting request/receipt, missing original receipt, unknown callback
state or a replacement Runtime blocks. Persistent ACK bytes cannot authorize
a restarted process. Receipt/fence ambiguity is never repaired or retried.

Any observed RESULT, FAILURE, terminal intent, revoked/changed source,
competing mutation or uncertain session prevents new acknowledgment and replay
authority. An observed failure latches STOP in the current owner; an interrupted
effect retains its original fence. Native checkpoint, feedback and terminal
assessment failures observed by this same explicitly bound owner also latch
the local STOP, so restoring a revoked source cannot restore receipt authority.
The historical gates retain their behavior without a local invocation binding.
This API never writes a terminal outcome or
reopens a terminal RUN; existing Runtime once-only terminal writers remain sole
owners. A later legitimate terminal outcome preserves prior factual records.

## STOP and bounded inspection

Successful output is `LOCAL_ACKNOWLEDGED`, with the historical observation
`CURRENT_DURING_LOCAL_CALLBACK`. Its separate assessment is always **STOP**:

| Observed condition | Assessment |
| --- | --- |
| First/same/old SHA or no independently observed improvement | NO_VERIFIED_COMMITTED_PROGRESS |
| New authenticated scoped committed descendant with strict reduction of original failed obligations, same full semantics and a prior original locally consumed FAIL | VERIFIED_SCOPED_DESCENDANT_PROGRESS; finite Human opt-in/token budget NOT_ESTABLISHED; STOP |
| Exhausted known native checkpoint/execution/byte/time limit | BLOCKED before exposure |
| Lease/source/session/operation/callback/fence not established | BLOCKED; STOP |

Progress uses original native failed-obligation sets, exact immutable history,
changed trees and causal ancestry already authenticated by L3-02, plus retained
original local receipts. Caller claims, empty/unrelated deltas, dirty/out-of-scope
source and inconclusive proof cannot establish it. Verified progress grants no
C2 acceptance. Complete Executor token/time/correction accounting, original
finite Human version/policy/profile/budget consent, withdrawal state and risk or
semantic choices are not established by this slice. No such input is invented
or accepted from callers. Known cumulative native counters remain factual and
unchanged by consumption. Missing accounting never implies free capacity.

Every result retains L3-03's denied authority vector: production delivery,
ACK consumption permission, C2 admission, continuation, Executor/policy/strategy
selection and another RUN remain NOT_AUTHORIZED; production is NOT_ACTIVATED;
Human risk/budget and a durable live lease are NOT_ESTABLISHED. The separate
local callback fact does not change F_i or this authority vector. REPAIR and
REMEDIATION cannot consume PRIMARY F_i: independently authenticated exact
original FAILURE/REVIEW, correction action/finding/scope, operation authorization
and current sticky holder are still prerequisites for any future correction seam.

Strict deterministic versioned codecs enforce closed fields, fixed statuses and
reasons, bounded ASCII identities/hashes, counter consistency and the existing
2 MiB/depth-24/200,000-node decoder limits. The store has at most 17 checkpoints
and one F_i/receipt per checkpoint. Results contain only factual identities,
digests, known counters and guarded assessment; no raw logs, secrets, absolute
paths, executable instructions, patches, authored commands or Runtime-selected
HOW. Parsed output never authenticates itself.

## Definitions and remaining rollout boundaries

`tests/test_lean_vp_l3_same_executor_continuation.py` defines focused real-Git,
native proof/capture and actual `RunLeaseRegistry`/`ExecutorBoundary` cases.
They include an isolated positive same-holder callback, unchanged full mandatory
coverage, exact replay, wrong/released/reacquired/copied holder, replacement
adapter/profile/owner, detached/after-execute call, stale/source collision,
terminal/fence contention, callback reentry, crash/partial/complete-write
ambiguity, ERROR/UNKNOWN, absent consent/accounting, exhausted limit, no progress,
committed partial improvement and correction spoof. Positive liveness comes
from the actual invocation and registry, not an affirmative validator mock.
The progress case begins with two original failures and one passing independent
worker proof. It retains all three complete mandatory proofs and their original
programs across the scoped subject commit, reconciles the first immutable
receipt and observes no extra proof/callback effects on exact replay. The crash
case retains the entire original store and blocks a reconstructed owner before
another native invocation.
Runtime owns canonical execution and EVIDENCE for these definitions.

This is **preactivation only**. L3-05 owns real rollout/rollback and finite Human
admission, consent, budget/token accounting and withdrawal. L3-06 owns integrated
focused engineering proof for **G3**, followed by independent review/publication.
**G6** remains separate exact Human per-RUN activation after eligible G3.
Postactivation **L4**, including G4/G5, whole-episode VP-01/VP-06 measurement and
full real VP-07 conformance, remains separate. This isolated local demonstration
establishes no production activation, G3/G6 completion, consent or performance
improvement. Frozen Kernel, KA-01 normative source, roadmap and TASK lineage are
unchanged.
