# AIOS GitHub -> ChatGPT Brain Wake Automation Audit v1

Status: HUMAN/BRAIN PLANNING AUDIT  
Approved priority: 2026-09-30  
Architecture subject main: `8b8208149c5162832efc10231464e2d326962c67`  
Scope: automation/wake transport and continuity only; no lifecycle authority transfer

## 1. Human objective

Before resuming semantic-handoff hardening implementation, complete the missing
automation loop between GitHub and ChatGPT Brain.

Target operating shape:

```text
ChatGPT Brain authors semantic action
        |
        v
GitHub / AIOS deterministic admission + execution
        |
        +-- deterministic success that needs no Brain --> continue automatically
        |
        +-- failure / semantic checkpoint --> wake ChatGPT Brain
                                              |
                                              v
                                        fresh Brain Sync
                                              |
                                   reason / review / correction
                                              |
                                              v
                                      write GitHub ingress
                                              |
                                              +---- loop
```

The wake mechanism is a transport. It must not become Planner, Reviewer, correction
selector, roadmap authority or lifecycle truth.

## 2. Current-state audit

AIOS already has strong GitHub -> Runtime carriers and canonical lineage, including:

- GitHub Issue ingress for AUTHOR_TASK / SUBMIT_REVIEW / AUTHOR_REMEDIATION /
  AUTHOR_REPAIR.
- deterministic safe publication dispatch after eligible review ingress.
- deterministic REPAIR wakeup dispatch after eligible REPAIR ingress.
- terminal attention over exact RUN / terminal kind / artifact SHA.
- canonical FAILURE / RESULT / REVIEW / REPAIR / publication evidence.

The missing edge is **GitHub -> ChatGPT invocation**. Existing attention code can
produce/validate an operational signal but cannot start a fresh ChatGPT turn by
itself. Today a Human message such as "Kiểm tra" or an already-active Brain turn
performing polling closes that gap manually.

## 3. Current ChatGPT-native event capability

As of this audit, documented ChatGPT Work event-triggered tasks can respond to
supported GitHub pull-request activity in an authorized github.com repository.
Supported activity can include pull-request comments and commit updates depending on
the selected trigger.

The current documented GitHub event-trigger surface is not a generic arbitrary
GitHub-Issue/workflow-run webhook into an existing ordinary ChatGPT turn. Therefore
the lowest-complexity ChatGPT-native design is to use one GitHub PR activity surface
as a **wake carrier**, while keeping AIOS canonical refs/artifacts as truth.

No OpenAI API Brain service, Slack intermediary, message broker or persistent
orchestration database is required for the first implementation.

## 4. Proposed minimal wake carrier

Use one dedicated long-lived GitHub pull request per controlled repository, for
example an operational PR named "AIOS Brain Wake Bus".

A deterministic GitHub workflow emits a bounded PR comment only when Brain attention
is required:

```text
[AIOS BRAIN WAKE]
version: 1
event_id: <deterministic bounded identity>
attention_family: <bounded enum>
selector:
  ...
fresh_brain_sync_required: true
```

The comment is **not truth**. It is only a webhook-compatible bell.

The event-triggered ChatGPT Work task wakes on matching PR-comment activity, receives
the bounded selector, then:

1. ignores the comment for semantic truth;
2. performs fresh Brain Sync against canonical `main` and exact AIOS lineage;
3. verifies that the attention event remains current/unresolved;
4. resolves the correct flow through existing Flow Resolver / Decision Packet;
5. performs Brain-owned semantic work;
6. writes only the next authorized GitHub ingress/action;
7. stops.

Every new GitHub result creates a new wake only when Brain authority is needed.

## 5. Why PR comments, not a new custom service

This design deliberately reuses two already available systems:

- GitHub Actions / canonical AIOS state;
- ChatGPT Work event-triggered GitHub PR tasks.

Rejected as first-line architecture:

- **Slack relay:** feasible but adds another provider and another state/permission
  surface.
