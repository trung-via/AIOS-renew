# AIOS Self-Host End-to-End Flow v1

Status: CURRENT NORMATIVE OPERATIONAL NAVIGATION — `SELF_HOST_END_TO_END_FLOW_V1`  
Scope: ordinary Brain navigation of `trung-via/AIOS-renew` GitHub/self-host operation.  
Activation unit: TASK-318's entrypoint and scoped redirects/retirements together.

This is the **single current Brain-facing normative operational-navigation
entrypoint**. Its candidate becomes production navigation only through eligible
Runtime verification, Reviewer PASS and canonical Publisher publication of the
same complete candidate. Its status is not an assertion that those events occurred.
The [approved architecture](AIOS-SELF-HOST-END-TO-END-FLOW-ARCHITECTURE-v1.md)
remains Human/Brain planning authority; navigation supplies no engineering-state truth.

## 1. Authority and minimum fresh context

Fresh canonical Git/lineage outranks navigation. Use the published
[Project Contract](CHATGPT_PROJECT_CONTRACT.md) and frozen Kernel for their own
governance/protocol obligations. Human owns intent, priority, risk and delegation;
Brain owns WHAT/WHY, scope and planning; the admitted Executor owns HOW; Runtime
owns RUN admission, deterministic lifecycle state, verification and EVIDENCE;
Reviewer owns semantic verdict; Publisher owns exact eligible publication.
H4 planning/conformance and immutable historical observations retain their distinct
authority. Neither this document nor transport can advance a roadmap or choose work.

For ordinary continuation, establish fresh canonical repository/main identity,
the exact selected TASK/revision and relevant immutable lineage, unresolved status,
existing Unified State `next_action`/authority, and the existing Flow Card result.
Use the existing `brain_sync.observe_brain_sync` / `unified_state.observe_unified_state`
observation, then `brain_context.compose_brain_work_context` and
`brain_context.resolve_flow` with the observed repository's existing card registry.
Read this entrypoint for operational navigation, then only the material required by
that selected semantic flow. Reuse a bounded governance/specification projection
only after freshly proving its canonical binding/digest unchanged. Relevant main,
subject, request, governance or card movement invalidates stale context/proposals.
Ambiguity or conflict expands bounded context to directly relevant canonical sources
or **fails closed**; never guess continuation from chat, memory, browser or receipts.
No repository-wide rediscovery is needed to reconstruct the ordinary path.

The seven-card registry remains closed: `ARCHITECTURE`, `TASK_AUTHORING`,
`SEMANTIC_REVIEW`, `REMEDIATION_AUTHORING`, `REPAIR_AUTHORING`, `DIAGNOSTIC`, `RESEARCH`.
Keep [.ai/flow-cards.yaml](../.ai/flow-cards.yaml) and
[brain_context.py](../src/aios_renew/brain_context.py) unchanged. This document is
not a `SELF_HOST` card, adds no `operational_flow_ref` or other Flow Card field,
and requires no second semantic flow resolver, dependency engine or lifecycle router.
Use the existing read-only composition/resolution; operational execution, publication
and wait remain existing canonical actions rather than new reasoning flows.

## 2. AUTHOR and INGRESS

Human/Brain authors the final semantic contract under the
[TASK authoring contract](AIOS-RENEW-BRAIN-TASK-AUTHORING-CONTRACT.md).
Resolve intent, scope, acceptance and current Executor delegation from their own
authorities. Before closing scope for a self-host boundary change, consult the
bounded impact map in section 9. Do not select new semantic work from that map.

The Issue carrier recognizes exact syntactic families and delivers to hosted Brain
ingress; it does not select lifecycle work. New origin-affine authoring must satisfy
the currently published exact-origin proof/admission contract. Route/generation,
carrier attempt, TASK identity, expected main and envelope bindings cannot be
self-certified by Brain or hosted transport. Ordinary revisions preserve canonical
base affinity; explicit legacy routing remains separate. Planning a replacement
issuer does not activate it. Deployment-owned secrets/registry remain outside Git.

AUTHOR_TASK admission validates and canonicalizes the exact final TASK under current
ingress policy and expected-main/CAS obligations. Confirm that artifact is on
canonical main before PRIMARY. Addressed admission failure is operational:
`run_created=false`; it creates no canonical RUN, FAILURE or REPAIR lineage.
Non-target carrier rejections are fan-out noise, not the addressed TASK outcome.

## 3. PRIMARY and admitted RUN / Executor

PRIMARY delivery binds exact canonical TASK id, revision, artifact/blob identity,
authoring provenance and the authorized Executor/profile. The Brain wakeup carrier
hands those selectors to the self-host primary workflow and bounded control entry.
After synchronization, Runtime must independently admit the exact current semantic
TASK from the then-current canonical main. Unrelated main movement cannot silently
substitute a changed TASK. A carrier receipt, workflow dispatch or `RUNNER_STARTED`
does not prove RUN creation or Executor invocation; inspect canonical RUN separately.

