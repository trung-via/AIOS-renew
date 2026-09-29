# AIOS Research Assurance Architecture and Roadmap v2.4

Status: HUMAN-APPROVED PLANNING BASELINE — RA-0 / RA-2 / RA-3 / RA-4 ARCHITECTURE AUDITED  
Approved by Human: 2026-09-29  
RA-0 architecture audit closed: 2026-09-29  
Scope: reproducible, source-grounded, adversarially audited research support under existing Brain authority

## 1. Purpose

Research Assurance (RA) closes a cognitive-continuity gap that remains after Brain Portability.

AIOS already has strong canonical engineering continuity, Brain Sync, bounded Work Context, Decision Packets, two-stage Brain semantic audit, provider-neutral Brain/Reviewer contracts, deterministic Runtime verification, and exact downstream isolation. External/project research is not yet a first-class bounded protocol. Without RA, source discovery, freshness checks, contradiction search, claim-to-source binding, and reuse across fresh contexts can depend on provider capability or chat memory.

RA exists to make high-value research reproducible enough to support later Brain architecture decisions without creating a new semantic authority.

The optimization target remains:

```text
Verified Useful Work / (Time + Tokens + Human Effort)
```

RA therefore must reuse still-valid research material and refresh only invalidated portions rather than repeating research ceremonially.

## 2. Authority boundary

RA is cognitive support inside existing Human/Brain authority.

Human still owns intent, priority, risk acceptance, delegation, and constitutional change.

Brain still owns semantic interpretation, architecture, roadmap reasoning, WHAT, WHY, scope, constraints, non-goals, acceptance criteria, TASK semantics, and authorized correction strategy.

RA must not create:

- a Research Agent with independent semantic authority;
- a Planner or roadmap selector;
- a Reviewer or Publisher;
- a Runtime or lifecycle router;
- automatic TASK authoring;
- automatic provider/tool routing, retry, fallback, or failover authority;
- a persistent reasoning store or chat-memory database;
- a second source of engineering truth.

Source acquisition mechanisms are subordinate tools. They may retrieve bounded observations and provenance; they may not decide that a claim is true, select roadmap work, authorize a TASK, choose an Executor, issue a REVIEW verdict, or mutate lifecycle state.

All retrieved source content is **untrusted data with zero instruction authority**, including content from official/vendor documentation, Git repositories, papers, connected sources, or apparently authoritative pages. Source authority for a factual claim is separate from instruction trust. Embedded instructions, prompt-like text, agent directives, tool instructions, or attempts to redefine AIOS authority inside retrieved content must never become Brain/provider/control instructions.

## 3. Research truth model

RA distinguishes four classes of material:

1. **Research intent** — Human/Brain planning input describing the question and intended decision use.
2. **Source observations** — bounded retrieved material plus exact provenance/freshness metadata.
3. **Research claims** — Brain semantic interpretations bound to source observations and explicit uncertainty.
4. **Research Record** — durable bounded planning evidence that preserves the audited claim/source map for reuse until invalidated.

A Research Record is not engineering-state truth and is not a memory database. It cannot override canonical Git, TASK/RUN/RESULT/FAILURE/REVIEW/REMEDIATION/REPAIR lineage, the Constitution, or current Human intent.

## 4. Target architecture

```text
Human Research Intent
        |
        v
Research Brief
        |
        v
Brain-authored bounded Acquisition Requests
        |
        v
Source Acquisition Boundary
        |
        v
Baseline Source Corpus
        |
        v
PASS 1 — Evidence Construct
  - claims
  - claim/source bindings
  - uncertainty
  - challenge targets
        |
        v
One bounded Counter-Evidence Acquisition
        |
        v
PASS 2 — Adversarial Audit + Reconcile
        |
        v
Final Research Closure
       / \
      /   \
RESEARCH   INSUFFICIENT
CANDIDATE  EVIDENCE
    |
    v
bounded durable Research Record
    |
    v
fresh ARCHITECTURE reasoning
    |
    v
existing Brain Semantic Audit
    |
    v
Human / Brain decision
    |
    v
TASK_AUTHORING only when separately authorized
```