- **Custom OpenAI API Brain daemon:** feasible but changes cost/provider/runtime
  assumptions and no longer uses ChatGPT product as the primary Brain.
- **High-frequency polling:** simpler but wastes calls and increases latency; retain
  only as optional recovery fallback if native event delivery proves lossy.
- **PR body as lifecycle state:** rejected; the PR is a wake bell only.

## 6. Attention event matrix

The automation must be audited against the whole flow, not only AUTHOR_TASK.

### 6.1 Brain wake REQUIRED

- Brain ingress rejected or carrier delivery failed.
- PRIMARY/REMEDIATION/REPAIR dispatch rejected after canonical authorization.
- pre-AIOS runner/transport failure requiring semantic or operational diagnosis.
- canonical RUN FAILURE.
- canonical RUN RESULT requiring semantic REVIEW.
- review ingress rejected.
- REVIEW CHANGES_REQUIRED or BLOCKED requiring Brain semantic follow-up.
- REMEDIATION/REPAIR authoring rejection.
- repair/remediation execution FAILURE.
- publication dispatch or publication execution failure.
- successful publication when Human/Brain roadmap reconciliation or the next
  semantic commitment is required.
- explicit canonical conflict/staleness that invalidates a previously prepared Brain
  candidate.
- wake-delivery recovery indicating an unresolved attention event.

### 6.2 Brain wake NOT REQUIRED

- ordinary ingress success when deterministic dispatch can proceed.
- dispatch accepted.
- RUNNER_STARTED / executor in-progress.
- Runtime verification in progress.
- successful deterministic transport bookkeeping with no semantic decision.
- auto-publication start after an eligible PASS.

These remain operational signals and must not consume Brain calls.

## 7. Wake payload boundary

The wake payload should contain only enough information to reconstruct canonical
truth, for example:

```text
event_id
repository
attention_family
canonical selector(s)
fresh_brain_sync_required: true
```

Allowed selectors include exact TASK/RUN/REVIEW/finding/publication identifiers or
immutable ref/SHA identities already recognized by AIOS.

The wake payload must not contain:

- authoritative next action;
- correction strategy;
- Reviewer verdict not already canonical;
- roadmap successor;
- copied TASK semantics;
- raw logs or reasoning;
- provider/model/session identity as semantic authority.

## 8. Brain behavior after wake

The event-triggered Work task must act as a **fresh Brain**, not as a continuation
that trusts prior chat memory.

Its bootstrap instruction should be thin:

1. target canonical repository;
2. parse the exact attention selector;
3. perform Brain Sync;
4. ignore stale/resolved events;
5. resolve the flow;
6. apply repository-owned audit profile where required;
7. take exactly one authorized semantic step;
8. write the resulting GitHub ingress/action;
9. stop and await the next wake.

This makes chat identity and conversation memory non-authoritative.

## 9. Loop examples

### Authoring rejection

```text
Brain -> AUTHOR_TASK Issue
GitHub ingress -> FAIL AUDIT_HANDOFF_MISSING
GitHub -> wake comment
ChatGPT Work wakes
fresh Brain Sync -> TASK_AUTHORING
Stage 1 -> Stage 2 -> reconciled candidate
Brain -> corrected AUTHOR_TASK Issue
GitHub ingress -> PASS
GitHub dispatches PRIMARY
```

### Successful execution

```text
PRIMARY -> RUN -> RESULT
GitHub -> wake comment
ChatGPT Work wakes
fresh Brain Sync -> SEMANTIC_REVIEW
Brain -> SUBMIT_REVIEW
PASS -> GitHub auto-publish
publication success -> wake Brain
fresh Brain Sync -> roadmap/next semantic decision
```

### Failure / repair

```text
RUN -> FAILURE
GitHub -> wake comment
ChatGPT Work wakes
fresh Brain Sync -> REPAIR_AUTHORING
Brain chooses strategy from canonical facts
Brain -> AUTHOR_REPAIR
GitHub dispatches repair
...
```

## 10. Idempotency and loop safety

Every wake must use a deterministic event identity derived from its canonical
attention subject. Duplicate webhook delivery is acceptable.