Runtime's existing H1R reconciliation runs only under the PRIMARY pre-RUN repository
mutation lock. Equal main and clean stale-behind fast-forward retain their existing
behavior. A clean diverged local main can synchronize automatically only when one
unique exact canonical candidate preserves its complete local-only ancestry and
the current canonical main is independently reproved. Existing canonical FAILURE
and reviewed-PASS preservation remain eligible. A reviewed CHANGES_REQUIRED or
BLOCKED RESULT is also eligible **solely as history-preservation proof**: Runtime
must independently validate the exact TASK/revision, RUN/base/candidate, complete
RESULT/EVIDENCE, byte-exact canonical REVIEW metadata and applicable correction
predecessor lineage. CHANGES_REQUIRED requires acceptance FAIL and precise valid
findings. BLOCKED alone supplies no proof; a deficient or unreviewed candidate
remains ineligible. The original verdict, findings and remote refs remain unchanged.

At the mutation edge Runtime rechecks immutable proof refs, current remote main,
full ancestry, local HEAD, attached main/upstream binding and worktree safety.
Tracked/untracked edits, hidden assume-unchanged or sparse content, ignored-path
collisions, submodules, ambiguous upstream, remote unavailability, ahead-only
history and any unpreserved extra commit retain rejection or their existing safe
behavior. Only the existing single reset to the exactly reproved main is permitted,
followed by clean attached-main postconditions. TASK/source changes require the
existing safe generation restart with the same TASK/blob, Human-selected
Executor/model/effort and durable dispatch identity. Workflow transport and its
transient control source acquire no Git recovery or RUN-admission authority.

| PRIMARY observation | Operational handling |
| --- | --- |
| Exact clean divergence with complete immutable preservation proof | Runtime may reconcile inside H1R, then independently admit the pending exact TASK through normal PRIMARY. Preservation grants no Reviewer PASS, correction, Publisher eligibility or roadmap progression. |
| Missing, malformed, stale, conflicting or moved proof; unsafe local state; lock or generation conflict | Fail closed before RUN/Executor. Inspect the existing admission boundary and canonical source facts; never infer repair authority or perform a transport-side retry/reset. |
| Pre-RUN synchronization refusal | Existing admission failure uses `phase=PRIMARY_SYNCHRONIZATION`, `reason_code=PRIMARY_SYNCHRONIZATION_REJECTED`; the bounded public operational receipt reports `run_created=false`, `executor_invoked=false`. Raw/private Git or host diagnostics stay local. |
| Terminal dispatch or absent local continuity | Observe the original configured repository's durable dispatch record. A clean independent checkout cannot establish its terminal status or substitute its journal. |

Dispatch idempotency is local to the original `.git/aios` journal. Existing terminal
FAILED-without-RUN and SUCCEEDED-with-RUN deliveries are non-executing replays on
that journal, even if preservation proof later becomes valid. A fresh clone's
missing journal is unknown continuity, not authorization to rearm the old delivery.
H1R does not select another checkout, copy/migrate a journal or update
`AIOS_REPO_ROOT`; there is no cross-checkout deduplication guarantee. Preserve the
configured checkout and terminal record when diagnosing delivery. Any later
execution requires its own existing Human authorization and delivery boundary.

For the RUN-359-002 / TASK-360 shape, CHANGES_REQUIRED preservation does not correct
the AC4 finding. The historical workflow refusal before RUN supplies no FAILURE
for REPAIR and no REMEDIATION authorization. Independent Reviewer, explicitly
authorized REMEDIATION/REPAIR and Publisher boundaries in sections 5–7 still apply.
Synthetic real-Git/native-I/O simulations exercise this route; they do not establish
live self-host wakeup, Runtime verification or semantic success.

Runtime validates the Human delegation/profile binding, admits the bound RUN, and
admits and invokes exactly one Human-delegated native Executor/profile. Runtime
does not select or override Executor delegation; delegation and overrides remain
Human-only. That Executor implements only the authorized modification scope and
commits the final candidate.
It returns concrete implementation claims bound to that committed HEAD. It does not
re-admit/dispatch execution, change planning, select correction, issue review verdicts,
publish, or alter current execution-profile delegation. No Executor push is authorized.

## 4. Runtime verification and RESULT / FAILURE

Runtime owns the authored canonical verification envelope, exact subject/base
bindings, minimum-sufficient evidence derivation/reuse and canonical EVIDENCE.
Executor claims, a clean commit or a successful workflow are not verification proof.
Do not duplicate baseline/full/canonical verification before implementation or rerun
unchanged proof for ceremony. Runtime alone constructs the canonical terminal outcome.