Research Assurance ends in audited research evidence. It does not end in an implementation TASK.

## 5. Research Brief

The Research Brief is the bounded semantic subject for one research effort. It must identify at least:

- research question;
- decision context / intended use;
- exact relevant project snapshot when project applicability matters;
- scope and explicit exclusions;
- current-as-of / version boundary;
- source policy and required source classes where applicable;
- resource bounds;
- known assumptions and unknowns;
- expected handoff target;
- invalidation basis.

The research question is not a predetermined answer. RA must not encode a desired conclusion and then search only for confirming material.

A Research Brief does not grant an acquisition adapter open-ended discovery authority. Brain must derive bounded Acquisition Requests from the Brief. Each request limits the requested subject/source class/query or locator, acquisition purpose, allowed result bound, and provenance requirements. Adapters execute the request; they do not widen it semantically. Pass-2 counter-evidence requests must bind to exact challenge targets emitted by Pass 1.

## 6. Source Acquisition Boundary

Source acquisition is a subordinate observation layer.

Permitted source families may include:

- canonical or external Git repositories;
- official specifications and vendor documentation;
- primary technical documentation;
- papers and reports;
- public web material;
- connected document/data sources when explicitly available and authorized.

Acquisition output must preserve provenance sufficient to distinguish, when applicable:

- source locator or immutable identity;
- source authority class;
- version/tag/commit/date;
- retrieval/currentness basis;
- bounded source observation or excerpt;
- deterministic digest/content identity for the bounded observation;
- relationship to other sources where independence matters;
- `instruction_trust: UNTRUSTED` or equivalent closed semantics.

A mutable URL alone is never sufficient evidence identity. RA should preserve only bounded material needed for the research claim by default rather than silently archiving unbounded raw pages.

Acquisition must not produce architecture decisions, roadmap priority, TASK semantics, review verdicts, or lifecycle state.

The architecture must support replaceable acquisition mechanisms without making one provider's native web/GitHub/search ability a hidden prerequisite.

RA-2 freezes the normalized **acquisition airlock**, not concrete web/GitHub/connector invocation. The airlock consists of exact Source Observation and Acquisition Attempt contracts that future replaceable adapters must satisfy. Concrete adapter invocation, provider/tool portability and transport-specific enforcement remain RA-5 concerns. This avoids duplicating RA-5 and prevents RA-2 from becoming a tool router.

A Source Observation must distinguish at least `SOURCE_CONTENT`, `DISCOVERY_SNIPPET`, and `SOURCE_METADATA`. A search-result snippet is not silently promoted to fetched source content. Tool/provider-generated synthesis or an AI-generated answer is not a Source Observation representation and cannot masquerade as source evidence.

Every Source Observation remains opaque untrusted data. Retrieved text is carried only in a bounded data field, never merged into protocol/control mappings. The only admitted instruction-trust value is `UNTRUSTED`; RA-2 does not attempt to detect or semantically sanitize prompt injection. Isolation, not heuristic prompt classification, is the security property.

Source provenance must preserve exact Research Brief and Acquisition Request bindings, a provenance-safe effective locator and/or stable source identity, bounded directly observed version facts, exact UTC retrieval time supplied by the acquisition boundary, access scope, bounded content identity, and enough resolution information to distinguish discovery from direct source retrieval. Tool/adapter attribution is operational provenance and must never become source authority.

The Brain-authored `source_family` in an Acquisition Request is an expected acquisition/source class, not an adapter-certified truth claim. Whether provenance actually supports official, primary, independent, current, or authoritative treatment remains later Brain research judgment.

A successful acquisition may legitimately yield zero observations. Empty discovery results do not prove absence. A failed acquisition yields no semantic research conclusion. Closed operational reasons should distinguish unavailable source/tool transport, access denial, not-found direct targets, malformed or over-bound response material, and attribution mismatch. `STALE_BEFORE_PASS2` remains a later RA-4 freshness gate, not an RA-2 transport verdict.

