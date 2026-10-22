# Lean VP L3-05: prospective v1.3 admission and resource budgets

This implementation is **NOT_ACTIVATED** in production. L3-06 integrated G3,
independent review/publication, separate authenticated Human G6 and later L4
remain outstanding. No source or test fixture in this slice supplies those
decisions, advances the roadmap or changes earlier TASK/RUN lineage.

## Existing owners and explicit interface

`operator.preactivate_ka01_v13(runtime=..., request=AdmissionRequest(...),
operation=..., execution_profile=..., policy=..., leases=..., lease=...)` is
the version-1 nondefault controlled offline interface. It consumes an already
persisted ACTIVE RUN and the original `RuntimeCompletion`, actual current
`RunLeaseRegistry` holder and exact selected execution profile. It creates no
RUN, lease, adapter, verification runner, strategy or dispatch. A successful
return is `ADMITTED_PREACTIVATION`, with production `NOT_ACTIVATED` and
continuation `NOT_AUTHORIZED`; it is never a LIVE production receipt.

The ordinary `run_task`, `run_repair` and `run_remediation` entrypoints retain
their unopted paths. An explicit `ka01_admission` argument rejects with
`KA01_V13_NOT_ACTIVATED` before repository admission or adapter construction.
There is no production activation producer or silent production callback.

The offline interface requires a controlled local bare Git transport and the
existing ingress policy from canonical main. It uses the existing
`_authenticate_selection_issue` authentication of an allowlisted Human,
original unedited Issue content, repository/actor identities and independent
edit history. Each authority Issue must also remain open. Closing, deleting,
editing or changing the source, or changing its original ingress policy, denies
further work. Reopening a withdrawn source cannot clear this owner's STOP.
This is a versioned consumer of that existing trust root, not a new issuer.

## Exact source records

`AdmissionRequest` has exactly version `1`, `selection_issue`,
`selection_sha256`, `consent_issue` and `consent_sha256`. Digests locate bytes;
they never authenticate themselves. The unique first-party current selector is
`refs/heads/aios/ka01-v13-consent/<RUN>`. Its parentless immutable Git carrier
contains `.ai/transport/ka01-v13.json`, the canonical JSON request. Missing or
competing `<RUN>/*` selectors, mismatched carrier bytes or changed selector SHA
reject. There is no production writer for this prospective selector. Tests
construct controlled offline carriers, as the earlier native fixtures do.

Human Issue bodies use closed canonical JSON schemas:

* `AIOS_KA01_V13_SELECTION`, version `1`: implementation `KA01-L3-05-v1`,
  `ka01_version="1.3"`, profile `KA01-v1.3-default-resource-profile`, exact
  existing TASK verification policy, TASK `{id, revision}`, explicit operation
  list, original remote URL, normative `{source_sha, path, sha256}` and an
  explicit list of override `{issue, sha256}` pins. The normative blob is the
  exact published prospective v1.3 amendment, SHA-256
  `474be079dd641690dbd44652efc54f6d80cc02557c6c8767d7ae0acb6841d280`, anchored
  in current main and unchanged there. Historical versions never select these
  defaults. Existing v1/v2 verification policies and native codecs retain
  their separate meanings; selecting a resource profile cannot waive proof.
* `AIOS_KA01_V13_OVERRIDE`, version `1`: `ka01_version`, exact TASK reference,
  explicit applicable operations and a nonempty `values` object naming only
  independently overridden resource fields. Every named Issue is independently
  authenticated. Duplicate or overlapping fields reject, including equal-value
  overlaps. A malformed, stale, revoked or inapplicable override is never a
  reason to default. Selection and override sources are operational delegation;
  neither changes the semantic TASK or supplies per-RUN consent.
* `AIOS_KA01_V13_RUN_CONSENT`, version `1`, mode
  `CONTROLLED_PREACTIVATION`: exact implementation/version, selection digest,
  TASK reference, RUN id and original admitted-RUN byte digest, one operation,
  selected Executor, full execution-profile digest, authenticated operation
  source digest and merged effective-limit digest. Another RUN's consent,
  PRIMARY receipt, reusable preference or caller approval boolean cannot fill
  a missing field. This controlled mode is not a production Human G6 decision.

PRIMARY authenticates its original canonical admission artifact, TASK/base and
current clean scoped candidate through the existing native source reader.
REPAIR independently resolves the original predecessor FAILURE and current
AUTHOR_REPAIR selector, validates the complete existing repair contract and
matches its original protected execution/action/scope/root/affected proof.
REMEDIATION independently resolves its selected current source RUN,
CHANGES_REQUIRED review, outstanding finding and exact remediation selector;
it retains the existing review/remediation validators, affected proof and
cumulative execution-base checks. A forged predecessor, changed action/scope,
closed finding, stale authorization or cross-operation receipt cannot replace
those sources. DIRECT_CANDIDATE, pre-RUN and terminal sources reject. Batch
native continuity is not established by the single-finding interface.

REPAIR and REMEDIATION can resolve their own admission facts on controlled
sources, but return native continuity **BLOCKED**. Their explicit prospective
Dispatcher paths stop before constructing an adapter. This does not simulate a
live correction invocation or borrow L3-04's PRIMARY callback. Production
adapters have no newly installed correction callback.

