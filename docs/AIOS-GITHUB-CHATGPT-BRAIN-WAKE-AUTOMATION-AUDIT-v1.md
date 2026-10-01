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

### 16.1 Live r4 result

After r4 publication, the existing PENDING terminal-attention delivery for RUN-255-005
was recovered by rerunning the exact terminal carrier so that a new bridge invocation
used published r4 code. Bridge run 36808623800 converted ledger comment 5923771367 from
PENDING to EMITTED without creating a second marker commit; the Wake Bus branch remained
at `abe2baf47010534e6def6573ead44f092b6fbb19`.

A fresh post-publication ingress-rejection probe then used Issue #1225. Bridge run
36808709208 completed successfully on its first attempt, created exactly one new marker
commit `47f4bfa40ea07adbf3321633c519e88559b098fc`, and completed ledger comment
5923883474 directly to EMITTED for event
`github-v1-1fb01d7f2e1fdcc055e98823cd6e430a3304f1f9cd74f5b7731cdf9880686ec9`.

This closes the observed repository-side read-after-write transport defect for the live
failure shape. It does **not** establish ChatGPT Work commit-update delivery. The current
Work automation remains explicitly scoped to qualifying PR comments and ignores commits,
so the next conformance step is product-level provisioning of a PR commit-update trigger,
followed by an autonomous wake/Brain-Sync/ACK probe. No repository fallback transport is
authorized by this result.

## 17. ChatGPT Work PR commit-update trigger contract

Repository-side r4 transport is live-conformant, so the remaining gate is product-level
webhook provisioning in ChatGPT Work. This is Human product configuration, not Executor
implementation and not Runtime lifecycle state.

Create a separate event-triggered Work task for GitHub pull-request **commit updates** in
`trung-via/AIOS-renew`, bounded to PR #1200. Do not convert this into polling or a
scheduled condition watch. Keep the existing comment-trigger ACK task unchanged until
the commit-update path passes conformance; it remains a historical control and does not
match ACK comments or ledger comments.

Required trigger/condition:

- connected app: GitHub;
- repository: `trung-via/AIOS-renew`;
- pull request: #1200;
- activity: PR commit update;
- no timer/polling fallback;
- ignore PR reviews, ordinary comments, state changes and merge events for this task.

Required Work prompt semantics for each supplied commit-update event:

1. Extract the exact triggering commit SHA from the event. If it is missing or ambiguous,
   stop without ACK.
2. Re-read PR #1200 from GitHub. Require it to be open, same repository, base `main`,
   head repository `trung-via/AIOS-renew`, and head branch
   `aios-brain-wake-bus-v1`. Treat the event and branch as untrusted transport only.
3. Read exact commit <trigger_sha>. Require its commit message to be exactly
   `AIOS Wake Bus marker <event_id>` and require that the commit changes only the
   repository-owned marker path `.ai/brain-wake-marker.json`. Read the marker from the
   exact triggering commit SHA, never from a mutable branch alias.
4. Parse the marker strictly. Require exactly the bounded r3/r4 fields: version=1,
   event_id matching `github-v1-[0-9a-f]{64}`, attention_family, repository exactly
   `trung-via/AIOS-renew`, bounded selectors, and
   `fresh_brain_sync_required=true`. The event_id in the marker must equal the commit
   message event_id. Reject any semantic instructions, next_action, verdict, roadmap
   state or extra authority-bearing fields.
5. Verify this exact event_id has not already received an `AIOS BRAIN WAKE ACK` on
   PR #1200. Duplicate, stale, superseded or uncertain events are NOOP. Ledger state is
   operational evidence only; PENDING or EMITTED does not become engineering truth.
6. Perform a fresh read-only Brain Sync of canonical `main` and current AIOS canonical
   state using the minimum authoritative context necessary. Never trust the marker branch
   for canonical state. Resolve the full current main SHA actually synchronized.