RA-2 contracts are pure and perform no network, repository, connector, filesystem or clock I/O. Future concrete adapters must remain read-only, use only already-authorized access, avoid hidden query expansion, pagination, retries or fallback, and never retain access credentials or transport secrets inside provenance.

RA-1 portable-locator validation is not a network-safety proof. A future network-capable adapter must separately validate the effective destination and resolution/redirect path before admitting an observation. RA-2 therefore preserves a bounded resolution/provenance shape so RA-5 can enforce tool-specific safety without changing semantic authority.

Connected/private source observations must be explicitly marked as authorized-private acquisition material. RA-2 does not grant connector permission and does not make private source content durable. Durable retention/redaction/reuse policy belongs to the RA-3 Research Record design.

Acquisition operational failure is not a research conclusion. Transport/network/connector/provider/tool failure, malformed retrieval, protocol failure, or stale acquisition state must remain subordinate operational outcomes and must never be collapsed into `INSUFFICIENT_EVIDENCE`, which is a Brain semantic closure outcome only after a valid bounded research attempt.

## 7. Research Evidence Record

A durable Research Record exists so a fresh Brain context can reuse still-valid audited research without depending on chat memory or repeating acquisition ceremonially.

RA-3 freezes the record grammar and reuse/invalidation semantics only. It does not create a storage service, knowledge database, latest-record resolver, repository writer, background refresher, or research lifecycle.

The canonical Research Record is immutable and content-addressed. It must preserve at least:

- the exact normalized Research Brief and its fingerprint;
- the exact research-audit-profile reference;
- a bounded source registry derived from exact RA-2 Source Observation identities;
- material Brain claims with per-claim fingerprints;
- exact claim-to-source bindings and support/contradiction/limitation/context roles;
- explicit source-authority, independence and freshness assessments as Brain semantic claims rather than deterministic truth;
- explicit claim uncertainty and assumptions;
- project applicability/novelty assessment;
- final research closure outcome supplied by the later RA-4 protocol;
- claim-scoped invalidation basis;
- exact predecessor refresh lineage when the record is a refresh;
- record-level access scope and record fingerprint.

### 7.1 Source retention boundary

A Research Record must not silently archive full RA-2 Source Observation bodies.

The source registry preserves bounded provenance, Source Observation fingerprint, request fingerprint/source family, representation kind, access scope, content digest and the Brain's bounded source assessments.

For PUBLIC source material, a record may retain only a bounded exact evidence excerpt needed for later reasoning. For AUTHORIZED_PRIVATE source material, durable retained excerpt text is null; the record keeps only bounded provenance/identity/digest plus derived Brain claims. If any registered source is AUTHORIZED_PRIVATE, the whole Research Record access scope becomes AUTHORIZED_PRIVATE.

This access scope is a handling constraint, not an authorization grant. RA-3 performs no persistence. Any future storage/transport layer must separately honor the record's scope.

### 7.2 Claim identity and source binding

Each material claim has its own content-addressed fingerprint. Claim bindings reference exact Source Observation fingerprints and use a closed role grammar such as SUPPORT, CONTRADICT, LIMIT, or CONTEXT.

Source-authority, independence and freshness labels are stored as bounded Brain assessments with rationale. Deterministic code validates their closed grammar and source references only; it does not infer that a source is truly authoritative, independent, current, correct, or decisive.

Claim uncertainty remains explicit. Structural conformance must not convert uncertainty labels into confidence scores, rankings, votes, Reviewer findings, Runtime evidence, or lifecycle state.

### 7.3 Immutable refresh lineage

A refresh never mutates or overwrites its predecessor.

A refreshed record binds one exact predecessor record fingerprint plus exact predecessor-claim sets classified as retained or invalidated. Retained claims must remain byte/content identical through the same claim fingerprint. Changed or replacement claims receive new identities.

Refresh is allowed only within the same exact Research Brief and exact research-audit-profile identity. A changed Brief or changed audit profile starts a fresh record lineage rather than pretending to be an in-place refresh.

