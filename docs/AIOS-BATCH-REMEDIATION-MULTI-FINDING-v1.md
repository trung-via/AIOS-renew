# Batch REMEDIATION and multi-finding DELTA REVIEW v1

Status: prospective Runtime extension. Kernel v0.1, historical bytes/refs,
PRIMARY reviews, and single-finding REMEDIATION/DELTA contracts keep their
existing meanings. This document grants no execution or publication authority.

## Exact selection and identity

A Human explicitly selects **2–32 distinct CODE_FIX findings** from one canonical
CHANGES_REQUIRED REVIEW. Selection never follows a model's inferred grouping or
an automatic choice of outstanding findings. One selection carries these exact
fields (no aliases):

```yaml
source_run_id: RUN-379-001
review_id: REVIEW-379-001
task_id: TASK-379
task_revision: 1
reviewed_sha: <40 lowercase hex characters>
review_decision_sha: <40 lowercase hex characters>
source_artifacts_sha: <40 lowercase hex characters for the source RUN/RESULT/EVIDENCE commit>
finding_ids: [F1, F2]
```

The examples are placeholders, not permission to correct this document's TASK.
The source RUN must belong to the selected TASK namespace. Runtime authenticates
the source candidate, immutable REVIEW metadata commit, source RESULT/EVIDENCE,
TASK contract and revision through the existing owners. Canonical ref movement is
rechecked at mutation boundaries. Each selected subject copies the original
finding's `id`, `basis`, `action`, `location`, `issue`, and `expected` exactly.
Foreign, duplicate, stale, mixed-action and implicitly selected subjects fail.
EVIDENCE_ONLY remains a single-finding operation.
Batch family documents are bounded to 262,144 UTF-8 bytes and reject duplicate
mapping fields and YAML anchors/aliases. Recursive batch source proofs are
bounded to 32 links and reject cycles.

`BatchSelection` normalizes the explicit set into lexically sorted finding IDs.
`batch_id` is `BATCH-` plus SHA-256 of its UTF-8 JSON mapping, with sorted keys,
compact separators and the default ASCII JSON escaping. Sorting changes no
membership. This address covers the source REVIEW decision and every selection
field. A changed source or membership is a different selector.

## Final authoring and Human intent

The existing `AUTHOR_REMEDIATION` ingress accepts a version-1 envelope with
identity `{source_run_id, batch_id, finding_ids}` and expected state containing
both `expected_reviewed_sha` and `expected_review_decision_sha`. The final payload
is the closed `AIOS_BATCH_REMEDIATION`, version 1 mapping:

- `batch_id`, `selection`, and sorted original `findings`;
- `per_finding_scope`: exactly one `{finding_id, modification_scope}` entry for
  each selected finding, in selection order;
- `modification_scope`: the exact sorted union of those explicit file paths;
- `action: CODE_FIX`, `verification_policy: minimum-sufficient-v1`,
  `affected_verification`, and `constraints`.

Every modification path must be an exact original TASK modification path.
Glob/traversal paths and ambiguous aliases are rejected. Hard constraints equal
the original ordered TASK constraints. The verification list equals the original
ordered `TASK.verification.required` list, validated under minimum-sufficient-v1.
The union does not grant wider policy or verification authority. v2 verification
TASKs are outside this first batch contract.

The existing immutable ref and metadata family is retained:
`aios/remediation/<source_run_id>-<batch_id>` and
`.ai/remediations/REMEDIATION-<source_run_id>-<batch_id>.yaml`. The commit's sole
parent is the exact source REVIEW decision. Identical normalized replay is
read-only; a conflicting contract at the same identity fails. Original bytes and
commit identity remain immutable. Active overlapping batch/single authorizations
are rejected before authoring or admission.

The GitHub Human Issue carrier accepts `AIOS_REMEDIATION_INTENT_REQUEST`, version
3, with `correction_dispatch_id`, `source_run_id`, **`batch_selection` containing
the complete explicit selection above**, `executor`, `model`, and
`reasoning_effort`. Version 2 keeps its single `finding_id`; a BATCH selector in
that single-finding request is explicitly rejected. The authenticated Issue actor
and existing repository carrier policy remain the Human authority boundary.

The reusable approved intent/wakeup workflows carry the derived BATCH selector
in their existing bounded `finding_id` transport slot. This spelling is a carrier
slot, not a claim that the batch is a legacy finding. Manual dispatch requires
the exact content address of the set the Human has inspected. Runtime resolves
and authenticates the complete batch before recording/requiring A3 approval.
The A3 journal uses its batch version 2; A6 uses journal version 3 and binds the
entire selection, immutable remediation SHA, Human approver and resolved Executor
profile. Reusing a delivery with changed selectors, authority or profile fails.

Carrier, runner and transport receipts assert delivery facts only. Pre-AIOS
operational failures retain `run_created=false` and `executor_invoked=false`;
they cannot fabricate an engineering RUN, FAILURE, verified PASS or semantic PASS.

## One admitted correction and verification episode

Runtime admits one Executor, one RUN and one cumulative candidate using the
existing dispatcher and completion owner. A batch cannot use direct-candidate
acceptance to bypass the Executor requirement. The current cumulative tip must
have an independent REVIEW, all selected original findings must still be
outstanding, and the tip must descend from their reviewed SHA. Existing integrated
execution-base authority is reused when canonical main requires integration.
Competing continuations, incomplete local RUNs, consumed authorization, wrong
base candidates and stale immutable approvals fail before another invocation.