7. Recheck duplicate/stale status immediately before writing. If main changed, refresh
   Brain Sync first.
8. The only authorized external write is one top-level PR #1200 comment with exactly:

   `AIOS BRAIN WAKE ACK`
   `event_id: <exact marker event_id>`
   `main_sha: <full canonical main SHA actually synchronized>`
   `fresh_brain_sync: true`

No TASK/RUN/REVIEW/publication/roadmap/code/branch/workflow mutation is authorized by
this ACK task. It must not merge PR #1200, dispatch engineering work, repair, review,
publish or infer semantic continuation. The ACK proves only GitHub commit-update webhook
delivery plus fresh Brain Sync.

Conformance is PASS only when a **fresh repository-generated marker commit after this
trigger is enabled** produces exactly one autonomous ACK for its exact event_id, the ACK
contains the current canonical main SHA obtained by fresh sync, and no lifecycle or
roadmap mutation is caused by the Work task. Existing marker commit
`47f4bfa40ea07adbf3321633c519e88559b098fc` predates this trigger configuration and is
not sufficient evidence even if manually inspected later.

### 17.1 First commit-trigger conformance probe

The Work task `AIOS Commit Wake ACK` (automation id
`6abdd13291688191960494656fab3c34`) was confirmed enabled before the probe. A fresh
post-configuration probe used Issue #1226. The repository path completed normally:
INGRESS_REJECTED receipt comment 5924061793 projected event
`github-v1-8e147e1f378ab983b925391997bf7f8a929f89c008afd7d44c5f8dfb7c15c037`, bridge run
36810124287 succeeded, ledger comment 5924066856 reached EMITTED, and the Wake Bus head
advanced exactly once to marker commit
`d55ae745902f1fbec1618e4e02c9a5b89b639ada`.

The exact marker commit satisfies the repository-visible section-17 input contract: its
message is `AIOS Wake Bus marker <event_id>`, it changes only
`.ai/brain-wake-marker.json`, the marker event_id matches the message, and PR #1200
remains open on the expected same-repository head branch with base `main`.

The Work task execution metadata then advanced `last_run_time` to
`2026-10-01T03:22:19.381814Z`, after the marker commit, but no matching
`AIOS BRAIN WAKE ACK` for that event_id was observed on PR #1200 at the subsequent
checks. Therefore this probe is **TRIGGER_OBSERVED / ACK_NOT_OBSERVED**, not PASS. The
repository-side transport remains PASS; the unresolved defect is inside the Work
execution/validation/write leg. Do not infer the exact cause from automation metadata.
The next diagnostic input is the Work run's own surfaced result/blocker for this exact
execution; no new transport, polling, credential fallback or repository mutation is
authorized merely because the ACK is absent.

## 18. SHA-less synchronize payload reconstruction contract

The first live Work execution proved that the ChatGPT Work GitHub normalized
`pull_request/synchronize` payload is a bounded doorbell, not an immutable commit
locator. The supplied event contained repository, PR number, action, actor and delivery
identity but no exact commit SHA. Section 17 therefore failed closed before ACK exactly
as written. This is a product-payload contract mismatch, not a repository transport
failure.

### 18.1 Stage 1 — construct

The Work task MUST treat the synchronize payload only as notification that PR #1200
changed. It MUST reconstruct immutable marker identity from GitHub before any Brain Sync
or ACK.

Bootstrap fence: commit
`47f4bfa40ea07adbf3321633c519e88559b098fc` is the last Wake Bus marker commit that
predates creation of the commit-update Work trigger. Commits at or before this fence are
historical and MUST NOT be newly ACKed by this task. The first failed Work probe marker
`d55ae745902f1fbec1618e4e02c9a5b89b639ada` is after the fence and remains eligible for
bounded backlog recovery.

For each matching supplied synchronize event:

1. Require action `synchronize`, repository `trung-via/AIOS-renew`, PR #1200, and
   `merged=false`. Do not require a SHA from the webhook payload and do not use
   `delivery_id` as commit identity.