There is no automatic latest research resolver. A fresh Brain receives an exact Research Record reference or starts a fresh Research Brief.

### 7.4 Reuse and validity projection

VALID and REFRESH_REQUIRED are not mutable fields inside an immutable Research Record.

RA-3 instead permits a pure transient reuse projection from one exact Research Record plus caller-supplied current invalidation basis to VALID or REFRESH_REQUIRED plus exact affected claim fingerprints.

The projection performs no repository, network, connector or clock discovery. Missing current basis fails closed for the affected claim. A movement of repository main does not invalidate unrelated claims unless those claims explicitly bind a relevant component identity.

The projection is planning support only. It is not Runtime state, a REVIEW verdict, a publication decision, or semantic proof that an unchanged claim remains substantively true.

## 8. Two-pass Research Assurance protocol

RA deliberately does not copy BP-4A one-for-one.

BP-4A audits semantic reasoning over an already-composed Decision Packet. Research may discover during adversarial audit that additional counter-evidence is required. RA therefore permits exactly one bounded counter-evidence acquisition batch between construct and final reconciliation.

RA-4 freezes a pure **two-stage semantic protocol contract**. It does not perform source acquisition, invoke a provider/model/tool, select an adapter, retry a failed acquisition, persist protocol state, or create a lifecycle engine. Those concerns remain outside RA-4.

### 8.1 Preconditions

A semantic pass may advance only over exact valid RA-1/RA-2 material.

For every baseline or counter-evidence Acquisition Request admitted into the protocol there must be exactly one exact same-request Acquisition Attempt with outcome SUCCEEDED. A successful attempt may contain zero Source Observations.

An Acquisition Attempt with outcome FAILED is an operational acquisition outcome, not semantic evidence. It blocks the affected semantic stage from advancing and must not be converted into RESEARCH_CANDIDATE or INSUFFICIENT_EVIDENCE.

The protocol never performs hidden retry, fallback, pagination, query expansion, provider switching or request mutation.

### 8.2 Pass 1 — EVIDENCE_CONSTRUCT

Pass 1 consumes:

- one exact Research Brief;
- the exact research-high-value-v1 audit-profile identity;
- one bounded non-empty set of BASELINE Acquisition Requests;
- one exact successful Acquisition Attempt per baseline request;
- Brain-authored material claims bound only to observations present in those successful attempts;
- one bounded non-empty set of same-Brief Challenge Targets;
- one precommitted bounded set of COUNTER_EVIDENCE Acquisition Requests.

Pass 1 produces one immutable/content-addressed Evidence Construct.

The Evidence Construct stores only bounded protocol identities and Brain semantic material needed for Pass 2. It does not archive native tool payloads or operational logs.

Pass 1 must freeze:

- exact Brief and audit-profile identity;
- exact baseline request/attempt identities;
- exact baseline observation identities;
- the exact Pass-1 claim set;
- exact Challenge Target identities;
- the exact counter-evidence request set;
- one construct fingerprint.

The counter-evidence request set is committed **before** counter-evidence results are observed. Pass 2 cannot add a new request, change a query/locator, widen a request bound, add a Challenge Target or start another acquisition round.

Challenge Targets must be grounded in the Evidence Construct. CLAIM and ASSUMPTION targets reference exact Pass-1 claim fingerprints. SOURCE, FRESHNESS and INSTRUCTION_BOUNDARY targets reference exact baseline Source Observation fingerprints. COVERAGE, APPLICABILITY and GAP targets may use bounded descriptive target_ref values when no single claim/source identity is sufficient.

Every Challenge Target must be referenced by at least one precommitted COUNTER_EVIDENCE request, and the union of request challenge_target_fingerprints must equal the exact Pass-1 Challenge Target set.

At least one counter-evidence request is required. The number of requests must not exceed the Research Brief max_counter_evidence_requests bound. Each request remains subject to its RA-1 per-request target and acquisition bounds.

