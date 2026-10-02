# AIOS H4A3 ACK-only Exact-chat Conformance v1

Status: conformance procedure; live proof is not asserted by this document.
Implementation authority: TASK-269 revision 1, current-main documentation integration only.
Bounded insertion correction and recovery procedure: TASK-274 revision 1.
Reviewed semantic source: TASK-268 revision 1; historical source is not publication authority.
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
engineering correctness or authorize semantic continuation. Current canonical
sequencing is **H4A3 -> H4A4 -> H4A5 -> H4B**: H4A4 starts only after Human/Brain
closes H4A3 on the required live proof; H4A5 starts only after H4A4 closes; H4B
remains blocked until H4A5 closes. H4A3 closure alone does not make H4B eligible.
The Human-approved H4A4/H4A5 planning in the local wake document, semantic handoff
hardening document, and `.ai/roadmap-state.yaml` remains authoritative and unchanged.

## 2. Eligible positive source: TASK-268's real RESULT

The positive live-probe source MUST be the real canonical TASK-268 RESULT
terminal-attention event, produced through its normal bound RUN and Runtime
lifecycle: `RUN-268-001`, terminal kind `RESULT`, artifact commit
`1f3d0dac34c7cff6d768d580f31513df08ebb253`. The Executor's candidate commit or
ResultPackage is not that terminal. The exact later live-probe identity is:

```text
terminal:RESULT:RUN-268-001:1f3d0dac34c7cff6d768d580f31513df08ebb253
```

Its matching signal ref is
`refs/heads/aios/terminal-attention/RESULT/RUN-268-001/1f3d0dac34c7cff6d768d580f31513df08ebb253`.
TASK-269's own terminal is not a replacement positive source.

The immutable reviewed semantic input is TASK-268 candidate
`31eb283ad8eb07695f26e940509696c38e62b3a4`, document blob
`3836cabc48139bec19e1a9dd80caecacdc34ba9b`, and PRIMARY PASS
`REVIEW-268-001` at decision commit
`7a52a4c82490a4f0a3684b3517404a7cd42bd778`. Publication workflow run
`36947006121` returned `INTEGRATION_REQUIRED` because current main diverged;
this was not a RUN or semantic failure. That historical PASS does not authorize
direct publication of the old candidate or replacement of current-main planning.
TASK-269 realizes the contract as a fresh current-main descendant for its own
Runtime verification, semantic review, and eligible reviewed-source publication.

The existing terminal-attention workflow must successfully admit the exact
canonical RUN, `RESULT` terminal kind, terminal artifact commit SHA, and matching
attention refs under its published admission policy. Establish from canonical
lineage that this RUN belongs to TASK-268 revision 1 and that the terminal is
current and unresolved for attention. A syntactically valid event string, Issue,
or successful overall workflow status alone is not admission proof.

Revalidate the exact source and current attention eligibility at the later replay;
the historical RESULT and review do not by themselves prove current admission.
Admitted outputs MUST match `RUN-268-001`, `RESULT`, and
`1f3d0dac34c7cff6d768d580f31513df08ebb253`. Use the admitted `artifact_sha`,
not a candidate HEAD, workflow checkout SHA, or attention signal commit SHA. The published handoff constructs the event identity
from these exact outputs. Later Issue creation or selector/artifact observability
failure does not invalidate a successful admission or suppress its authorized
local wake; conversely, an Issue cannot substitute for admission.

MUST NOT fabricate a RUN identity, RUN-like transport identity, RESULT, FAILURE,
REVIEW, Git ref, or synthetic canonical event for this probe. TASK-268's real
RESULT is required even though the earlier H4A2 planning document described a
synthetic or bounded first probe. A historical TASK-266/TASK-267 terminal, a stale
terminal, an already-resolved terminal, or a FAILURE is not a positive substitute.
If the exact real source is unavailable or has ceased to be eligible before live
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

Prepare readiness before the authorized exact-terminal replay.
If readiness is absent, disabled, or unprovable, do not attempt the live probe or
claim it passed or failed. H4A3 stays open. Missing local readiness or skipped
delivery establishes neither engineering failure nor conformance success. A
skipped gate or missing Human setup must not be fabricated into canonical FAILURE,
review truth, or lifecycle truth.

If a later authorized delivery of the same real source is considered, its current
eligibility must still be established; delayed readiness never licenses stale
terminal substitution.