Read exact RESULT/EVIDENCE for a successfully completed admitted RUN, or exact
FAILURE/failed RUN/candidate for a failed admitted RUN. Completion or verification
failure after admission is distinct from failure before admission. RESULT is not
semantic Reviewer PASS, publication, H4 live proof or roadmap completion.

## 5. REVIEW

Reviewer reconstructs exact TASK, RESULT, typed EVIDENCE, candidate delta and applicable
PRIMARY/DELTA predecessor lineage. Submit the verdict only through canonical review
ingress/currentness validation. A proposal, wake ACK or transport acceptance is not
a canonical REVIEW. PASS authorizes only the exact eligible reviewed source candidate;
CHANGES_REQUIRED carries explicit findings; BLOCKED preserves the unresolved boundary.
New Human intent is not a FIX. Correction and later review retain their existing owners.

## 6. REPAIR and REMEDIATION

REPAIR applies only to an admitted failed RUN with immutable FAILURE lineage.
Human/Brain selects and authors the exact authorized strategy; action-neutral
correction preflight and current H2 strategy facts supply facts/eligibility, not strategy. Bind failed RUN,
failed head and current authorization SHA. An unexecuted obsolete authorization may
be superseded only under the existing immutable contiguous authorization contract;
never fabricate FAILURE, rewrite history or replace an admitted RUN's authorization.

Durable REPAIR delivery must consume canonical `executor_required`, including the
TASK-314/TASK-315 chain in section 9. An action-name guess cannot determine Executor
need for `FINALIZE_CANDIDATE`. Preflight rejection before a continuation RUN remains
operational even when a predecessor RUN has already failed; preserve that original
FAILURE without inventing a new continuation FAILURE. Durable dispatch replay
reconciles its exact binding/RUN; it is not permission for blind retry or failover.

REMEDIATION follows exact CHANGES_REQUIRED REVIEW/finding and an explicitly authorized
correction. Approved intent/admission, approved wakeup delivery and Runtime continuation
remain separate boundaries. Preserve source RUN/finding, approval, correction identity,
scope, profile and currentness. Neither approval delivery nor dispatch creates a RUN.
DELTA review checks the authorized finding/repair and material defects introduced by
that correction; it does not restart the original TASK or absorb new Human intent.

### Exact-subject Human AUTO delivery (TASK-365)

The Issue carrier accepts an explicit per-request AUTO opt-in using the existing
eight selectors, with `version: 3`, `executor: AUTO`, and null `model` and
`reasoning_effort`. For example, the following is a request shape only; replace
the RUN, authorization SHA and immutable delivery id with the exact approved subject:

```yaml
format: AIOS_REPAIR_WAKEUP_REQUEST
version: 3
repair_dispatch_id: repair-human-exact-subject
failed_run_id: RUN-365-001
repair_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
executor: AUTO
model: null
reasoning_effort: null
```

AUTO delegates deterministic reuse of the exact protected failed RUN's coding
profile. It does not delegate provider/model selection. Runtime first requires
READY canonical preflight and matching Unified State for the current REPAIR SHA.
When `executor_required=true`, it reads exactly one source RUN/FAILURE/profile
through canonical remote recovery, checks TASK/RUN/executor identity, source
candidate policy and current capability compatibility, and preserves the source's
exact model, effort and original source attributions. Missing, ambiguous, malformed
or incompatible source state rejects before journal admission/RUN; the actionable
alternative is an explicit Human coding selection. No local RUN label, repository
default, prior preference or free-form provenance string supplies an AUTO profile.

Explicit `codex`/`antigravity` and supported model/effort continue through the manual
route without consulting AUTO. Version 2 manual/executorless Issues remain valid.
Mixed AUTO/model/effort inputs are rejected. Both source fields of a pre-resolved
manual profile must be supplied together with its exact values. An absent optional
workflow value is omitted from the command, preserving existing default resolution.

The self-host workflow verifies repository, canonical main ref/current SHA, bounded
selectors and original GitHub event proof. An allowlisted Human `workflow_dispatch`
may request AUTO for its exact input subject. A reusable opened Issue may be delivered
by `github-actions[bot]` only when re-admission of the GitHub-owned event proves its
allowlisted Human sender/author and exactly matches all transported selectors and
profile values. A copied Human name or Bot-authored dispatch cannot establish that
proof. AUTO rechecks it at the Runtime outer boundary as well. The existing bare
Bot `workflow_dispatch` route stays executorless and requires its deterministic
`repair-<failed_run_id>-<repair_sha>` identity, with no model/effort/source inputs.