The RA-4 protocol adds a global bounded-corpus rule compatible with the RA-3 Research Record: the union of unique Source Observation identities admitted across baseline and counter-evidence attempts must not exceed 64. The protocol never silently truncates or selects around an over-bound corpus.

### 8.3 Counter-evidence acquisition boundary

The exactly-one counter-evidence acquisition **batch** is external to the RA-4 pure protocol implementation.

A future adapter layer may execute the exact precommitted COUNTER_EVIDENCE requests. RA-4 then accepts one exact same-request successful Acquisition Attempt for every request.

A counter-evidence success with zero observations is valid. The later Brain audit decides whether the absence of additional material is meaningful.

A failed, missing, foreign, duplicated or substituted attempt blocks Pass 2. It does not authorize a second counter-evidence batch.

### 8.4 Pass 2 — ADVERSARIAL_RESEARCH_AUDIT_AND_RECONCILE

Pass 2 consumes:

- the exact Evidence Construct;
- the exact successful counter-evidence attempts for every precommitted request;
- one Brain-authored audit result for each of the nine research audit lenses;
- explicit reconciliation of every Pass-1 claim;
- one caller-supplied final Research Record material set constructed from only the exact baseline/counter observations admitted by this protocol.

Every audit lens appears exactly once and receives a Brain semantic disposition:

- CLEAR;
- LIMITATION;
- BLOCKING.

Each lens result contains bounded rationale and exact references to relevant claim, source and Challenge Target fingerprints. Deterministic code validates grammar, bindings and complete nine-lens coverage only; it never decides which disposition is substantively correct.

Every Pass-1 Challenge Target must be referenced by at least one Pass-2 lens result. Counter-evidence observations may support, contradict, limit or add context; duplication with baseline observations does not become independent corroboration merely because it came from the counter batch.

Every Pass-1 claim receives exactly one reconciliation disposition:

- RETAINED — the final claim fingerprint is unchanged;
- REVISED — the final claim has a new fingerprint;
- REMOVED — the Pass-1 claim does not appear in the final claim set.

Every final claim must be accounted for either by a RETAINED/REVISED reconciliation entry or as an explicitly NEW claim. No Pass-1 claim or final claim may disappear from reconciliation coverage.

Pass 2 may construct one final immutable AIOS_RESEARCH_RECORD v1 by reusing the RA-3 contract. Its Brief/profile identity must match the Evidence Construct. Its source registry may reference only Source Observations present in the exact admitted baseline/counter attempts. If a predecessor Research Record is supplied, existing RA-3 predecessor/refresh rules still apply unchanged.

### 8.5 Final closure

The only semantic closure outcomes remain:

- RESEARCH_CANDIDATE;
- INSUFFICIENT_EVIDENCE.

The outcome is Brain-owned semantic material, but RA-4 enforces one structural closure invariant over the Brain's own audit dispositions:

- RESEARCH_CANDIDATE is valid only when no audit lens is BLOCKING;
- INSUFFICIENT_EVIDENCE requires at least one audit lens marked BLOCKING.

LIMITATION does not automatically block a candidate; the limitation and uncertainty remain explicit in the final Research Record.

This rule does not make deterministic code a research judge. Deterministic code checks that the Brain's declared closure is internally consistent with the Brain's declared audit blockers.

### 8.6 Anti-recursion and authority boundary

RA-4 ends after Pass 2.

There is no Pass 3, recursive challenge generation, automatic request refinement, automatic retry/fallback, model voting, confidence scoring, automatic architecture decision, roadmap advancement or TASK creation.

If Pass 2 exposes a material unresolved issue that would require new acquisition, the current protocol closes as INSUFFICIENT_EVIDENCE. A later Human/Brain decision may start a **new Research Brief/lineage**; it does not mutate or recursively continue the closed two-pass protocol.

A successful Research Record is bounded planning evidence only. It may be handed to a fresh ARCHITECTURE flow, which independently decides project meaning and then uses the existing BP-4A audit where applicable.

## 9. Research Audit Profile v1

RA-3 freezes one dedicated repository-owned research audit profile, separate from the existing BP-4A Brain Semantic Audit profile.