The existing adapter attaches only to the already-running target. This procedure
does not authorize a replacement browser/profile, navigation of an unrelated
chat, general draft clearing, bypass of the enable gate, or ChatGPT Work fallback.
The sole Human draft/event removal exception is the exact historical pre-click
attempt in section 4.1; ordinary delivery has no recovery or clearing authority.

## 4. Positive observation procedure

This is a later Human/Brain live procedure, not an instruction for the TASK-269
Executor to run a live probe or canonical verification. It is permitted only after
TASK-269 reviewed publication succeeds and Human-owned local readiness is established.
TASK-269 implementation neither dispatches nor performs this replay.

1. Confirm TASK-269 reviewed publication, operational readiness, and the ACK-only
   boundary. Revalidate the exact RUN-268-001 RESULT source and current eligibility
   through the existing terminal-attention `workflow_dispatch` replay surface.
   Observe exact successful admission and bind the observation to the actual
   admitted identity specified above.
2. Let that existing published terminal-attention handoff deliver the exact identity
   through the local wake path. This replay is transport delivery of the existing
   terminal, not another AIOS execution or a new canonical event. Do not change
   workflows or introduce a semantic command to trigger the probe.
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
   event_id: terminal:RESULT:RUN-268-001:1f3d0dac34c7cff6d768d580f31513df08ebb253
   repository: trung-via/AIOS-renew
   fresh_brain_sync_required: true
   ```

5. Through the same existing terminal-attention `workflow_dispatch` replay surface
   and bounded transport, perform an authorized duplicate delivery of the **same
   admitted identity**, revalidating admission and keeping the same Human binding and
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

### 4.1. One-attempt Human recovery of the known pre-click INSERT_BLOCKED

This exception applies **only** to workflow run `36963244174`, attempt `2`,
whose terminal-attention admission was PASS and whose local receipt was
`BLOCKED / INSERT_BLOCKED` for
`terminal:RESULT:RUN-268-001:1f3d0dac34c7cff6d768d580f31513df08ebb253`.
Its transport checkout was `dc435f7d799ba8208869afcef3b54fedc712adc3`.
These identifiers bind the historical attempt, not just a reusable event string.
TASK-274 supplies this bounded procedure and synthetic insertion correction;
its Executor does not perform recovery, a live submission, or H4A3 replay.

Before either removal, the Human MUST privately establish **all** preconditions:

1. Fresh canonical reconstruction retains the exact RUN-268-001 RESULT lineage
   in section 2 and the recorded workflow attempt above. The canonical replay
   observation and privacy-safe reconciliation establish that this specific
   failure occurred **before Send click**, with no submission and no source
   consumption. `INSERT_BLOCKED` alone, absence of a user turn alone, or a text
   length alone is insufficient. Any contrary or uncertain observation stops
   recovery and leaves H4A3 open.
2. The Human-owned external state contains this exact event as `ATTEMPTING`,
   attributable to that single historical attempt. There has been no intervening
   delivery or state reset. No `.lock` or `.pending` file, in-flight local wake,
   other state writer, or uncertain write exists. Pause competing delivery and
   Human edits for this bounded inspection/removal; do not remove a lock or
   pending file to manufacture these preconditions.
3. The unchanged private binding identifies exactly one bound page, with the
   exact complete conversation URL, one authenticated account indicator, one
   regular-chat composer and one main surface; no login, nonregular surface,
   active generation, or disabled composer is present. No navigation or binding
   substitution is permitted. The known unsent attempt has no visible Send
   control; an unexpected control/state change requires fresh Human assessment
   rather than assuming it is the historical unchanged draft.
4. The unsent composer equals the entire fixed four-line doorbell in section 4,
   using the insertion correction's **same exact equivalence contract**: exact
   logical text and text content, or exactly one flat P/DIV block (or root text
   node) per logical line, with exact characters/order and only zero, one, or two
   rendered newlines at each proven DOM block boundary. Every boundary must
   touch a block; block children must be text nodes and blocks visible. No
   leading/trailing separator, trim, generic repeated-newline collapse,
   whitespace/case folding, substring, substituted line, extra node or nested
   block normalization is allowed. Private comparison produces only bounded
   match/guard booleans; do not export composer or conversation text.
5. Bounded inspection of the **exact outbound user doorbell only** proves there
   is no matching outbound user turn in that bound page. Do not read assistant
   output or scrape history. This absence is corroboration of the independent
   pre-click observation in item 1, never an independent resend license.

If every condition holds, the Human may remove **only that exact unsent draft**
from that exact composer. Immediately before removal, recheck unchanged payload,
page/surface, absence of matching outbound user turn and no concurrent write or
edit. Any Human-added content blocks removal; preserve it. Do not click Send.
Then privately confirm the composer is empty, the exact bound page and guards
still hold, and no matching outbound user turn has appeared. Recheck the same
historical event is still `ATTEMPTING` and that lock/pending/write ambiguity is
still absent. Only then may the Human remove **that single event entry** from
the external operational state, preserving its schema and every other entry.
Do not delete/reinitialize the state file or change canonical lifecycle state.
If either step cannot be completed unambiguously, stop without automatic retry.

This consumes the exception for that one historical attempt. It cannot authorize
any other `ATTEMPTING` event, another workflow attempt, or a subsequent attempt
of the same event, even if its receipt says `INSERT_BLOCKED`. Normal `deliver()`
continues to return `ATTEMPT_REQUIRES_HUMAN` without attaching, clearing, resetting,
or retrying any existing `ATTEMPTING` entry. No generalized recovery is introduced.

Recovery is not replay permission or positive conformance. After reviewed
publication of TASK-274, any later one-shot replay requires separate Human/Brain
planning, fresh exact-source admission/readiness and the ACK-only boundary in
sections 2–4. The exact user-turn and duplicate NOOP proof remain later
Human/Brain observations; H4A3 remains open and no H4A4/H4A5/H4B advancement is
authorized by this exception.

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
correction** must be created. Neither TASK-268's documentation-only authority nor
TASK-269's integration authority widens to patch transport, workflows, tests,
Runtime, Reviewer, or Publisher.

## 6. ACK-only semantics and distinct completion owners

The regular Chat Brain may acknowledge the doorbell but MUST NOT create or mutate
TASK, RUN, RESULT, FAILURE, REVIEW, REMEDIATION, REPAIR, publication, or roadmap
state because of this probe. The doorbell's `fresh_brain_sync_required: true`
marker carries no semantic-action authority. Fresh reconstruction, if performed,
cannot turn H4A3 into H4A4, H4A5, H4B, or a review/continuation step. The transport
cannot choose `next_action`, correction strategy, review verdict, or roadmap successor.

Assistant output is never transport input or conformance evidence. Do not extract,
parse, scrape, store, or use assistant replies or conversation history to decide
probe success or drive lifecycle action. Bounded examination of the exact
outbound user doorbell and local send/composer state is the permitted observation
surface. An ACK is optional and its contents are not a gate.

Keep the completion boundaries distinct:

| Boundary | Owner and meaning |
| --- | --- |
| Historical TASK-268 lineage | RUN-268-001 supplies the exact real RESULT source; REVIEW-268-001 PRIMARY PASS supplies reviewed semantic input, not direct publication authority |
| TASK-269 implementation | Executor integrates and commits only this conformance document on current main, preserving inherited planning; no replay or live-delivery success claim |
| TASK-269 canonical verification and EVIDENCE | Runtime verifies this integration TASK and produces its canonical RESULT; future live readiness/delivery is not a prerequisite and that RESULT does not replace RUN-268-001 as the probe source |
| Semantic review and publication | Reviewer retains the semantic verdict and Publisher retains exact eligible reviewed-source publication of TASK-269; historical PASS, a delivered doorbell, or an ACK cannot substitute for either |
| H4A3 live planning proof | Human/Brain assesses the four required observations and the reviewed negative-case basis; sanitized notes may record canonical event identity, admission, bounded receipt statuses, exact-chat match/count, and duplicate outcome without private chat/session data |
| H4A3 roadmap closure and successor sequencing | Separate Human/Brain planning mutations: H4A3 live proof closes before H4A4, H4A4 closes before H4A5, and H4B stays blocked until H4A5 closes; never automatic from execution, Runtime verification, review, publication, wake delivery, or an assistant ACK |

This document's existence, TASK-268 or TASK-269 lifecycle completion, and the
published H4A2 transport do not alone establish H4A3 live conformance. Live
observations are Human/Brain planning-conformance material, not Executor claims or Runtime
EVIDENCE. H4A3 remains open until that planning authority assesses the actual
required proof and explicitly closes the milestone.