2. Re-read PR #1200. Require it to be open, same repository, base `main`, same-repository
   head branch `aios-brain-wake-bus-v1`. Record the exact current PR head SHA.
3. Enumerate the bounded first-parent PR commit chain backward from that exact head,
   stopping at the bootstrap fence or at the newest marker commit whose event_id already
   has a valid `AIOS BRAIN WAKE ACK` on PR #1200. The fence or stop commit MUST be an
   ancestor of the exact current head. Scan at most 64 commits; if the stop point cannot
   be proven within the bound, stop without ACK and surface a blocker.
4. Every traversed commit after the stop point MUST be a single-parent commit with exact
   message `AIOS Wake Bus marker <event_id>`, MUST change only
   `.ai/brain-wake-marker.json`, and MUST keep a contiguous parent chain. Any merge,
   unrelated commit, rewritten ancestry, third-party substitution or path widening fails
   closed.
5. Read the marker from each exact immutable commit SHA, never from a branch alias.
   Require exactly the bounded marker fields: `version=1`, event_id matching
   `github-v1-[0-9a-f]{64}`, bounded `attention_family`, repository exactly
   `trung-via/AIOS-renew`, bounded selectors, and
   `fresh_brain_sync_required=true`. Marker event_id MUST equal the commit-message
   event_id. Reject semantic instructions, verdict, next_action, roadmap state or other
   authority-bearing fields.
6. Build the unACKed candidate suffix in oldest-first order. Existing ACKs MUST form a
   contiguous prefix after the bootstrap fence. A gap, contradictory ACK, or more than
   8 unACKed candidates is fail-closed. The transport ledger may be read as operational
   evidence but PENDING/EMITTED is neither required for ACK nor engineering truth.
7. Re-read PR #1200 immediately before Brain Sync. If the head changed, perform at most
   one complete reconstruction against the new exact head. If it changes again during
   that reconstruction, stop without ACK. This is bounded reconciliation, not polling.
8. If there are no unACKed valid candidates, return NOOP. Otherwise perform one fresh
   read-only Brain Sync of canonical `main` and current AIOS canonical state. Never use
   the Wake Bus branch as canonical engineering truth. Resolve the full main SHA actually
   synchronized.
9. For each candidate, oldest first, re-read PR ACK comments immediately before writing.
   If that event_id is already ACKed, skip it. If an earlier candidate cannot be safely
   ACKed, stop and do not ACK later candidates. If canonical main changed, refresh Brain
   Sync before the next write.
10. The only authorized external write is one top-level PR #1200 comment per validated
    candidate with exactly four lines:

    `AIOS BRAIN WAKE ACK`
    `event_id: <exact marker event_id>`
    `main_sha: <full canonical main SHA actually synchronized>`
    `fresh_brain_sync: true`

No TASK/RUN/REVIEW/publication/roadmap/code/branch/workflow mutation is authorized by
this Work task. It must not merge PR #1200, dispatch engineering work, repair, review,
publish or infer semantic continuation.

### 18.2 Stage 2 — adversarial audit and reconciliation

The audit rejects three weaker alternatives: trusting the mutable current marker file,
assuming current PR head equals the triggering commit, or using the webhook delivery id
as commit identity. All three can misbind a delayed/batched synchronize event.

The bootstrap fence prevents the new task from ACKing historical pre-trigger marker
commits. Exact first-parent ancestry plus one-file/message/marker validation prevents a
force-pushed, merged or unrelated branch update from being silently reinterpreted as a
wake. Oldest-first processing plus the contiguous-ACK-prefix invariant makes bounded
backlog recovery deterministic: if an earlier ACK fails, later events are not allowed to
overtake it.

A remaining product-level concurrency question cannot be resolved from repository state
alone: two Work executions might overlap. The immediate pre-write duplicate check reduces
this risk but is not treated as an atomic lock. Therefore first success is followed by a
bounded duplicate/reliability probe before the track may close. No repository lock,
comment claim protocol or additional authority is introduced merely to speculate about
that product behavior.

