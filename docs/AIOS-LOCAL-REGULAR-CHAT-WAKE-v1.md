# AIOS Local Regular Chat Wake v1

Status: HUMAN/BRAIN PLANNING CANDIDATE
Approved priority: 2026-10-01
Audit profile: `brain-high-value-v2`
Scope: GitHub/AIOS semantic attention -> exact existing regular ChatGPT Project conversation
Supersedes for production wake: `HUMAN_WAKE_RELAY_V1`
Preserves as historical evidence only: ChatGPT Work wake/ACK conformance

## 1. Human objective

When canonical AIOS state reaches a semantic checkpoint, wake the Human-visible regular
ChatGPT Brain automatically in one exact bound conversation without using ChatGPT Work,
without consuming the Work/Codex shared allowance for wake transport, and without making
the wake transport a Brain, Reviewer, Planner, Runtime, Publisher or lifecycle router.

Target shape:

```text
AIOS/GitHub canonical attention
        |
        v
deterministic bounded wake event
        |
        v
local exact-chat wake transport
        |
        v
bound regular ChatGPT Project conversation
        |
        v
one bounded wake message is submitted
        |
        v
regular Chat Brain performs fresh Brain Sync
        |
        v
existing Flow Resolver / Brain / Reviewer / ingress contracts
```

The Human observes semantic progress in the bound ChatGPT conversation. Canonical
engineering truth remains repository state and exact immutable lineage.

## 2. Evidence basis

Current OpenAI product surfaces do not provide an ordinary Plus Project-chat public API
that accepts an existing `chatgpt.com/c/<conversation-id>` and appends a turn from an
external GitHub event without using Work or a separately billed API/managed-agent
surface.

External implementations demonstrate the technical browser/session pattern but are not
AIOS authority:

- `cobuildwithus/review-gpt`: reopens an exact ChatGPT conversation URL, can auto-send
  a follow-up, validates the selected chat surface, preserves exact thread identity and
  fails closed on uncertain submission.
- `qayshp/chatgpt-playwright-backend`: attaches to an already-authenticated Chrome
  session over CDP, opens/selects an exact conversation, refuses mismatched/busy/draft
  states and warns against automatic resend after uncertain submission.
- `yudduy/chatgpt-pro-web`: uses a persistent browser profile and supports
  `--continue <conversation-url>`.
- `eimexdev/RightClickGPT`: browser-extension content-script pattern for locating the
  ChatGPT composer and submitting a prompt.

These are feasibility evidence only. AIOS does not import their lifecycle semantics and
does not require any one implementation technique.

## 3. Authority boundary

The local wake transport owns only delivery of one bounded Human-visible wake message to
one exact operationally bound regular ChatGPT conversation.

It MUST NOT:

- infer or carry authoritative `next_action`;
- select correction strategy;
- perform semantic review;
- allocate TASK/RUN/REVIEW/REPAIR identities;
- invoke Executor, Runtime verification or Publisher;
- read/parse assistant output to drive lifecycle actions;
- scrape conversation history as engineering truth;
- advance roadmap state;
- retry an ambiguous accepted submission;
- use ChatGPT Work as a fallback.

The regular Chat Brain retains the existing Brain/Reviewer authority selected after
fresh canonical reconstruction. Runtime and Publisher boundaries are unchanged.

## 4. Operational chat binding

The exact target conversation is replaceable transport configuration, not canonical
engineering state.

The binding MUST:

- identify one exact durable `https://chatgpt.com/c/<conversation-id>` target;
- remain outside committed repository state;
- be explicitly established or changed by the Human;
- never be treated as TASK/RUN/roadmap identity or semantic authority;
- fail closed when missing, malformed, logged out, on the wrong ChatGPT surface or
  otherwise unprovable.

A future supported OpenAI conversation-trigger API may replace this browser/session
transport without changing the semantic contract.

## 5. Wake message

The transport-submitted message is a doorbell, not truth. It should be minimal and
bounded, for example:

```text
[AIOS LOCAL CHAT WAKE]
event_id: <deterministic attention event id>
repository: trung-via/AIOS-renew
fresh_brain_sync_required: true
```

It MUST NOT include copied TASK semantics, logs, a review verdict, correction strategy,
roadmap successor, model selection or lifecycle command.

