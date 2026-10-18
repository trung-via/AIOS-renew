# Lean VP L1 baseline and proof-contract readiness

TASK-373 supplies a bounded read-only view, not an L1 exit or production gate.
VP-01 remains an **outstanding measurement dependency**, historically
`DEFERRED_BY_HUMAN`; it is neither DONE nor waived. Published
`minimum-sufficient-v1` is the interim comparison baseline. KA-01 remains
**NOT_ACTIVATED**, subject to its separate G1–G6 gates and Human versioned opt-in.
The [Lean VP plan](AIOS-LEAN-VP-INTELLIGENT-INTRA-PRIMARY.md) supplies the L1–L4
crosswalk; this document changes no planning or historical state.

## Exact source and sampling method

`build_l1_baseline_readiness(snapshot, task_ids=[...])` in
`src/aios_renew/performance_observation.py` takes **only** the already
authenticated `RemotePerformanceSnapshot` from the existing canonical
`resolve_remote_performance_snapshot` path, and an explicit exact TASK set.
Reuse that snapshot in memory. The projection does not call the resolver,
`observe_performance`, Git, a provider, a clock or verification. There is no
new collector, CLI, persistence, current-TASK selection or automatic refresh.

The caller names 1–32 unique bare TASK IDs; caller order is normalized, while
the snapshot must carry exactly the same sorted set. The sample is the supplied
transport's complete selected RESULT/FAILURE terminal-ref census, at most 256
RUNs. It is not a random population or all attempted work: pre-admission carrier
failures, active RUNs and episodes without those canonical terminal refs are
outside this denominator. TASK selectors select all supplied historical revisions
and operations, not a latest revision, matched policy, or matched failure class.

Each ref/SHA must correspond exactly once to its terminal artifact. Duplicate
RUNs, competing terminal kinds, artifact SHAs reused across RUNs, moved refs, stale SHA pairings and fake RUN
names fail closed. Existing v1 binders validate the admitted RUN, terminal
family, TASK revision, Executor, base/candidate and observation identity before
baseline metrics are produced. Missing observations are counted only after
terminal validation; malformed/unsupported observations raise, not missingness.
L1 additionally rejects duplicate JSON fields and non-finite values. Its own
limits are 128 characters per identity, 1 MiB per consumed JSON record, 8 MiB
total consumed JSON, and exactly one observed ref per terminal. Private
execution-profile bytes are not consumed. Historical v1 decoding is unchanged.

The portable `source` inventory lists each sampled TASK/revision, RUN, terminal
kind, Executor, artifact/base/candidate SHA and SHA-256 of the exact RUN,
terminal and optional observation bytes. Absent observation digest is null.
`binding_sha256` hashes the complete `source` object **excluding that field**:
sorted-key compact ASCII JSON, `ensure_ascii=True`, `allow_nan=False`, SHA-256.
Terminals sort by RUN ID and refs lexically; source order cannot change the
view. Raw-byte changes (including whitespace) change the binding. Raw records,
claims, errors, workspace paths, logs and model/provider details are omitted.

These hashes bind a view to its supplied source; they do not authenticate
caller-fabricated coherent records, establish present-day remote freshness or
create a second trust root. Upstream authentication/currentness stays with the
existing transport owner. The projection checks internal stale/conflicting
bindings without a second remote observation. A detached v1 summary, Executor
claim or caller-declared coverage counter cannot replace the snapshot.

## Available samples and coverage

No authenticated terminal snapshot or matched episode dataset was supplied with
TASK-373. Consequently this document reports **no numerical historical
baseline**, production sample count, savings or dollar cost. That absence is
UNKNOWN, not a measured zero. The API's source inventory and denominators
identify the actual available samples whenever its authenticated input is
supplied; this change performs no independent retrieval to populate a report.

The available regression samples in `tests/test_performance_observation.py` are
explicitly **synthetic**, with no historical/provenance claim:

| Fixture population | Available observation samples | Coverage limitation |
| --- | --- | --- |
| Complete two-terminal RESULT/FAILURE fixture | RUN 10/30 s; Executor 4/4 s; verification 2/2 s; input 100/10, cached 25/0, output 40/5 tokens | 2/2 for each metric; PRIMARY and REPAIR are separate families |
| Three-terminal zero/partial/missing fixture | Two observations; one with exact zero phase/token values, one without Executor/verification/token samples; third has no observation | RUN 2/3; Executor, verification and each token counter 1/3; operation classification 2/3 |
| Valid terminal without sidecar / empty selected census | No duration/token sample | 0/1 or 0/0; sums/percentiles null, never zero |
| Older legitimate sidecar without `soft_budget` | Existing durations remain usable | No inferred budget observation; v1 optional-field semantics retained |

Every metric exposes `sample_count`, `population_count`, `missing_count` and
`status`. OBSERVED means a value is available for every terminal in that
specific sample, PARTIAL means some are available, and UNAVAILABLE means none
are available, including an empty census. These are measurement coverage labels,
never proof PASS. Optional absent phases retain a missing count even when the
Executor was not invoked; they are not silently replaced by zero.

Durations expose nearest-rank p50/p95 and `observed_sum` for available samples.
Input/cached/output tokens each expose their exact available-sample sum and
coverage. Tokens are the existing all-or-none provider-neutral native invocation
counter group; cached input is a subset of input, never added again. A partial
sum is not an estimate of missing consumption. Observed zero has a positive
sample count and remains zero. Empty metrics have null values. No per-metric
denominator is the number of selected TASKs or an assumed count of test nodes.