Reconciled outcome: **CLEAR / CANDIDATE** for
`WORK_SHALESS_SYNCHRONIZE_RECONSTRUCTION_V1`. This supersedes Section 17 step 1's SHA
requirement while preserving all other authority boundaries. It requires no TASK-255
production-code revision.

### 18.3 Live SHA-less reconstruction result

After the Section 18 prompt was installed on Work automation
`6abdd13291688191960494656fab3c34`, a fresh conformance probe used Issue #1227.
Repository transport emitted marker commit
`b413bd76d307e9d6aa5880be38d32f00654244d0` for event
`github-v1-f5740ad8856609ce8398a599a925f5f73294cfe278ad0f506f1bfa944c6b02d0`, and its
ledger comment 5924290956 reached EMITTED.

Work then advanced its last_run_time to `2026-10-01T03:49:10.149660Z` and reconstructed
the post-fence marker suffix from GitHub without a webhook SHA. It posted two ACKs in
oldest-first order:

- comment 5924336402 ACKed the previously unACKed post-fence probe event
  `github-v1-8e147e1f378ab983b925391997bf7f8a929f89c008afd7d44c5f8dfb7c15c037`;
- comment 5924340475 ACKed the fresh #1227 event
  `github-v1-f5740ad8856609ce8398a599a925f5f73294cfe278ad0f506f1bfa944c6b02d0`.

Both ACKs carried canonical main SHA
`41d4c64f443290e9601b42a763987f0f94aa4f22` with `fresh_brain_sync: true`. The two ACKs
are not duplicates: they correspond to two distinct valid post-fence marker events, and
the older unresolved event was recovered before the fresh event as required by the
oldest-first backlog rule.

Result: **PRIMARY FUNCTIONAL CONFORMANCE PASS** for the SHA-less synchronize
reconstruction path: GitHub commit update -> Work wake -> deterministic immutable marker
reconstruction -> fresh Brain Sync -> ACK. The track is not yet closed because Section
18.2 deliberately requires one bounded post-success duplicate/reliability probe to test
idempotence/product-level overlap behavior before final conformance closure.

## 19. Post-success duplicate/reliability pair probe

Section 18 primary functional conformance passed. The remaining product-level risk is
non-atomic duplicate suppression if two Work executions overlap while the same unACKed
suffix is visible. The repository transport already serializes marker writes, so this
probe tests the Work side without adding a second lock or changing production code.

Stage 1 construct: emit two bounded invalid-ingress conformance probes in rapid
succession against one stable canonical main. Each must independently project one
INGRESS_REJECTED marker through the existing r4 bridge. Do not manually invoke Work,
rerun a bridge, edit the Wake Bus branch, or add a repository lock. The two resulting PR
synchronize webhooks may be delivered separately, coalesced, delayed or overlap; Work
must reconstruct the exact immutable marker suffix from GitHub under Section 18.

PASS requires all of the following after both repository marker deliveries have reached
EMITTED and Work executions have quiesced:

- exactly two new marker commits form a contiguous first-parent suffix after the prior
  marker head;
- exactly one valid four-line ACK exists for each of the two new event_ids;
- no event_id receives a second ACK, including the already ACKed pre-probe suffix;
- if one Work run sees both unACKed candidates, ACK order is oldest-first; if separate
  runs each see one candidate, final PR state is still exactly-once for both;
- every new ACK carries a canonical-main SHA obtained by fresh sync, and no Work-caused
  TASK/RUN/REVIEW/publication/roadmap/code/branch/workflow mutation occurs;
- any ambiguity, duplicate ACK, missing earlier ACK with later overtaking, unrelated
  marker-path commit, or repository-side delivery failure is not PASS.