On receipt, the regular Chat Brain must ignore semantic claims from the message, perform
fresh Brain Sync from canonical `main` and exact lineage, verify that the event remains
unresolved, resolve the current flow and take only the authority allowed by current
canonical contracts.

## 6. Delivery and idempotency

Each canonical attention subject has one deterministic event identity.

Before sending, the local transport must establish that:

1. the event is eligible for Brain attention;
2. the exact target conversation is the configured target;
3. the ChatGPT session is authenticated and on regular Chat;
4. there is no existing unsent draft;
5. no response generation is active;
6. the same event has not already been proven submitted.

Successful submission proof is operational only. It may use bounded local delivery
metadata but must not become a second engineering-state database.

Duplicate source delivery is a NOOP. If send acceptance is ambiguous, the adapter stops
and surfaces a Human-visible operational failure; it never blindly resends.

## 7. Concurrency and Human interaction

Manual Human use of the bound conversation outranks automated wake delivery.

If the conversation is busy, contains a draft, is generating, is on another durable
conversation identity or cannot prove its target, the adapter fails closed or defers
through a bounded transport mechanism without semantic interpretation.

The adapter must not overwrite Human text or navigate away from an active unrelated
conversation merely to deliver a wake.

## 8. Privacy and output boundary

The wake transport does not require assistant-response extraction.

Production v1 ends its authority once the exact wake user turn is proven submitted.
The ChatGPT product renders the Brain response directly to the Human in the same
conversation. Any later GitHub mutation is performed through the regular Chat Brain's
authorized connector/ingress flow, not by parsing browser output.

No cookies, credentials or conversation content are committed to the repository.

## 9. H4 sequencing

### H4A — Local Regular Chat Wake transport

Implement and prove the bounded transport contract only:

- canonical attention -> local delivery signal;
- exact operational chat binding;
- authenticated regular-Chat target validation;
- one-message dedupe;
- busy/draft/generation protection;
- ambiguous-send fail-close;
- zero ChatGPT Work invocation;
- no assistant-output extraction.

The first live conformance probe is ACK-only in semantic effect: one fresh synthetic or
bounded attention event must cause exactly one short wake message to appear in the exact
bound conversation. The Brain side performs no lifecycle mutation for this probe.

### H4B — Regular Chat Brain resume

After H4A transport conformance passes, prove that one real unresolved canonical semantic
checkpoint can wake the bound conversation and that the regular Chat Brain:

1. performs fresh Brain Sync;
2. reconstructs exact canonical lineage;
3. resolves the current Flow Card;
4. occupies only the selected authority;
5. uses the existing Brain/Reviewer protocol and canonical ingress;
6. takes at most one semantic continuation step for that wake;
7. stops at a Human intent/priority/risk boundary;
8. leaves a Human-visible result in the same conversation.

No ChatGPT Work task is part of this path.

## 10. H5 exit gates added by this revision

Hardening integration cannot close until live evidence proves:

- Work wake automations remain disabled for production AIOS wake;
- one eligible canonical attention event produces exactly one message in the exact bound
  regular ChatGPT conversation;
- duplicate delivery does not duplicate the wake turn;
- stale/resolved events do not produce a new wake;
- wrong chat, logged-out state, existing draft and active generation fail closed;
- uncertain submission does not auto-resend;
- the transport never reads assistant output to choose lifecycle action;
- the woken regular Chat performs fresh canonical reconstruction before semantic action;
- Human can observe the semantic result in that exact conversation;
- canonical state and roadmap remain independent of conversation/session identity.

## 11. Two-stage architecture audit

Stage 1 — CONSTRUCT: **RISK_FOUND**.

Material risks:
- unstable/private browser UI surface;
- exact-conversation substitution;
- duplicate and ambiguous submission;
- concurrent Human/chat generation interference;
- authentication/profile drift;
- accidental Work-surface selection;
- transport becoming a lifecycle agent;
- conversation identity becoming engineering truth.

Stage 2 — ADVERSARIAL_AUDIT_AND_RECONCILE: each material risk is bounded by exact target
binding, regular-Chat validation, one-message authority, deterministic event identity,
busy/draft/generation gates, no blind resend, no assistant-output extraction, local-only
operational binding and mandatory fresh Brain Sync after wake.

Closure: **CLEAR**.
Outcome: **CANDIDATE**.

This candidate changes only the planned wake transport. It does not claim implementation
or conformance, does not alter current TASK lineage and does not advance the active
roadmap milestone automatically.