| Delivery / canonical requirement | Boundary and result |
| --- | --- |
| Human manual REPAIR, coding required, valid exact profile | Existing manual profile reaches Runtime unchanged; canonical admission still owns source/currentness/scope/checkout/ancestry checks. |
| Human version-3 AUTO, coding required, unique protected compatible source profile | Resolve once, bind concrete executor/profile and Human/source provenance in the same dispatch journal, then enter existing REPAIR admission. |
| Human AUTO without valid Human proof/current authorization or with absent/ambiguous/invalid source profile | No new RUN or coding invocation; bounded authorization/source-profile reason identifies the required correction. |
| Bot reusable Issue transport with proven original Human AUTO/manual request | Re-admit the original exact event before AIOS; Bot does not choose or broaden authority. |
| Bare Bot coding/AUTO `workflow_dispatch`, wrong actor/repository, stale main, malformed or swapped selectors | Reject before RUN; these are intended safety gates. |
| `CODE_FIX`, `CONTINUE_IMPLEMENTATION`, nonreusable `FINALIZE_CANDIDATE` | `executor_required=true`; require manual or authenticated AUTO coding profile. Empty selection rejects. |
| Eligible reusable `FINALIZE_CANDIDATE`, `NO_CHANGE` | `executor_required=false`; empty selection or authenticated AUTO remains executorless/profileless. No source-profile lookup or coding invocation; explicit coding selection rejects. |
| Same id and identical bound AUTO/manual identity | Replay the existing journal result/RUN without resolving defaults/lineage again or invoking another Executor. |
| Same id with changed RUN/SHA/profile/actor or AUTO versus manual | Immutable collision rejection; a source-profile digest binds exact failed-RUN identity. Existing per-source locked admission prevents a second accepted repair. |
| Human A3 REMEDIATION with its exact source/finding and profile | Existing approved-intent route independently binds the explicit Human profile; no AUTO change or synthetic A3. |
| Carrier admitted but runner/control setup or downstream workflow failed | Operational failure only. Inspect the exact self-host step/receipt; carrier admission is not a RUN. |

AUTO journal version 4 adds the authenticated Human actor, exact source RUN and
canonical source-profile digest to the existing immutable dispatch identity.
Historical journal versions 1–3 retain their existing replay semantics and cannot
be reinterpreted as AUTO. Repaired candidate ancestry and source-level CAS remain
owned by existing REPAIR admission; the delivery layer adds no retry or terminal.

The bounded self-host receipt distinguishes `AUTO_AUTHORIZATION` /
`EXACT_HUMAN_REPAIR_PROOF_REQUIRED` from `AUTO_SOURCE_PROFILE` /
`CANONICAL_SOURCE_PROFILE_REQUIRED`, with no private payload or trace. The Issue
receipt distinguishes admission rejection, `OPERATIONAL_FAILED` downstream delivery,
and `SELF_HOST_COMPLETED`. Requested AUTO is not reported as a resolved coding
executor. Only separately observed Runtime journal/RUN/terminal pointers establish
those facts; verification, review and publication remain independent.

Reproduce the branches with the focused carrier/provenance, operator and dispatch
tests in `tests/test_github_issue_repair_wakeup.py`,
`tests/test_github_issue_repair_wakeup_workflow.py`, `tests/test_operator.py`,
`tests/test_operator_correction_preflight.py` and `tests/test_repair_dispatch.py`.
Runtime runs the authored focused commands; this matrix does not claim their result
or introduce a full-suite/V2 gate. Reports that Issue #2033 failed downstream and
#2037 reached `SELF_HOST_COMPLETED` with explicit Codex do not establish either
RUN truth or the cause of #2033. A Bot coding safety rejection, an authorization or
profile mismatch, runner/control failure and later Runtime failure are distinct
diagnoses requiring the exact corresponding observation.

### AUTHOR_REPAIR continuation and discoverable workflow outcomes (TASK-370)

TASK-365's published source `9f70b6b1dabad4234794db74c4d7c1f43f9a4a60`,
with exact-source PRIMARY PASS `REVIEW-365-001`, implements the Human manual and
explicit exact-subject AUTO paths above. The omitted consumer was the independent
`aios-brain-ingress.yml` caller. TASK-370's supplied incident anchors are originating
Issue #2075 and GitHub Actions run **39026366429**, attempt **1**, run number **168**,
actor `github-actions[bot]`, event `workflow_dispatch`, control source
`491903e00630abb9e39e91310358fe4464bf487a`, job **117378039859**. For canonical repair
SHA `800e7dabd1e91d07407d9c745201ce322404beb7`, its empty Executor reached
`Deliver one immutable REPAIR intent` and failed with
`coding REPAIR requires an explicit Executor`. The old artifact's `AIOS_INVOKED`,
`run_created=false`, `executor_invoked=false` and Issue's `DISPATCH_ACCEPTED` did not
describe that final operational cause. This was a pre-RUN rejection, with no new
canonical FAILURE or Codex invocation. It does not show that Human AUTO failed.
The active Human-authorized TASK-369 correction is independent; do not replay it.