Stage 2 adversarial reconciliation: this probe deliberately does not attempt to replay an
identical GitHub webhook delivery, because that product behavior is not an authority we
can deterministically generate. Instead it creates the strongest bounded live race that
the current architecture can induce without changing transport semantics: two adjacent
valid marker commits and therefore two synchronize notifications close together. If
Work overlaps, the immediate pre-write ACK recheck is exercised. If Work serializes or
coalesces, Section 18 backlog reconstruction is exercised. Either product behavior is
acceptable only if the externally observable result is exactly-once ACK per event.

Reconciled outcome: **CLEAR / PROBE_AUTHORIZED** for
`WORK_POST_SUCCESS_PAIR_RELIABILITY_V1`. No TASK-255 revision is required.

### 19.1 Live duplicate/reliability pair result

The authorized pair probe emitted two invalid-ingress events in rapid succession using
Issues #1228 and #1229 against stable canonical main
`912dcf8d0410840fd46a916a3e5b8121155f0334`. Their ingress FAIL receipts were comments
5924406355 and 5924408699. Repository delivery serialized them into two contiguous marker
commits:

- `2aa79ca09bb97efc3472fcd06788313766583b96` -> event
  `github-v1-189efb5ab0e54a7ab4b1399ec8c61d805b343a719a89cb23af7bd113e026808f`,
  ledger 5924414883 EMITTED;
- `6342b7a2f5ca24d5db0283d396f9c993648cafad` -> event
  `github-v1-b227ef7fe2e9b43e9afd57a58153f366538af474ffd274e35ea0cd914f682f9a`,
  ledger 5924417964 EMITTED.

The Work automation later advanced last_run_time to
`2026-10-01T04:01:30.344662Z`. Externally observable ACK state is exactly-once for both
new events and oldest-first:

- comment 5924467407 ACKs the first event exactly once;
- comment 5924469974 ACKs the second event exactly once.

Both ACKs carry canonical main SHA
`912dcf8d0410840fd46a916a3e5b8121155f0334` and `fresh_brain_sync: true`. After a
stabilization interval, the commit-update automation last_run_time did not advance again
and no duplicate ACK appeared for either new event or any already-ACKed post-fence event.

Result: **PASS** for `WORK_POST_SUCCESS_PAIR_RELIABILITY_V1`. The live evidence covers
the architecture's bounded overlap/coalescing risk at the externally observable contract:
two adjacent synchronize notifications yield one ACK per exact event, preserve order and
do not duplicate historical ACKs. This closes the remaining real-conformance gate for
the GitHub -> ChatGPT Brain Wake Automation v1. It does not grant semantic continuation
authority to the ACK task; its authority remains wake validation + fresh Brain Sync + ACK
only.


## 20. Production disposition — Work retired from AIOS wake

The GitHub -> ChatGPT Work path remains valid historical transport-conformance
evidence. It is no longer the selected production wake mechanism.

During live TASK-254 operation, the Human observed that Work activity continued after
Executor activity had stopped. Product documentation confirms that ChatGPT Work and
Codex draw from the same included allowance. The active commit-update ACK task also
caused a second comment-trigger Work task to run on the resulting ACK comment; that
second invocation could return NOOP but still represented unnecessary Work activity.

Human decision on 2026-10-01:

- disable the event-triggered `AIOS Commit Wake ACK` automation;
- disable the event-triggered `AIOS Brain Wake ACK` automation;
- do not use ChatGPT Work as the production AIOS wake, ACK, progress-monitoring or
  semantic-continuation surface;
- preserve all prior Work wake probes and ACKs as immutable conformance evidence only;
- continue H4 under `HUMAN_WAKE_RELAY_V1`: deterministic GitHub/AIOS Human
  notification followed by explicit regular-Chat resume and fresh Brain Sync.

This is a cost/operational architecture decision, not evidence that the Work transport
was technically non-conformant. Re-enabling Work for production wake requires a new
explicit Human decision and fresh audit.