On wake, Brain must first determine whether the event is still unresolved. If the
canonical state already contains the successor artifact or a newer conflicting
state, the task performs no mutation.

The wake task must never recursively react to its own ordinary GitHub ingress writes.
Only the dedicated wake PR marker/condition triggers it.

A bounded retry is a transport concern only. Semantic retries are not automatic.

## 11. Wake-channel failure

The hardest residual failure is: GitHub creates canonical attention but the wake
carrier itself fails before ChatGPT receives the PR event.

Initial implementation should prove the primary event path first. The audit reserves
one low-frequency reconciliation fallback only if real testing shows it is needed:

- scheduled ChatGPT Work task;
- scans for unresolved canonical attention;
- no more frequent than required;
- same fresh Brain Sync semantics;
- never a second lifecycle authority.

Do not add this fallback preemptively if the native event path is reliable.

## 12. Conformance gates before roadmap resumes

The wake automation is not complete until real end-to-end probes demonstrate:

1. a GitHub PR wake event starts a ChatGPT Work task without a Human chat message;
2. the woken Brain can read the authorized repository and perform fresh Brain Sync;
3. the woken Brain can perform the required GitHub write action unattended under the
   configured app permissions, or the exact approval limitation is documented;
4. duplicate wake delivery produces at most one canonical mutation;
5. stale/resolved wake events produce no mutation;
6. ingress rejection wakes Brain with the exact cause;
7. canonical RESULT wakes Brain for semantic review;
8. canonical FAILURE wakes Brain for correction reasoning;
9. REVIEW CHANGES_REQUIRED wakes Brain for follow-up;
10. PASS -> publication can remain deterministic without an unnecessary Brain call;
11. publication failure wakes Brain;
12. successful publication wakes Brain only when a Brain/Human planning decision is
    actually required;
13. RUNNER_STARTED/in-progress never masquerades as completion and does not wake Brain;
14. pre-AIOS operational failure never fabricates RUN/FAILURE lifecycle truth;
15. the wake task requires no chat-history memory to recover the correct next
    semantic step.

## 13. Relationship to TASK-254 and the existing roadmap

TASK-254 r1 is already authored but has not been executed. Human priority now pauses
its execution.

The wake-automation audit/proof is inserted before H1 because strengthening fail-close
without a return path to Brain would improve safety while leaving continuity manual.

After the wake mechanism passes real conformance, resume TASK-254. H1 then guarantees
invalid semantic authoring is rejected; the wake layer guarantees that rejection
returns promptly to a fresh Brain.

After the hardening track closes, continue VPRC, then perform full downstream
adoption to Python Agent as already approved.


## 14. Post-probe architecture reconciliation

Real conformance changed the implementation choice from hypothetical transport to a
proven native path:

- `wake-probe-1200-001` produced one autonomous ACK after 404 seconds.
- A paired duplicate/fresh probe then produced no second ACK for the already-resolved
  `wake-probe-1200-001` and exactly one ACK for `wake-probe-1200-002` after
  281 seconds.
- Both ACKs carried the canonical `main` SHA observed after the wake comments were
  emitted, which is consistent with fresh repository reread rather than setup-time
  state reuse.
- GitHub comments created through the connected ChatGPT GitHub app are eligible
  trigger events; ACK comments do not recurse because they do not begin with the
  wake marker.

The remaining problem is therefore not GitHub -> ChatGPT delivery. It is the
deterministic bridge from existing AIOS operational/canonical checkpoints into the
already-proven wake PR surface.

### 14.1 Selected minimal bridge

Use **one repository-owned wake bridge**, not one bespoke wake implementation per
lifecycle workflow.

The bridge observes existing GitHub operational surfaces and emits only a bounded
wake comment. It does not mutate TASK/RUN/REVIEW/publication state and does not choose
semantic continuation.

Selected source event families:

1. `issues.opened` for the already-admitted `[AIOS TERMINAL ATTENTION]` Issue.
2. `issue_comment.created` for bounded receipts produced by existing AIOS carrier
   workflows.