The persisted wrapper has `kind: REMEDIATION`,
`format: AIOS_BATCH_REMEDIATION_RUN`, `version: 1`, the exact authorization SHA,
the full selection as `predecessor`, a mandatory cumulative `execution_base`, and
`AIOS_BATCH_REMEDIATION_EXECUTION`, version 1. Execution serializes **`findings`**,
never a singular `finding`. The original source identities remain distinct from
the later operational base. The Executor receives the combined authorized scope
and exact original subjects/constraints and commits one permitted delta.

The ordinary RESULT shape remains sufficient: empty correction claims and
unresolved lists, the real final committed `head_sha`, and exact changed paths.
Runtime owns verification and generates one episode on that candidate. The
batch gate requires exactly one successful `VERIFICATION` record per authorized
command in order, with unique evidence IDs, the exact RUN ID and RESULT SHA, and
integer exit code zero. Missing, extra, substituted, duplicated or nonzero
observations fail; the committed delta must be nonempty, descend from the
execution base, and stay within the exact union. Executor-supplied evidence is
not promoted to canonical verification authority.

## Independent review and partial outcomes

The Reviewer authors `AIOS_MULTI_FINDING_DELTA_REVIEW`, version 1. Its closed
fields are `format`, `version`, `review_id`, `reviewed_sha`, `mode: DELTA`,
`verdict`, `acceptance`, `findings`, `batch_id`, `selection`, and
`finding_outcomes`. The selection must equal the admitted batch. The outcome
mapping contains **every selected finding exactly once**, each with `PASS`,
`CHANGES_REQUIRED` or `BLOCKED`. Missing/foreign/duplicate/contradictory outcomes
fail closed. Tests and Executor assertions never supply these semantic verdicts.

Each selected finding's acceptance basis is assessed. CHANGES_REQUIRED outcomes
require a FAIL on their basis. Several findings can share a basis; per-finding
outcomes still remain independent. `findings` records newly discovered material
defects under new identities, with a TASK basis assessed FAIL. It must not mint
replacement identities for unresolved original subjects.

The overall verdict is deterministic from the independently supplied semantics:
BLOCKED if any selected outcome is BLOCKED; otherwise CHANGES_REQUIRED if any
selected outcome requires changes or there are new defects; otherwise PASS.
PASS requires no supplied acceptance FAIL. A BLOCKED batch can close its explicit
PASS findings while retaining every other outstanding finding and exposing the
blocker. This does not grant another execution automatically.

The prospective Reviewer return parser accepts `AIOS_BATCH_REVIEWER_RETURN`,
version 1, with exact `batch_id`, verdict, acceptance entries, identity-free new
finding semantics, and one `{finding_id, verdict}` outcome per selected subject.
The legacy return parser explicitly rejects batch-shaped material. Runtime still
owns canonical REVIEW/new finding identity materialization and SUBMIT_REVIEW.
Callers that only support the legacy return registry must fail closed and use
the explicit versioned batch return/ingress boundary; no registry migration is
implicit.

Semantic review scopes and material packages use version 2 for batches, bind the
full selection and batch ID in their fingerprints, and keep `prior_finding_id`
null. They expose the original semantic source plus the latest cumulative delta.
The Reviewer also receives the authenticated original REMEDIATION subjects. A
legacy-only consumer must reject this version instead of choosing one finding.
Unified State and ready correction preflight projections also use version 2
when carrying a batch selector and expose its full selection explicitly.

## Frontier, recovery and publication

CorrectionFrontier removes only selected findings independently marked PASS.
CHANGES_REQUIRED/BLOCKED findings retain their original source RUN, REVIEW,
reviewed SHA and finding bytes. Foreign/unselected findings remain. New defects
are attributed to the new correction RUN/REVIEW/candidate. Applying a conflicting
or already-closed predecessor fails. Unified State reduces that complete ancestry
and exposes the remaining frontier; a batch PASS alone cannot close unrelated
findings.

Partial review is terminal for that immutable batch authorization. The Human can
authorize an explicit new supported selection from outstanding findings (or a
single correction), on the latest lawful cumulative candidate. The prior batch
cannot be consumed again under a different delivery ID. An identical original
authoring or delivery replay remains read-only/reconciliatory. Mixed-source
recovery groups are outside v1.

Execution failure retains the ordinary attributed FAILURE and exact batch
lineage. v1 explicitly rejects routing a failed batch into legacy single-finding
REPAIR or direct-candidate acceptance (`BATCH_REPAIR_UNSUPPORTED_V1` /
`BATCH_DIRECT_CANDIDATE_UNSUPPORTED_V1`). Such recovery requires fresh explicit
Human/Brain authority for a supported recovery contract; neither a retry engine
nor conversion to one selected finding is inferred. Existing legacy recovery
behavior remains unchanged.

Publisher authenticates the exact reviewed batch candidate, its immutable
authorization, real RESULT/EVIDENCE and complete cumulative frontier. It
re-derives closure, requires an empty frontier, rejects competing continuation,
and rechecks the task's correction ref set before publication. Partial resolution,
BLOCKED, unselected or newly discovered outstanding findings block publication.
The existing reservation, main compare-and-swap and historical publish/replay
authority remain in place; there is no second Publisher.

## Verification scope and performance claims

Focused disposable-Git integration cases cover two findings in one RUN, partial
and blocked outcomes, retained unrelated/new findings, mixed/duplicate/stale/
foreign selections, forged evidence, conflicting correction, wrong base,
publication blocking and the historical single-finding flow. Runtime owns the
TASK's one minimum-sufficient-v1 verification command; this extension adds no
full-suite requirement.

Real-world time and token savings are **unknown**. Sharing one execution and one
verification episode can avoid repeated work, but this version makes no measured
or guaranteed savings claim. No roadmap state, Kernel policy, Executor/model
choice, admission, independent Reviewer PASS or publication eligibility follows
from this document.
