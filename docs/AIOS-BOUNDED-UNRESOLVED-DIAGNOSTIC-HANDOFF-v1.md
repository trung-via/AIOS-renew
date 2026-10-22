# Bounded unresolved FAILURE handoff v1

Human's 2026-10-22 immediate priority is TASK-399 revision 1, planning ID
`bounded-unresolved-diagnostic-handoff-20261022`. This is the sole current NEXT
in `.ai/roadmap-state.yaml`, its active track and its planning-document pointer.
The prior G1/TASK-398 planning record remains historical. Its reviewed/published
status may be used only with independently authenticated engineering lineage;
this planning amendment supplies no new completion or publication proof.
Conditional Publisher residual G2 is **not started**. After this handoff,
Human/Brain explicitly reconcile G2 eligibility and the paused, resumable Lean VP
v1.3 plan. L3-05, L3-06, G3, separate G6 and L4 remain unfinished; the original
16 slots and all earlier identities remain intact. RESULT, REVIEW and publication
never automatically advance this planning NEXT.

## Original terminal binding

The ordinary Runtime completion path writes the structural ResultPackage before
the completion gates. Runtime retains a process-local receipt of the exact bytes,
SHA-256, size, TASK digest/revision, RUN digest and staged head. Executor JSON or
a local file alone cannot mint this receipt. At the original unresolved terminal
write Runtime rereads that exact regular file, checks the receipt, validates the
closed structural envelope and pins its count and safe anchors in private FAILURE.
The public summary binds the canonical digest of that entire private FAILURE,
the TASK revision, RUN, failed and staged heads, staged byte digest and length,
and the virtual `runtime-staging:RUN` / `runtime-failure:RUN` identities. These
virtual identities cannot select filesystem paths.

Nonempty unresolved items still cause the original hard COMPLETION_GATE failure.
No diagnostic is a PASS, independent verification, EVIDENCE or repair choice.
Original FAILURE is write-once. Replaying or reading cannot rewrite it or restore
an AVAILABLE observation from changed, missing or substituted staged bytes.

## Public and authorized views

Public FAILURE V1/V2 and safe detail V1-V3 retain their decoders. New unresolved
detail uses safe detail V4 with the closed `AIOS_UNRESOLVED_DIAGNOSTIC` v1 field.
It exposes fixed cause `UNRESOLVED_ITEMS`, immutable source identities, exact
observed count or null, typed availability/blocker, and bounded safe anchors.
`PINNED` means observed at the original terminal; `AVAILABLE` requires a fresh
authorized source read. Immutable public FAILURE never asserts current private
availability. Proven zero is represented by integer zero only after actual
structural-source observation; it never changes the non-PASS terminal.

The existing self-host control-plane `observe_runtime_failure_detail` authenticates
the original private FAILURE digest and exact RUN/TASK/HEAD, checks the persisted
RUN and TASK digest, and locates only the recorded staging file. It verifies size
before parsing, raw SHA-256, duplicate-free JSON, closed structural schema and
empty Executor evidence, then recomputes count and safe anchors. It does this even
when public safe detail is present. `read_executor_unresolved_diagnostic` uses the
same projection with an exact already-admitted ACTIVE RUN and matching persisted
admission. The existing `read_executor_private_diagnostic` API also accepts the
read-only virtual selector `runtime-unresolved:RUN` through its unchanged exact
private-FAILURE admission/digest gate and returns only this safe view. The selector
adds no source, command, paging or correction authority. Neither API accepts
pasted source bytes, a source path, a source hash
as proof, or a paging selector as authorization. Protected full-source readers
retain their existing separate authorization and never feed portable contexts.

Portable Brain Sync, Brain work context, Unified State and terminal attention
consume this same safe field. Owner-gated attention rechecks canonical refs after
the local read. Source changes return `SOURCE_MISMATCH`; absent files return
`SOURCE_MISSING`; malformed source returns `SOURCE_INVALID`; unauthorized or
linked/reparse sources return `ACCESS_DENIED`; exhausted bounds return
`LIMIT_EXHAUSTED`. Sources without an original byte pin return `LEGACY_UNBOUND`.
Blockers contain no exception text, private path, unresolved text or invented count.

## Discriminating facts and limits

Every anchor carries the original item index and opaque SHA-256. AC anchors must
be declared in the exact TASK. Repository paths must be safe relative docs, tests,
source or TASK paths in its inspect/modify allowlist. Test identities must match
definitions observed by Runtime in committed test blobs at the failed SHA;
parameters and unrecognized identities are withheld. This immutable allowlist
is protected by the private FAILURE digest and reused without repeated Git reads.
Items without safe references expose an opaque digest only. These facts permit
Brain to compare missing implementation, docs versus tests, contract text and
absent evidence against the authenticated repository. They never classify an
Executor's prose as a semantic verdict. `SEMANTIC_CAUSE_UNKNOWN` and
`UNRESOLVED_TEXT_WITHHELD` are always explicit. Missing test identity proof and
anchor truncation have their own fixed limitations.

Input is at most 16 MiB, with at most 65,536 separators/containers and unresolved
items, strict duplicate and nesting rejection, regular-file/race checks and no
fallback sources. At terminal pin time test inventory is limited to eight 256 KiB
Git blobs, at most sixteen local Git calls, two seconds per call and ten seconds
total. No fetch, command from source text, test execution or persistent cache is
introduced. Repeated diagnostic reads perform fixed local reads and bounded
repository-location lookup, without repeating inventory Git or fetching objects.
Each diagnostic, safe detail and unresolved FAILURE context has at most 32 anchors
and at most 8 KiB portable JSON; anchor encoding reserves space for enclosing
contexts. Brain Sync/work serializers enforce the same 8 KiB bound for unresolved
views and redact the operational repository root. Oversize views fail closed with
`LIMIT_EXHAUSTED`. TASK-393/394 pytest paging and private capture budgets are unchanged.

## History and activation

RUN-398-004 / TASK-398 r1 remains immutable: artifact
`70fec2ab3f681f51c3bdfa40f51bcba50fa33852`, failed head
`2e97623c2ac283e151ffdc41485a90b8df14b19d`, private FAILURE digest
`b2e60c61f1d6885802e68c81612355445b290c936754f7eda6faba77b17b0455`.
Its exact staged-byte digest is unknown. A read-only legacy view must not
backfill it or infer a count from present-day local staging.

The implementation is wired into ordinary completion, projection and reader
paths with no feature flag or later activation Task. Its own Runtime RESULT,
independent Reviewer PASS and exact Publisher source publication remain the
existing activation gates. The Executor cannot publish or claim those outcomes.
The handoff creates no RUN, retry, wake, correction, REVIEW, mutation authority,
automatic Executor selection, second lifecycle engine or policy change.