New AUTHOR_REPAIR delivery consumes read-only Runtime correction preflight and
matching exact Unified State. Only READY, the current failed RUN/authorization SHA,
and both owners' literal `executor_required=false` permit `EXECUTORLESS_READY`.
The conditional FINALIZE case must have verified reusable state; its action name
alone grants nothing. Coding required, disagreement, moved/active correction or
unavailable facts yields one bounded **HUMAN_WAKEUP_REQUIRED** receipt on the
originating Issue, with exact failed RUN, repair SHA and `EXECUTOR_REQUIRED` or
`CURRENTNESS_UNAVAILABLE`. Use the existing authenticated manual v2 or exact Human
AUTO v3 wakeup. Authoring/ingress cannot invent a profile or consume another Human
wakeup. Identical authoring replay emits no additional execution selectors.

Eligible automatic executorless delivery calls the existing reusable self-host
workflow inside the originating GitHub workflow attempt. The self-host preparation
re-authenticates that original opened Human ingress event, forbids all coding/profile
inputs and re-observes current Runtime executorless authority. Runtime still owns
the final admission, journal lock, same-id replay and source-level CAS. Human manual
and AUTO reuse their original event, selection and profile provenance unchanged.

The existing hosted Issue writer creates a pending receipt, then updates that same
comment after self-host delivery. `AIOS_REPAIR_WORKFLOW_V1` selectors bind the Issue
id/body digest, dispatch id, failed RUN, exact repair/control SHA, actual GitHub
workflow run id and attempt. They are operational pointers, not lifecycle truth.
While the parent workflow is running, its status remains running even if the repair
job has finished. Job id, bounded known step status and the attempt URL come from
GitHub; no screenshot or Human-supplied workflow URL is required.

From a known originating Issue, a fresh read-only consumer can use:

```text
python -m aios_renew.brain_attention repair-outcome --issue-number <origin-issue-number>
```

For a direct workflow_dispatch, use `--delivery-id <exact-repair-dispatch-id>`
instead. The fixed target's GitHub run name carries that identity. Discovery reads
at most 100 comments/runs/artifacts/jobs and fails explicitly when the bound is
insufficient. It verifies the allowlisted GitHub producer/main/actor and exact
attempt. Issue mapping additionally requires the existing same-attempt wake-source
artifact's exact Issue/comment selectors or the v2 artifact's GitHub-event origin
binding. A copied URL, untrusted comment, mismatched Issue/run/job, changed body,
different source SHA or different correction cannot establish provenance.

The observation supplies the real `/actions/jobs/<job-id>/logs` selector.
`RepairWorkflowSources.job_logs` rechecks the attributable job before reading its
real GitHub logs with the reader's existing permissions. Logs are private read
results; they are never uploaded or copied into the public receipt. Missing or
restricted diagnostics stay explicitly `UNAVAILABLE` / `DIAGNOSTIC_UNAVAILABLE`.
No credential expansion is required. The self-host job remains contents-read-only;
the hosted writer retains its existing Issue permissions.

Workflow sealing attaches its GitHub-owned attempt to Operational Receipt v2.
The exact missing-Executor Runtime branch emits the operational cause
`CORRECTION_PREFLIGHT / EXECUTOR_SELECTION / EXECUTOR_REQUIRED`; the final CLI
projection preserves that stronger cause rather than overwriting it with
`AIOS_INVOKED`. A failed command with only a transient marker becomes operational
failure with unavailable diagnostics. Parent/carrier success cannot conceal a
failed repair job. Canonical RUN attribution or terminal pointers remain separate
and require their canonical Runtime artifacts; workflow success never asserts
verification, REVIEW, publication or TASK completion. Existing Brain Attention
projects these bounded pre-RUN causes without a new family, monitor or state store.

The focused workflow integration fixtures exercise real AUTHOR_REPAIR admission,
Runtime preflight/Unified State, durable repair delivery, Runtime completion and
the origin-Issue reader across coding-required, executorless and Human manual/AUTO
branches. GitHub HTTP/native process boundaries are fixtures; no live correction
is replayed. Runtime owns running the TASK's authored focused verification on the
exact candidate and constructing EVIDENCE. This navigation asserts no test result.

## 7. PUBLISH

Use [PUBLICATION_END_TO_END_FLOW_V1](AIOS-PUBLICATION-END-TO-END-FLOW-v1.md)
for PASS ingress, exact Publisher outcome, safe Runtime source recovery, evidence
validity, independent recovered-source review, CAS publication and attention.
This section's competing publication traversal is REPLACE_WITH_POINTER to that
one specialized leaf. This document remains the one generic self-host entrypoint.

TASK-308 remains unpublished/blocked. This cleanup **does not activate TASK-308**
publication-turn semantics. Publication-turn behavior is deferred to the **currently
published Project Contract and canonical lineage**, including any separately
authorized future change. No new general stop/continue rule is inferred here from
TASK-308, H4 proof procedures, publication attention or this navigation status.
Publication does not itself advance planning or establish wake/semantic-resume success.