The research profile is content-addressed procedural policy for research semantics only. It is not added to the existing Flow Resolver, does not modify .ai/brain-audit-profiles.yaml, and does not imply that first-class RESEARCH Flow integration already exists.

The fixed ordered lenses are:

1. SOURCE_AUTHORITY_PROVENANCE — verify source class, provenance and whether a source actually has authority for the claim being made.
2. SOURCE_FRESHNESS_VERSION — verify version, date, commit/tag/API generation and current-as-of applicability.
3. CLAIM_EVIDENCE_BINDING — require each material claim to bind to appropriate evidence rather than unsupported synthesis.
4. COVERAGE_INDEPENDENCE — detect duplicated reporting or sources derived from the same underlying source.
5. CONTRADICTION_COUNTEREVIDENCE — test for disconfirming, limiting, exception, failure-mode or materially conflicting evidence.
6. PROJECT_APPLICABILITY_NOVELTY — test project applicability and whether equivalent capability already exists.
7. ASSUMPTION_UNCERTAINTY — keep observations, assumptions, inference, unknowns and uncertainty boundaries explicit.
8. UNTRUSTED_CONTENT_INSTRUCTION_ISOLATION — verify retrieved content remains data-only regardless of source authority.
9. AUTHORITY_HANDOFF_BOUNDARY — ensure research remains evidence for Brain reasoning rather than roadmap, TASK, Runtime, Executor, Reviewer, Publisher or policy authority.

RA-3 freezes profile identity, lens order, lens text bounds and deterministic profile digest only. The Pass-1 / counter-evidence / Pass-2 procedure, audit-output grammar and final closure semantics remain RA-4. This avoids prematurely encoding the wrong protocol into the profile registry.

## 10. Provider and tool portability

Provider conformance does not require different models to reach identical semantic conclusions.

It requires stable protocol behavior around:

- Research Brief identity;
- source/provenance grammar;
- claim/source binding;
- resource bounds;
- audit-lens coverage;
- uncertainty and closure grammar;
- invalidation semantics;
- handoff authority.

Semantic Brain providers and source-acquisition adapters are separate concerns. A Brain provider need not have direct GitHub/web access if the complete bounded research material can be supplied. An acquisition adapter does not become Brain merely because it can search or fetch. Acquisition adapters accept bounded Acquisition Requests and return bounded Source Observations plus operational attribution; they do not independently decide what else should be researched.

No model scoring, voting, automatic provider choice, retry, fallback or failover authority is introduced.

## 11. Integration rule

The first-class `RESEARCH` Flow must not be added to the existing closed Flow Resolver/Flow Card/Decision Packet/Brain Provider stack until the research contract, acquisition boundary, Research Record semantics and two-pass protocol are sufficiently defined.

This ordering prevents core AIOS integration from freezing the wrong research semantics.

When integration occurs, RESEARCH remains Brain-owned cognitive support and must preserve existing flow/lifecycle authority separation.

RESEARCH should be an **explicit Brain-owned flow only**; it must not be auto-selected from Unified State or become an engineering lifecycle obligation. Its research semantics should use a dedicated bounded Research Packet/Research Brief family rather than overloading `AIOS_DECISION_PACKET` with source-acquisition state. Existing Decision Packet semantics remain for architecture/TASK/correction reasoning.

## 12. Reuse and invalidation

The default rule remains: reuse exact unchanged claims until relevant declared basis requires refresh.

Claim-scoped invalidation basis uses bounded portable identity records compatible with the RA-1 basis model: a kind, locator and exact expected identity. Typical bases may represent a source observation/content identity, a relevant project component/blob identity, a specification/version identity, or another explicitly named dependency.

Deterministic support may compare caller-supplied current basis against those exact declared identities and identify the affected claim fingerprints. It must fail closed when required current basis is missing. It must not search the repository, resolve latest, fetch a URL, inspect a connector, infer source freshness, or decide that a contradiction is semantically decisive.