## Frozen limits and accounting

| Field | Published default |
| --- | --- |
| `max_corrections` | 2 |
| `max_checkpoints` | 3 |
| `max_run_wall_minutes` | 240 |
| `max_runtime_verification_minutes` | 180, included in wall |
| `max_tokens` | null |

Counts must be integers, excluding booleans; corrections may be zero and
checkpoints must be at least one. Minute limits must be positive finite
numbers with verification at most wall and exact nanosecond conversion.
Tokens must be null or a positive integer. Merge validation includes all
cross-field constraints. Allowed transitions are at most
`min(max_corrections, max_checkpoints - 1)`; K1 still fits a zero-correction
profile. Finalizing an already proved checkpoint adds no checkpoint/correction.
Existing stricter native proof, capture, byte, time, watchdog and provider
constraints remain operative and may stop earlier.

The immutable admission lives under the original per-RUN protected verification
store, `ka01-v13/admission.json`. It records the complete effective limits,
normative/version/policy/profile sources, every Human provenance, exact
operation, original source facts, actual profile and an opaque original lease
binding. Returned dictionaries are detached; the retained limits are read-only.
The original lease object/registry and active invocation remain process-local
capabilities. Disk records cannot reconstruct them.

Timing starts at the original `RunObservationTracker.admit(run)` boundary,
using that same original tracker and native monotonic clock. Missing timing,
previous native invocation/verification, a substituted clock/tracker, clock
discontinuity or existing unaccounted proof work blocks admission. Wall covers
all elapsed Executor work, waits, preparation, validation, feedback,
verification and terminal work. Verification preparation and current
authentication/IO are measured together with proof work. Distinct intervals
accumulate; wall and verification are not added as separate allowances. Each
existing verifier subprocess receives the smaller of its existing timeout and
the original remaining wall/verification time.

Immutable `eNNN.json` reservation/settlement records are subordinate to that
same store. All writes use the existing `.runtime-mutation/intent.json` fence,
exclusive-create/fsync helper and original Runtime checkpoint, feedback and
terminal writer. There is no second lock, scheduler, lease registry, reducer
service or proof authority. Count reservations, spent counts, cumulative time
reservations, attributable settlement totals and measured spent costs never
decrease. A settlement records the provably unused portion of its own time
reservation; it never refunds consumed time. Unknown interrupted effects keep
their outstanding reservation and original fence/captures. Known elapsed lower
bounds are retained where the clock remains trustworthy. Protected bytes and
the original owner's counter facts are checked on subsequent boundaries.

K1 reserves a checkpoint before proof. Feedback consumption reserves one
correction before the original same-holder callback; the next checkpoint
requires that consumption. A new committed candidate must retain the complete
native proof population and strictly reduce original outstanding obligations.
No causal progress or unknown proof stops further work. Exact checkpoint or
feedback replay does not rerun proof/callback, reserve another count or charge
the original execution again. Its actual current validation time still counts.
Concurrent/detached access, lost original lease/invocation, changed profile,
withdrawal, terminal state and STOP cannot create replacement authority.

Null tokens require no token input or invented finite KA-01 ceiling. Existing
native usage observations do not authenticate the complete cumulative cost
population including every feedback/re-entry. Therefore a non-null cap currently
fails closed with `AUTHENTICATED_CUMULATIVE_TOKENS_NOT_ESTABLISHED`. Neither a
caller estimate nor partial provider counters can authorize capped work. A
future integrated transport must establish that complete population separately.

## Regression definitions and Runtime-owned verification

`tests/test_lean_vp_l3_admission_budget.py` defines controlled real-Git admission,
independent per-field Human overrides, exact consent, wrong/missing/edited or
withdrawn sources, count/time/token stops, native PRIMARY proof and callback,
replay, progress/finalization, uncertain settlement, concurrency, independent
correction source authentication and explicit unsupported-continuity cases.
The scoped existing tests add legacy isolation and production gate cases.
Historical cold codec and original same-holder cases remain in place.

Focused Runtime checks include the new admission file and unchanged direct
native file, plus the added `v13`/`unopted` Runtime/operator cases. Preserve the
existing ordinary operator PRIMARY/REPAIR/REMEDIATION cases, particularly
`test_operator_delegates_one_primary_invocation_to_dispatcher`,
`test_continue_implementation_repair_binds_exact_failed_run_and_preserves_runtime_boundary`
and `test_remote_remediation_resolves_lineage_and_uses_normal_boundary`.

TASK-398 and TASK-399 regression sources are unchanged. The focused shared-path
Runtime checks must include the existing
`tests/test_decision_guard_optimization.py::test_complete_native_snapshot_source_binding_and_bounds`,
`::test_warm_fresh_recheck_detects_every_material_ref_change`,
`::test_eligible_historical_pass_is_a_blocker_in_either_ref_order` and
`tests/test_unresolved_failure_handoff.py`. They retain their current remote
association, fresh recheck, historical PASS blocking, bounded diagnostic,
currentness and immutable terminal expectations. This slice supplies no new
baseline measurement and changes none of those tests or their authorities.

These are verification definitions, not a claim of Runtime EVIDENCE or PASS.
Canonical verification, semantic Review, publication and roadmap reconciliation
remain with their existing downstream owners.