## 8. ATTENTION / WAKE and RETURN

Terminal attention admits exact RUN/terminal kind/artifact selectors. Generic attention
projects bounded typed source facts under the existing family registry. Operational
source artifacts remain subordinate observations. Progress signals (carrier admission,
dispatch accepted, `RUNNER_STARTED`, Executor/verification in progress and auto-publication
start) do not prove terminal outcomes and remain non-wake progress signals.

The local regular-Chat wake workflow transports one selector-only doorbell to the
exact proven effective destination, preserving canonical base affinity, origin proof,
generation/binding integrity, draft/generation protection, durable dedupe and
proof-only ambiguity/no-blind-resend. Missing/conflicting origin has no silent
repository-default fallback. Legacy affinity stays explicitly separate. Current
Human-approved wake eligibility policies remain in their specialized contracts;
UNKNOWN transport eligibility is never canonical unresolved lifecycle truth.

Wake delivery/submission proves arrival only. Resumed Brain starts with section 1,
cross-checks exact selectors and current unresolved lineage, and occupies only the
selected Brain/Reviewer authority. Transport cannot parse assistant output to select
action, choose Executor/correction, review, verify, publish or advance the roadmap.
H4B's bounded one-semantic-step live proof is a specialized conformance requirement,
not a new publication-turn rule. Current H4 execution gates, H4E live matrix, residuals,
final H4B semantic-resume proof and H5 closure remain Human/Brain planning/conformance
authority; historical ACK/Work observations do not substitute for those proofs.

### Failure and signal boundaries

| Observation | Meaning / required distinction |
| --- | --- |
| Pre-AIOS carrier, dispatch, runner/setup or admission failure | Operational failure; `run_created=false`; no new canonical RUN/FAILURE/REPAIR lineage. Diagnose the exact addressed boundary. |
| CARRIER_ADMITTED / dispatch accepted / RUNNER_STARTED | Transport/operational signals only; not RUN creation, Runtime verification, Reviewer PASS, publication or wake success. |
| Admitted RUN FAILURE | Canonical failed RUN and immutable FAILURE; only this failure family can ground REPAIR. |
| RESULT / Runtime verification | Canonical completion/proof under Runtime; not semantic REVIEW PASS or publication. |
| REVIEW PASS / CHANGES_REQUIRED / BLOCKED | Canonical semantic verdict under Reviewer; not publication or delivery proof. |
| Publication report + canonical main inclusion | Publisher outcome; not wake arrival, semantic resume, planning closure or next-work selection. |
| Wake receipt / exact user-turn proof / ACK | Delivery observation only; not RUN, verification, review, publication or H4B semantic-resume proof. |

Runtime's public FAILURE projection preserves a nullable native exit code and
emits fixed native/verification/completion cause categories. Capture facts state
whether output was available, partial, truncated or withheld; no native text or
host-local raw locator enters a public artifact or provider packet. Incomplete
historical observations retain recorded identities and missing-detail status,
without newly generated fingerprints or PASS comparability.

A dirty or otherwise non-publishable candidate can publish an isolated
FAILURE-artifacts commit and its bound attention selector. This commit contains
only projected terminal material; it does not contain the working tree or create a
candidate ref. A failed projection/identity transport emits a bound
`FAILURE_TRANSPORT_BLOCKER` sidecar with its typed code, leaving original FAILURE
bytes intact. Existing artifact replay preserves earlier lawful representations.

`AttentionAdmission.failure_context` and terminal attention source resolution read
the exact artifact before and after the observation. They distinguish unavailable
source, invalid source and moved source. Unified State includes the safe context
even for non-repairable candidates; Brain Sync and provider work contexts preserve
its RUN, subject and artifact identity. Existing lifecycle authorities continue to
choose any next action. BP-2 failures retain typed operational causes and
`run_created=false`, without creating a terminal lineage.

## 9. Bounded impact map before scope closure

This finite reference exposes known contract/call/transport edges, not an execution
order, automatic dependency graph, scope authorizer or second semantic resolver.
Read only relevant leaves to check identity propagation, currentness, failure taxonomy
and durable outer consumers. Expand a directly affected edge when facts require it;
otherwise fail closed before handing off an incomplete scope. Runtime owns actual
verification; listing a leaf does not authorize modifying it.