Possible semantic reasons for Brain to author or refresh invalidation basis include source version/content change, relevant project component/content fingerprint change, currentness boundary change, claim/source provenance break, or newly discovered material contradiction.

A changed Research Brief or changed research-audit profile starts a fresh record lineage. It is not represented as a partial refresh of a different semantic subject.

## 13. Handoff

A successful Research Record may feed a fresh ARCHITECTURE flow.

The architecture decision must independently decide what the research means for AIOS and then pass through the existing high-value Brain Semantic Audit where applicable.

The separation is:

```text
Knowledge acquisition
!= Architecture decision
!= TASK authoring
!= Implementation
```

Research cannot automatically create or select a TASK.

## 14. Roadmap v2

### RA-0 — Research Architecture & Threat-Model Audit

Status: DONE — HUMAN/BRAIN PLANNING AUDIT.

The read-only RA-0 audit confirmed the RA v2 direction and milestone sequence, with mandatory v2.1 amendments covering:

- untrusted-source instruction isolation;
- explicit Brain-owned bounded Acquisition Requests;
- operational acquisition failure separated from semantic `INSUFFICIENT_EVIDENCE`;
- immutable Research Record/predecessor refresh lineage;
- mutable-source observation identity beyond URL;
- claim/component-scoped invalidation;
- deterministic structural validation without semantic truth checking;
- explicit-only future RESEARCH flow using dedicated research semantics;
- Brain-owned research closure without a new Research Reviewer.

RA-0 found one existing planning-surface mismatch: current deterministic Brain Sync interprets a non-TASK `NEXT` as `UNAUTHORED_TASK`. RA must not widen Brain Sync into a generic planning router merely to represent Brain-only milestones. After RA-0 closure, Human/Brain may author the exact RA-1 engineering TASK under explicit authority and then bind roadmap NEXT to that exact task.

No production implementation was performed by RA-0 itself.

### RA-1 — Research Contract Foundation

Define the bounded Research Brief and its identity, scope/non-goals, project/currentness basis, source policy, resource bounds, handoff and invalidation contract, plus the bounded Acquisition Request and Pass-1 challenge-target contract.

Do not add the first-class RESEARCH Flow, acquisition adapters, Research Record, provider invocation, or two-pass orchestration to core AIOS yet.

### RA-2 — Source Acquisition & Provenance Boundary

Architecture audit: DONE — PASS WITH BOUNDARY REFINEMENT.

RA-2 defines a pure normalized acquisition airlock, not concrete source-tool invocation. It must freeze content-addressed Source Observation and Acquisition Attempt contracts, exact Brief/Request binding, representation-class distinction, provenance-safe source identity/version/retrieval facts, explicit PUBLIC vs AUTHORIZED_PRIVATE access scope, `instruction_trust: UNTRUSTED`, bounded content identity, request-budget enforcement, success-with-zero-observation semantics, and a closed operational failure taxonomy.

RA-2 must not perform source I/O; implement concrete web/GitHub/connector clients; infer source authority or independence; summarize source material; admit tool-generated synthesis as source evidence; retain credential-bearing transport material; establish connector authorization; perform hidden retry/fallback/pagination/query expansion; create Research Record persistence; or add RESEARCH to the core Flow Resolver.

Concrete replaceable adapter invocation and tool-specific access/safety enforcement belong to RA-5 after RA-3/RA-4 semantics are stable.

### RA-3 — Research Evidence Record & Audit Profile

Architecture audit: DONE — PASS WITH MANDATORY BOUNDARY REFINEMENTS.

RA-3 freezes a pure immutable/content-addressed Research Record contract, exact predecessor refresh lineage, claim/source-binding and uncertainty grammar, source authority/independence/freshness assessment grammar, project applicability/novelty assessment, claim-scoped invalidation basis and a transient deterministic reuse projection.

RA-3 also freezes a separate content-addressed research-high-value-v1 audit-profile registry with the nine ordered research lenses. The registry contains profile/lens identity only; the actual two-pass research procedure and closure-output protocol remain RA-4.