3. `workflow_run.completed` for publication completion/failure and pre-AIOS
   operational workflow failure where no canonical RUN/FAILURE may be fabricated.

The bridge writes only to the configured Wake Bus PR.

### 14.2 Repository-owned transport policy

Add one small repository-owned policy binding:

- exact repository identity;
- exact wake PR number;
- wake marker;
- maximum comment size;
- exact trusted GitHub Actions bot identity for AIOS-generated receipt/attention
  sources;
- exact workflow names eligible for `workflow_run` attention.

This policy is operational transport configuration only. It does not select a
lifecycle action or encode roadmap state.

### 14.3 Deterministic event projection

A small deterministic projector converts one admitted GitHub event into either:

```text
NO_WAKE
```

or:

```text
WAKE
event_id
attention_family
bounded selectors
```

The projector must never claim semantic truth from the source receipt. It may only
classify a fixed operational boundary and preserve source selectors that let a fresh
Brain reconstruct truth.

Required first-generation mappings:

- failed Brain ingress receipt -> `INGRESS_REJECTED`;
- canonicalized `CHANGES_REQUIRED` review receipt -> `REVIEW_CHANGES_REQUIRED`;
- rejected PRIMARY/REPAIR/remediation carrier dispatch -> corresponding operational
  rejection family;
- `[AIOS TERMINAL ATTENTION]` Issue -> `TERMINAL_ATTENTION`;
- completed auto-publish workflow -> `PUBLICATION_WORKFLOW_COMPLETED` with
  conclusion and workflow-run selector;
- failed self-hosted PRIMARY/REPAIR/approved-remediation workflow ->
  `PRE_AIOS_OPERATIONAL_FAILURE`.

No wake is emitted for dispatch accepted, runner started, in-progress, successful
self-hosted execution bookkeeping, or ordinary ingress success.

### 14.4 Idempotency

The bridge derives one deterministic event id from the immutable GitHub source event
identity and attention family. Before posting, it scans the Wake Bus PR for an exact
existing wake event id. Existing exact events are reused as NOOP.

No persistent wake database is introduced.

### 14.5 Stage-2 adversarial reconciliation

The mandatory second audit rejected several simpler-looking variants:

- **Modify every existing workflow to post directly to PR #1200:** rejected because
  duplicated event grammar and dedupe logic would drift across workflows.
- **Treat existing receipts as lifecycle truth:** rejected. Receipts are source
  signals only; the woken Brain must fresh-sync canonical state.
- **Use `workflow_run` failure as canonical FAILURE:** rejected. Pre-AIOS
  operational failure remains operational and must never fabricate RUN lineage.
- **Wake on every workflow completion:** rejected because it would waste Brain calls
  and blur deterministic progress with semantic checkpoints.
- **Add a persistent attention database:** rejected; GitHub source event identity plus
  the Wake Bus comment history is sufficient for transport idempotency.
- **Allow bridge payload to carry `next_action`:** rejected because that would create
  a lifecycle router. The bridge emits family + selectors only.
- **Hard-code semantic downstream actions in the ChatGPT automation prompt:** rejected.
  The final Brain task must fresh-sync and resolve the repository-owned flow at wake
  time.

Reconciled outcome: **CLEAR / CANDIDATE** for one bounded wake-bridge implementation
task, followed by real ingress/terminal/publication conformance before TASK-254
resumes.

## 15. Post-publication actor discrimination and TASK-255 r3

TASK-255 r2 reached reviewed publication, then live post-publication P1 exercised the
actual bridge rather than a synthetic comment-only probe. The repository side behaved
as designed: Issue #1215 produced a rejected ingress receipt, source pointer, exact
`workflow_run` bridge projection, and one `INGRESS_REJECTED` wake on PR #1200.
However, wake comment 5921447869 was authored by `github-actions[bot]` and received no
ChatGPT Work ACK after the prior successful-probe latency window had been exceeded by
hours.

