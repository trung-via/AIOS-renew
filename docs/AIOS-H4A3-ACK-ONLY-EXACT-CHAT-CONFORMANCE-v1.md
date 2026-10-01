# AIOS H4A3 ACK-only Exact-chat Conformance v1

Status: conformance procedure; live proof is not asserted by this document.
Implementation authority: TASK-268 revision 1, documentation only.
Live observation and planning closure authority: Human/Brain.

## 1. Purpose and boundary

This procedure defines the H4A3 live exact-chat proof using the published H4A2
Local Regular Chat Wake transport. It supplements
[AIOS Local Regular Chat Wake v1](AIOS-LOCAL-REGULAR-CHAT-WAKE-v1.md) without
changing production transport, workflows, tests, TASKs, Runtime, Reviewer,
Publisher, or roadmap state. TASK-266 published the H4A2 capability at candidate
`0a3ca954b11c2e5323a43f5133212407e4d26093`.

The probe is ACK-only in semantic effect. Delivery proves a bounded doorbell
reached one exact Human-bound regular ChatGPT conversation; it does not prove
engineering correctness or authorize semantic continuation. H4B remains blocked
until Human/Brain closes H4A3 on the required live proof.

## 2. Eligible positive source: TASK-268's real RESULT

The positive live-probe source MUST be the real canonical TASK-268 RESULT
terminal-attention event, produced through its normal bound RUN and Runtime
lifecycle. The Executor's candidate commit or ResultPackage is not that terminal.
Only after Runtime produces the canonical RESULT may its exact terminal-attention
identity be used. No future terminal artifact SHA is supplied or guessed here.

The existing terminal-attention workflow must successfully admit the exact
canonical RUN, `RESULT` terminal kind, terminal artifact commit SHA, and matching
attention refs under its published admission policy. Establish from canonical
lineage that this RUN belongs to TASK-268 revision 1 and that the terminal is
current and unresolved for attention. A syntactically valid event string, Issue,
or successful overall workflow status alone is not admission proof.

The identity shape below is explanatory only; both placeholders MUST come from
the real successfully admitted terminal outputs:

```text
terminal:RESULT:<exact TASK-268 RUN id>:<exact canonical RESULT artifact SHA>
```

Use the admitted `artifact_sha`, not a candidate HEAD, workflow checkout SHA, or
attention signal commit SHA. The published handoff constructs the event identity
from these exact outputs. Later Issue creation or selector/artifact observability
failure does not invalidate a successful admission or suppress its authorized
local wake; conversely, an Issue cannot substitute for admission.

MUST NOT fabricate a RUN identity, RUN-like transport identity, RESULT, FAILURE,
REVIEW, Git ref, or synthetic canonical event for this probe. TASK-268's real
RESULT is required even though the earlier H4A2 planning document described a
synthetic or bounded first probe. A historical TASK-266/TASK-267 terminal, a stale
terminal, an already-resolved terminal, or a FAILURE is not a positive substitute.
If the real source is not yet available or has ceased to be eligible before live
proof, leave H4A3 open and return the source/timing question to Human/Brain. Do not
manufacture a terminal or reopen resolved lifecycle state to obtain a wake.

## 3. Human-owned operational readiness

Before live delivery, the Human MUST establish all of the following:

1. An explicit exact machine-local binding to one durable regular ChatGPT
   Project conversation, with local transport configuration and dedupe state
   outside committed repository state.
2. An already-running authenticated regular-Chat browser target with exactly one
   provably matching bound page. The composer must be empty and idle, with no
   unsent draft or active generation. Manual Human use takes priority.
3. The Human-operated workflow repository variable
   `AIOS_LOCAL_CHAT_WAKE_ENABLED=true` (the literal lower-case value `true`).
   Both the terminal handoff and reusable local wake job retain this gate.
4. An ACK-only understanding in the bound Brain conversation before the wake:
   acknowledge the doorbell only, with no semantic continuation or lifecycle
   mutation for this probe.

These are noncanonical operational prerequisites, not TASK acceptance facts or
engineering state. `AIOS_LOCAL_CHAT_WAKE_CONFIG` identifies the Human's local
configuration; it is not a workflow input or repository-owned binding. This
document supplies no actual binding, conversation URL/identity, credential,
cookie, browser profile, CDP endpoint, state path, or provider/session data.
Do not commit, infer, export, or copy such private values into planning notes,
transport receipts, TASKs, or Runtime EVIDENCE.

Prepare readiness before the expected RESULT attention delivery where possible.
If readiness is absent, disabled, or unprovable, do not attempt the live probe or
claim it passed or failed. H4A3 stays open. A skipped gate or missing Human setup
must not be fabricated into canonical FAILURE, review truth, or lifecycle truth.
If a later authorized delivery of the same real source is considered, its current
eligibility must still be established; delayed readiness never licenses stale
terminal substitution.

The existing adapter attaches only to the already-running target. This procedure
does not authorize a replacement browser/profile, navigation of an unrelated
chat, draft clearing, bypass of the enable gate, or ChatGPT Work fallback.

## 4. Positive observation procedure

This is a future Human/Brain live procedure, not an instruction for the TASK-268
Executor to run a live probe or canonical verification.

1. Confirm operational readiness and the ACK-only boundary. After the real
   TASK-268 RESULT exists, observe exact successful terminal-attention admission
   and bind the observation to its actual admitted identity as specified above.
2. Let the existing published terminal-attention handoff deliver that identity
   through the local wake path. Do not introduce another AIOS execution, new
   canonical event, workflow change, or semantic command to trigger the probe.