RA-3 must not persist records, auto-resolve a latest record, mutate record validity, perform source/repository/clock discovery, retain private source excerpts, infer source authority/freshness/independence, run the two-pass audit, create research conclusions, add a RESEARCH Flow, or create Runtime/Reviewer/Publisher authority.

### RA-4 — Two-Pass Research Assurance Protocol

Architecture audit: DONE — PASS WITH MANDATORY PROTOCOL REFINEMENTS.

RA-4 freezes a pure two-stage protocol over exact RA-1/RA-2/RA-3 identities. Pass 1 creates one content-addressed Evidence Construct and precommits the exact Challenge Target plus counter-evidence request set. One external bounded counter-evidence acquisition batch may then occur. Pass 2 accepts only exact successful attempts for those precommitted requests, requires complete nine-lens Brain audit coverage, exact Pass-1/final-claim reconciliation and produces one final Research Record with RESEARCH_CANDIDATE or INSUFFICIENT_EVIDENCE closure.

Operational acquisition failure blocks semantic advancement and never becomes a semantic outcome. RESEARCH_CANDIDATE requires no Brain-declared BLOCKING audit lens; INSUFFICIENT_EVIDENCE requires at least one Brain-declared BLOCKING lens. Deterministic code checks internal consistency and exact identity/budget/coverage bindings only.

RA-4 must not invoke source tools/providers/models, create retries/fallback, start a second counter-evidence batch, persist protocol state, infer semantic audit dispositions, add RESEARCH to Flow Resolver, make architecture/TASK decisions, or create lifecycle/Reviewer/Publisher authority.

### RA-5 — Provider & Tool Portability Contracts

Define provider-neutral Brain research request/return contracts and the concrete replaceable acquisition-adapter invocation boundary over RA-2's normalized airlock while preserving semantic/acquisition authority separation. Tool-specific invocation, effective-destination validation, operational attribution and already-authorized connector access are enforced here. No automatic provider/tool router, voting, scoring, retry, pagination, query expansion, fallback or failover.

### RA-6 — AIOS Integration & Continuity Conformance

Only after RA-1 through RA-5 semantics are stable, integrate an explicit-only first-class RESEARCH Flow with Flow Cards/context/dedicated Research Packet/provider surfaces and prove fresh-context, cross-provider, source-substitution, invalidation and Research Record reuse behavior.

Conformance is protocol/authority conformance, not identical semantic conclusions between providers.

### RA-7 — Real Project Proof & Closure

Run controlled real-project proof covering both:

- stable/immutable-source research;
- mutable/time-sensitive research; and
- adversarial/prompt-injection-bearing source content proving instruction isolation.

Prove valid-record reuse, relevant invalidation detection, bounded refresh with unaffected evidence reuse, audited handoff into fresh ARCHITECTURE reasoning, and no automatic roadmap/TASK progression.

RA closes only after Reviewer-quality semantic assessment confirms the proof preserves the intended authority boundaries and continuity properties.

## 15. Non-goals

RA v2.4 does not:

- replace Brain Sync;
- replace BP-4A Brain Semantic Audit;
- make research claims canonical engineering truth;
- create a generic web crawler or autonomous research agent;
- create persistent conversational memory;
- create a knowledge graph with independent authority;
- create a new lifecycle;
- create automatic TASK selection/authoring;
- create a general provider/tool router;
- require research providers to agree on one semantic answer;
- reopen completed Brain Portability engineering lineage.

## 16. Activation and sequencing

Brain Portability BP-9 downstream adoption is complete based on downstream exact-pin migration and fresh current-pin conformance publication evidence.

RA v2.4 is the active Human/Brain planning baseline after RA-0, RA-2, RA-3 and RA-4 architecture audits.

RA-1 through RA-3 are reviewed/published. RA-4 architecture semantics are now audited but remain non-executable until one exact canonical RA-4 TASK is authored and bound by Human/Brain planning authority. RA-5 through RA-7 remain gated.

A newer explicit Human priority may prospectively supersede this track, but future generic continuation may rely on that change only after it is canonicalized.
