# AIOS-renew v0.1.4 Direct Candidate Acceptance

**Status:** Candidate amendment to the frozen v0.1 architecture  
**Scope:** Human-attested admission of an already committed CODE_FIX candidate

This amendment adds one narrow acceptance path. It does not change the frozen
TASK, RUN, RESULT, EVIDENCE, REVIEW, or REMEDIATION contracts and does not alter
PRIMARY execution, normal REMEDIATION execution, REPAIR, terminal transport
retry, or publication behavior.

## Human request and authority

The Human requests Direct Candidate Acceptance with exactly three semantic
inputs: the TASK identifier, finding identifier, and selected Executor identity.
The Human does not supply a REVIEW path, REMEDIATION path, reviewed SHA,
modification scope, affected-verification command, or structural ResultPackage.

Before acceptance, the Human is responsible for selecting one Executor and
ensuring that it alone holds mutation authority while producing the candidate.
The candidate must already be committed at the repository's current HEAD. This
mode is therefore **Human-attested before acceptance**; Runtime does not claim an
equivalent pre-acceptance lease or proof of earlier Executor exclusivity.

## Deterministic remote lineage resolution

At the acceptance boundary Runtime acquires its repository mutation guard and
uses the configured upstream Git remote. It resolves remediation refs under
`refs/heads/aios/remediation/`, selects the requested finding, and binds the ref's
single REVIEW and REMEDIATION to the authoritative source RUN and ResultPackage
under `refs/heads/aios/artifacts/<RUN_ID>`.

The frozen validators must establish one exact lineage: the source RUN references
the requested TASK and revision; its persisted result binds `reviewed_sha`; the
REVIEW is `CHANGES_REQUIRED`; the finding exists; and the REMEDIATION is a
CODE_FIX with the same finding, action, reviewed SHA, TASK-bounded scope,
original constraints, and nonempty affected verification. Missing, malformed,
conflicting, or multiple matching lineages fail closed.

## Pre-admission gates

Before writing a canonical execution record, Runtime requires:

- a clean worktree;
- a candidate HEAD different from `reviewed_sha`;
- `reviewed_sha` to be an ancestor of candidate HEAD; and
- the Git-derived exact changed-file delta to fit both REMEDIATION
  `modification_scope` and original TASK mutation authority.

Rejection at these gates creates neither a remediation RUN nor a canonical
execution FAILURE.

## Admission, verification, and review continuity

An admissible candidate enters one canonical REMEDIATION execution record. That
record binds the original TASK, REVIEW, finding, reviewed SHA, selected Executor
identity, and immutable candidate HEAD, while recording Direct Candidate mode as
operator metadata. Runtime does not launch the selected Executor.

Runtime derives `result.head_sha` and `result.changed_files` from Git. The
remediation result has no Executor-authored claims, unresolved items, or evidence.
Runtime executes only the canonical REMEDIATION affected verification and creates
the canonical EVIDENCE bound to the admitted candidate SHA. It does not rerun
unaffected original TASK verification merely because Direct mode was used.

After successful verification Runtime rechecks that HEAD is unchanged and the
worktree remains clean, persists the canonical result, and uses the existing
post-PASS `aios/review/<RUN_ID>` plus `aios/artifacts/<RUN_ID>` transport. ChatGPT
therefore receives the same DELTA review continuity as normal remediation.
Failures after admission remain ordinary pre-PASS failures. Transport failures
after result persistence remain eligible for the existing terminal transport
retry.

## Idempotency and exclusions

A repeated request for the same canonical REVIEW, finding, and unchanged
candidate SHA returns the existing accepted remediation outcome, independent of
the Executor identity supplied by the repeated request. The outcome preserves
the Executor identity recorded by the original accepted RUN; it neither invokes
an Executor nor reruns affected verification.

Direct Candidate Acceptance does not apply to PRIMARY execution, EVIDENCE_ONLY
remediation, REPAIR, automatic publication, retry, reroute, fallback, planning,
polling, or semantic correctness inference.