3. Observe the bounded local receipt for this exact identity:
   `status: SUBMITTED`, `reason: EXACT_USER_TURN_PROVEN`.
   The published state writes `ATTEMPTING` before submit and `SUBMITTED` only
   after bounded outbound-turn/composer proof. These are local operational
   metadata, not canonical lifecycle status.
4. In the privately bound chat, establish exactly one matching outbound **user**
   wake turn. Match the entire published doorbell text, including the admitted
   event identity and fixed repository, not a partial substring or assistant ACK:

   ```text
   [AIOS LOCAL CHAT WAKE]
   event_id: <exact admitted TASK-268 RESULT event identity>
   repository: trung-via/AIOS-renew
   fresh_brain_sync_required: true
   ```

5. Through the existing bounded transport, perform an authorized duplicate
   delivery of the **same admitted identity**, keeping the same Human binding and
   persistent local dedupe state. This is duplicate transport delivery, not a new
   RUN or terminal. The duplicate must return `status: NOOP`,
   `reason: ALREADY_SUBMITTED`, and the bound chat must still contain exactly one
   matching wake user turn, with no second turn. Do not reset/delete dedupe state,
   alter the identity, or resend an uncertain attempt to obtain this observation.

Positive conformance requires all four observations together: exact terminal
admission, matching local `SUBMITTED` receipt, exactly one matching user turn in
the exact bound chat, and duplicate `NOOP` with no second turn. A receipt alone,
message in another chat, screenshot without exact identity, or assistant reply
does not establish the positive proof.

If submission is ambiguous, stop. `ATTEMPTING`, `BLOCKED`, uncertain state writes,
or inability to prove submission must not be treated as `SUBMITTED`; never blindly
resend. Preserve the bounded operational receipt for Human inspection without
exposing local configuration or session material.

## 5. Negative-case basis and contrary live observations

Use the reviewed TASK-266 deterministic coverage as the existing basis for busy,
draft, generation, wrong-chat, logged-out, and ambiguous-send negative cases.
Do not require destructive live negative-case ceremony, create a Human draft to
overwrite, log out the target, switch chats during submission, or force an
ambiguous send merely to repeat those cases.

The bounded reference surfaces are:

| Case | Reviewed deterministic coverage surface |
| --- | --- |
| Busy/disabled composer, draft, active generation, logged-out or wrong regular-Chat surface | `tests/test_local_chat_wake.py`: preflight blocks before composer modification; Human draft races block without clearing text |
| Wrong chat or nonunique target | `tests/test_local_chat_wake.py`: zero/multiple target pages, unrelated pages without navigation, and exact submission proof rejecting wrong chat/second target |
| Ambiguous send or state persistence | `tests/test_local_chat_wake.py`: ambiguous attempt never resends, interrupted writes block, final state-write failure retains `ATTEMPTING` and prevents resend |
| Enable gate and exact admitted handoff | `tests/test_local_chat_wake_workflow.py` and `tests/test_terminal_attention_workflow.py`: gate, complete successful admission outputs, bounded permissions, and post-admission observability independence |
| Exact terminal provenance | `tests/test_terminal_attention.py`: strict selectors, remote binding revalidation, conflicting terminal/artifact rejection, and inert exact replay |

This procedure references that reviewed basis; it does not rerun it, create new
verification EVIDENCE, or assert a new test result. Contrary live evidence must
not be discarded because deterministic coverage exists. Stop the probe and give
Human/Brain only bounded, sanitized operational observations. If those observations
reveal a production transport defect, a **separate Human/Brain-authorized
correction** must be created. TASK-268's documentation-only authority never widens
to patch transport, workflows, tests, Runtime, Reviewer, or Publisher.

## 6. ACK-only semantics and distinct completion owners

The regular Chat Brain may acknowledge the doorbell but MUST NOT create or mutate
TASK, RUN, RESULT, FAILURE, REVIEW, REMEDIATION, REPAIR, publication, or roadmap
state because of this probe. The doorbell's `fresh_brain_sync_required: true`
marker carries no semantic-action authority. Fresh reconstruction, if performed,
cannot turn H4A3 into H4B or a review/continuation step. The transport cannot choose
`next_action`, correction strategy, review verdict, or roadmap successor.

Assistant output is never transport input or conformance evidence. Do not extract,
parse, scrape, store, or use assistant replies or conversation history to decide
probe success or drive lifecycle action. Bounded examination of the exact
outbound user doorbell and local send/composer state is the permitted observation
surface. An ACK is optional and its contents are not a gate.

Keep the completion boundaries distinct:

| Boundary | Owner and meaning |
| --- | --- |
| TASK-268 implementation | Executor adds this single conformance document and commits the permitted candidate; no live-delivery success claim |
| TASK-268 canonical verification and EVIDENCE | Runtime verifies the documentation TASK and produces its canonical RESULT; future live readiness/delivery is not a prerequisite for that RESULT |
| Semantic review and publication | Existing Reviewer and Publisher contracts apply independently; a delivered doorbell or ACK cannot substitute for either |
| H4A3 live planning proof | Human/Brain assesses the four required observations and the reviewed negative-case basis; sanitized notes may record canonical event identity, admission, bounded receipt statuses, exact-chat match/count, and duplicate outcome without private chat/session data |
| H4A3 roadmap closure and H4B sequencing | A separate Human/Brain planning mutation after live proof; never automatic from execution, Runtime verification, review, publication, wake delivery, or an assistant ACK |

This document's existence, TASK-268 lifecycle completion, and the published H4A2
transport do not alone establish H4A3 live conformance. Live observations are
Human/Brain planning-conformance material, not Executor claims or Runtime
EVIDENCE. H4A3 remains open until that planning authority assesses the actual
required proof and explicitly closes the milestone.