A bounded actor-discrimination P1b then posted an otherwise qualifying wake on the same
PR through the authorized GitHub connector as user actor `trung-via`. Event
`wake-p1b-actor-discrimination-20261001-001` was ACKed autonomously after 185 seconds
by comment 5921598459, carrying exact canonical main
`524b7eacb13d51867135f4589b19cb3c3d95d258` and `fresh_brain_sync: true`.

These observations are operational conformance evidence, not a newly invented GitHub or
OpenAI platform contract. They establish that the remaining blocker is the r2
last-mile bot-authored PR-comment delivery path strongly enough to revise the transport
contract, while preserving all r2 source handoff and deterministic projection semantics.

The Stage-1 construction selected a secretless PR-head commit-update transport because
current ChatGPT Work documentation includes PR commit updates among supported GitHub
pull-request activity. Stage-2 adversarial reconciliation added the following mandatory
boundaries before implementation:

- preserve one central projector and change only post-projection delivery;
- keep PR #1200 and `aios-brain-wake-bus-v1` operational-only and never merge them as
  engineering truth;
- bind one exact marker path and permit `contents:write` only in the bridge delivery
  job;
- serialize the Wake Bus globally;
- use a non-trigger PENDING/EMITTED ledger on PR #1200 so replay and partial
  write/ledger failure can be reconciled without semantic choice;
- block an unrelated wake from overtaking unresolved PENDING delivery;
- never fall back silently to a PAT, GitHub App credential, external relay, polling or
  another wake surface;
- keep real ChatGPT Work commit-update invocation outside Executor claims and require a
  fresh post-publication conformance probe.

Reconciled outcome: **CLEAR / CANDIDATE** for TASK-255 r3,
`SECRETLESS_PR_HEAD_COMMIT_UPDATE_V1`. If the later real commit-update Work probe
fails, return to Human/Brain architecture review. Credential-bearing user-attributed
comment transport is not authorized by r3 and requires a separate Human decision.

## 16. TASK-255 r4 — live read-after-write reconciliation

The first live post-publication r3 marker probe used Issue #1220 and produced the exact
INGRESS_REJECTED event
`github-v1-f89372ce2240f2d3d17f7c4c69312045d0c75d0fa3d95516484ea11cbe3c2c01`.
Bridge run 36806928682 attempt 1 created PENDING ledger comment 5923638532 and
successfully committed the exact marker to `aios-brain-wake-bus-v1` at
`70e5b34021c0735728798e1c88cffecd8df7ba2e`, then failed with
`live PR and branch head disagree`.

The failure was not projection, authorization, marker confinement, or write failure.
The branch ref had advanced to the exact returned marker commit while GitHub's PR
representation temporarily still advertised the previous head SHA. Once the PR view
converged, a manual rerun of only the failed ring job recovered the same PENDING entry
to EMITTED without a second marker commit. This live result validates the r3 recovery
ledger but exposes an invalid immediate post-write equality assumption for the normal
first attempt.

Stage 1 keeps the r3 architecture and removes only that assumption. Pre-write admission
remains exact PR-head == branch-ref. After the bridge itself receives one successful
marker write response, completion is bound to the exact transition
`pre_write_head_sha -> write_response_commit_sha`: the branch ref must equal the new
commit, the marker must be read from that exact commit SHA, and PR identity/repository/
branch/base bindings must remain unchanged. During this bounded post-write window only,
PR head.sha may be either the exact old SHA or exact new SHA.

Stage 2 rejected polling, sleeps, automatic reruns, looser arbitrary staleness and branch
alias confirmation. A third PR head SHA, branch movement beyond the returned commit,
identity substitution, marker mismatch, closed/merged PR, or wrong repo/ref still fails
closed before EMITTED. Existing later recovery remains exact and creates no second marker
commit.

Reconciled outcome: **CLEAR / CANDIDATE** for TASK-255 r4,
`BOUNDED_POST_WRITE_PR_HEAD_LAG_RECONCILIATION_V1`. Real ChatGPT Work commit-update
wake remains a separate post-publication conformance gate.