| Boundary | Known impact edges / implementation leaves | Scope-closure question |
| --- | --- | --- |
| AUTHORING | origin proof → [Issue carrier](../.github/workflows/aios-issue-carrier.yml) → [Brain ingress](../.github/workflows/aios-brain-ingress.yml) / [github_issue_ingress](../src/aios_renew/github_issue_ingress.py) → canonical TASK → eligibility | Are exact provenance, expected main, revision/affinity and addressed rejection preserved through hosted/self-hosted admission? |
| PRIMARY | [Brain wakeup](../.github/workflows/aios-brain-wakeup.yml) / [github_issue_wakeup](../src/aios_renew/github_issue_wakeup.py) → [self-hosted wakeup](../.github/workflows/aios-self-hosted-wakeup.yml) → bounded control entry / [operator](../src/aios_renew/operator.py) → Runtime RUN admission | Does exact TASK/profile identity survive delivery and synchronization without treating dispatch as RUN? |
| REPAIR | exact FAILURE → AUTHOR_REPAIR ingress → [correction_preflight](../src/aios_renew/correction_preflight.py) → `operator.run_repair_wakeup` → [repair_dispatch](../src/aios_renew/repair_dispatch.py) ↔ [github_issue_repair_wakeup](../src/aios_renew/github_issue_repair_wakeup.py) / [brain-repair-wakeup](../.github/workflows/aios-brain-repair-wakeup.yml) → [self-host-repair-wakeup](../.github/workflows/aios-self-hosted-repair-wakeup.yml) → Runtime continuation | Does canonical `executor_required` reach durable dispatch/replay/profile checks across both Issue wakeup and direct ingress dispatch? |
| REMEDIATION | exact REVIEW/finding → authorized correction → [approved remediation intent](../.github/workflows/aios-approved-remediation-intent.yml) → admission → [approved remediation wakeup](../.github/workflows/aios-approved-remediation-wakeup.yml) → operator / Runtime continuation | Are approval/source/finding/currentness bindings preserved without converting approval or delivery into RUN creation? |
| PUBLICATION | exact semantic PASS → decision ref → [auto-publish](../.github/workflows/aios-auto-publish.yml) / [publication](../src/aios_renew/publication.py) → canonical main → publication attention | Are candidate ancestry/CAS/integration and publication outcome separate from metadata, dispatch and wake? |
| RETURN | attention identity → [terminal attention](../.github/workflows/aios-terminal-attention.yml) / [brain_attention](../src/aios_renew/brain_attention.py) source projection → [local chat wake workflow](../.github/workflows/aios-local-chat-wake.yml) / [local_chat_wake](../src/aios_renew/local_chat_wake.py) → exact chat → fresh Brain Sync | Do exact source, origin/effective destination, dedupe and ambiguity survive without selecting semantic action or fabricating lifecycle truth? |

### Mandatory positive regression: TASK-314 → TASK-315

TASK-314's `FINALIZE_CANDIDATE` same-head structural-package preservation changed
the canonical Executor requirement. A valid reusable package makes
`executor_required=false`; absent reusable state requires the explicitly selected
Executor (`executor_required=true`); malformed present state fails closed rather
than taking the absent-package fallback. TASK-315 covers the durable outer mismatch.
Scope closure for any change to this preflight contract must surface **all** of:
`correction_preflight`, `operator.run_repair_wakeup`, `repair_dispatch`,
`github_issue_repair_wakeup`, `brain-repair-wakeup`, `self-host-repair-wakeup`.

Check canonical preflight/Unified State agreement, explicit Executor/profile presence
or prohibition, exact authorization/failed-RUN binding, durable record version/replay
compatibility, nullable carrier/workflow selectors and final Runtime continuation.
The hosted AUTHOR_REPAIR ingress is also a consumer: its Runtime-backed gate may
call the same reusable self-host workflow only for proved executorless work.
Omitting that caller or the Human Issue route misses an outer transport consumer.
A successful inner preflight alone cannot establish complete impact coverage.
These are navigation review questions and regression scenarios, not changes to REPAIR
strategy, schemas, Runtime behavior or profile delegation.

## 10. Section-scoped consolidation and retained authorities

`SELF_HOST_SINGLE_NORMATIVE_ENTRYPOINT_V1` and
`SELF_HOST_NORMATIVE_CONSOLIDATION_V1` require this entrypoint and the scoped cleanup
in one candidate/activation; an additive document with competing current generic
instructions is insufficient. The following classifications apply to sections/claims,
never to entire mixed-authority files:

| Document / section or claim | Classification and retained purpose |
| --- | --- |
| [Project Contract](CHATGPT_PROJECT_CONTRACT.md) §1 generic lifecycle diagram; §13 generic operational ordering guards | REPLACE_WITH_POINTER here; RETAIN_NORMATIVE governance, failure taxonomy, Brain Sync identity/checkpoint and planning authority in §§2–14. |
| [Publication flow](AIOS-PUBLICATION-END-TO-END-FLOW-v1.md); Project Contract §12 and this §7 | RETAIN_NORMATIVE specialized publication navigation; duplicated publication traversal is REPLACE_WITH_POINTER to that leaf. Independent Publisher/Reviewer/Kernel/VP/H4 authority and historical evidence remain retained. |
| [Brain Portability](AIOS-BRAIN-PORTABILITY.md) §3 target composition, §§4–13 provider/Flow Card/handoff/receipt contracts; §§15–23 planning, conformance and exact migration leaves | RETAIN_NORMATIVE specialized architecture/protocol/planning; generic self-host traversal is REPLACE_WITH_POINTER here. Historical deployment observations remain HISTORICAL_ONLY claims at their original identities. |
| [Origin-Affine Wake](AIOS-H4-ORIGIN-AFFINE-UNATTENDED-WAKE-v1.md) H4C1 implementation contract; §§1–11 architecture, H4C0/C1/D/E requirements, H4B/H5 gates and sequencing | RETAIN_NORMATIVE specialized planning/implementation authority, including current independent requirements; generic traversal is REPLACE_WITH_POINTER here. Named historical fallback/observations retain their claim-scoped historical status. |
| [Local Regular Chat Wake](AIOS-LOCAL-REGULAR-CHAT-WAKE-v1.md) §1 target shape and §5 generic Brain continuation | REPLACE_WITH_POINTER here; §§3–17 transport, current eligibility, planning and conformance requirements RETAIN_NORMATIVE where not explicitly superseded; older observations/policies remain HISTORICAL_ONLY at claim level. |
| [H4A3](AIOS-H4A3-ACK-ONLY-EXACT-CHAT-CONFORMANCE-v1.md) §§2–6 exact-source/readiness/ACK proof and observations | RETAIN_NORMATIVE specialized conformance gates; exact historical TASK/workflow evidence remains HISTORICAL_ONLY observations, without making the whole procedure historical. |
| [H4A4](AIOS-H4A4-DURABLE-WAKE-RECOVERY-CONFORMANCE-v1.md) readiness/durable-state/live matrix and assessment | RETAIN_NORMATIVE independent privacy, generation, no-resend, isolation and proof obligations; original terminal-only/config/cadence/flight claims are HISTORICAL_ONLY where later published contracts supersede them. |
| [H4A5](AIOS-H4A5-COMPLETE-ATTENTION-COVERAGE-CONFORMANCE-v1.md) source/family matrix, bounded live proof, residuals and dated closure disposition | RETAIN_NORMATIVE conformance/disposition authority; dated replay observations remain HISTORICAL_ONLY evidence; superseded lane-flight policy is historical at claim level. |
| [H4B](AIOS-H4B-REGULAR-CHAT-BRAIN-RESUME-CONFORMANCE-v1.md) Existing contract generic composition; Minimum fresh context generic bootstrap | REPLACE_WITH_POINTER here; exact review-scope/fingerprint obligations, offline proof, bounded live procedure, residuals/reopen/closure gates RETAIN_NORMATIVE specialized conformance. |
| [H4C0](AIOS-H4C0-ORIGIN-CAPTURE-CONFORMANCE-v1.md) §§1–5 prior probe, §§6–14 fallback implementation/live procedures/proof and observations | RETAIN_NORMATIVE independent feasibility, privacy, exact-origin and live-proof requirements within their bounded contracts; old source-specific eligibility/observations remain HISTORICAL_ONLY, not current generic navigation. |
| [TASK Authoring](AIOS-RENEW-BRAIN-TASK-AUTHORING-CONTRACT.md) identity/scope/acceptance/verification contract | RETAIN_NORMATIVE authoring authority; operational traversal is REPLACE_WITH_POINTER here. |
| [Semantic Handoff Hardening](AIOS-BRAIN-RUNTIME-SEMANTIC-HANDOFF-HARDENING-v1.md) §8.2 generic resume instructions | REPLACE_WITH_POINTER here; H4B fresh-context/one-step conformance requirements, §§3–12 planning/protocol/exit authority remain RETAIN_NORMATIVE except explicitly superseded claims. |
| [GitHub Brain Wake Audit](AIOS-GITHUB-CHATGPT-BRAIN-WAKE-AUTOMATION-AUDIT-v1.md) §§1–19 Work route/probes and loop examples | HISTORICAL_ONLY superseded Work navigation and dated immutable observations; §20 Work-retirement decision remains RETAIN_NORMATIVE, while its interim Human relay claim is superseded. Current generic navigation is REPLACE_WITH_POINTER here. |
| Linked current Python/workflow leaves | IMPLEMENTATION_LEAF; live admission/trust checks stay authoritative for implementation behavior and are not retired by navigation. |

No immutable historical observation is deleted. No whole-file historical label erases
active H4 planning/conformance authority. No REMOVE_OBSOLETE instruction is needed to
remove evidence: redirects retire duplicate navigation while retaining bounded proof.
The [H4 execution roadmap](AIOS-H4-EXECUTION-ROADMAP-v1.md), current planning bookmark
and approved architecture remain independent planning sources, not ordinary navigation
alternatives and not evidence that implementation/lifecycle milestones are complete.