Outcome, TASK, TASK/revision and Executor counts use all bound terminals.
Operation counts use only terminals with valid observations and disclose the
unclassified remainder. Per-task, Executor, terminal-kind and operation family
groups carry outcome counts and all six metric denominators. Operation-group
denominators cover classified members only; their parent classification coverage
must be retained. Empty groups have null count values. A RESULT is an outcome
family, not a claim of independent semantic review, publication or live proof.

## Metric and proof-work gaps

| Dimension | L1 disposition | Independently required input |
| --- | --- | --- |
| Admitted RUN, Executor invocation, Runtime verification elapsed time | OBSERVED/PARTIAL/UNAVAILABLE with exact denominators | Existing bound RUN_OBSERVATION only |
| Outcome/TASK/Executor/operation counts; input/cached/output tokens | OBSERVED/PARTIAL/UNAVAILABLE as above | Existing terminal identities and optional observation |
| Whole-episode time | PARTIAL when an admitted-RUN sample exists, otherwise UNKNOWN; exact value always null | Authenticated episode boundary and non-overlapping phase measurements |
| Source/Git preparation, collection, fixture/worker startup, local-test breakdown | UNAVAILABLE | Explicit trusted phase observations; no subtraction of RUN/Executor/verification totals |
| Collected/executed/unique/equivalent-duplicate test items | UNAVAILABLE | Authenticated collection/execution population, distinguishing conditions and reviewed equivalence mapping |
| Checkpoint/applicability/feedback time, reused/invalidated/unknown proof counts | UNAVAILABLE; proof coverage/applicability UNKNOWN | Authenticated exact-subject proofs, full applicability witnesses and distinct obligation population |
| Transport, Human intervention, failure handoff, review/publication time | UNAVAILABLE | Separately authorized authenticated phase boundaries, without private trace export |
| Resource/dollar cost, matched equivalence, comparative speedup, safety conformance | UNAVAILABLE or UNKNOWN | Actual resource/pricing data, reviewed matched episodes and real correctness/conformance evidence |

Neither filenames, commands, nodeids, diff disjointness nor equal counters
establish equivalence. Commit timestamps and operational delivery receipts do
not establish transport/review/publication durations. No arithmetic residual,
manufactured node count, token-to-dollar estimate or UNKNOWN-to-PASS conversion
is part of the view. R2.1's published TASK-363/364/366/367/368/369 engineering
history remains intact; independent real-provider live conformance remains
OUTSTANDING/UNKNOWN rather than PASS from this data.

## Reused foundations and independently verifiable entry gaps

Reuse [TASK-326 proof coverage v1](AIOS-VP02-PROOF-COVERAGE-CONTRACT-v1.md)
and `proof_coverage_contract.py` unchanged: exact TASK acceptance/semantic
obligations, reviewed mapping, full distinguishing proof conditions and all
BR-1–BR-6 base-replay gates. Reuse
[TASK-328 intra-RUN contract](AIOS-VP02-INTRA-RUN-PROOF-AND-KERNEL-GATE-v1.md)
and `intra_run_proof_contract.py` unchanged: offline checkpoint/feedback
declarations, immutable subjects/history, finite resources, full applicability
dimensions, VALID/INVALIDATED/UNKNOWN and role/lease/stop invariants. Their
synthetic validation is not authenticated applicability or execution coverage.
L1 neither reimplements nor invokes these contracts as a new Runtime gate.

Before L2 (VP-03), independently establish an exact reviewed semantic
contract/mapping and a complete authenticated source-to-target witness across
code, tests, fixtures/helpers/shared state, toolchain/environment, profile,
workers/concurrency/ordering, collection/integration/repetition/population and
evidence schema. Verify changed/missing/uncertain cases conservatively as
INVALIDATED/UNKNOWN, preserving every distinct mandatory proof. Source hashes
alone cannot supply that witness. Fresh independently admitted engineering and
real negative conformance are required.

Before L3 (VP-04/05), independently establish exactly one Runtime scheduler,
complete current-subject obligations, no equivalent double execution, bounded
committed checkpoints, authenticated single-consumption factual delivery/ACK
to the same authorized Executor/lease, finite consent/resource accounting and
crash/replay/terminal hard stops. An ERROR/UNKNOWN observation cannot authorize
correction. Mandatory integration/concurrency/ordering proof and BR-1–BR-6
remain separate; L1 supplies no scheduler or feedback authority.

VP-01 reconciliation and L4 (VP-06/07) still need actual matched baseline/Lean
episodes with exact policy, toolchain/profile, failure class, complete phase
coverage and unchanged correctness/conformance. Preserve disclosed missingness
and matched sample denominators; speedup remains UNKNOWN until measured.
No L1 fixture or document closes those exits.

Before any [KA-01](AIOS-KERNEL-KA01-PROSPECTIVE-AMENDMENT.md) opt-in, independently
resolve all G1–G6: Human ratification; exact normative publication; separately
admitted/reviewed/published engineering; actual VP-01/VP-06 measurements; real
VP-07 positive/negative conformance (AC-01–AC-36); and separate explicit Human
version/policy/profile opt-in with rollback. This slice discharges none by
assertion. Human retains risk, opt-in and Executor selection; Brain owns proof
intent; the admitted Executor owns implementation; Runtime alone owns canonical
verification/evidence; Reviewer and Publisher retain judgment/publication.
No historical artifact, live-proof state, return affinity or roadmap is changed.
