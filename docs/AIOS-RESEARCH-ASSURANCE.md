# AIOS Research Assurance Architecture and Roadmap v2.2

Status: HUMAN-APPROVED PLANNING BASELINE — RA-0 / RA-2 ARCHITECTURE AUDITED  
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

A durable Research Record exists so a fresh Brain context does not need chat memory or repeated research to recover still-valid knowledge.

The record must remain bounded and content-addressable and should represent:

- exact Research Brief identity;
- project/source snapshot basis;
- source registry;
- material claims;
- exact claim-to-source bindings;
- source authority and independence classification;
- uncertainty/assumption state;
- contradiction/counter-evidence findings;
- project applicability and novelty assessment;
- final closure outcome;
- freshness and invalidation rules;
- record fingerprint/content identity.

Permitted lifecycle-like validity labels are planning-evidence validity only, for example:

```text
VALID
REFRESH_REQUIRED
```

They are not Runtime lifecycle states.

Relevant source or project changes may invalidate only affected claims. Unaffected claims and source observations should be reused until their own basis changes.

Research Record identity must be immutable/content-addressed. A refresh creates a new record bound to the exact predecessor record and exact invalidated claim/source basis; it does not rewrite historical research. There is no automatic "latest research" resolver with semantic authority. A fresh Brain context receives an exact Research Record reference or explicitly starts a fresh Research Brief.

## 8. Two-pass Research Assurance protocol

RA deliberately does not copy BP-4A one-for-one.

BP-4A audits semantic reasoning over an already-composed Decision Packet. Research may discover during adversarial audit that additional counter-evidence is required. RA therefore permits exactly one bounded counter-evidence acquisition pass between construct and final reconciliation.

### Pass 1 — EVIDENCE_CONSTRUCT

Pass 1:

- acquires the bounded baseline corpus;
- constructs material claims;
- binds claims to sources;
- records uncertainty and assumptions;
- identifies gaps and challenge targets;
- does not claim closure merely because supporting evidence was found.

### Pass 2 — ADVERSARIAL_RESEARCH_AUDIT_AND_RECONCILE

Pass 2:

- challenges source authority and currentness;
- checks claim/source binding;
- tests source independence;
- seeks bounded contradictory or limiting evidence;
- tests project applicability and duplication;
- reconciles the exact Pass-1 claim set;
- performs final closure over the reconciled result.

Only one bounded counter-evidence acquisition pass is authorized by the base protocol. No recursive "research until satisfied" loop is created.

Final semantic outcomes are:

```text
RESEARCH_CANDIDATE
INSUFFICIENT_EVIDENCE
```

These outcomes exist only after valid acquisition and the required audit procedure. Acquisition/transport/protocol failures remain separate operational outcomes and do not imply either semantic result.

## 9. Research Audit Profile v2.1 lenses

The planned research-specific lenses are:

1. **SOURCE_AUTHORITY_PROVENANCE**  
   Verify source class, provenance and whether a source actually has authority for the claim being made.

2. **SOURCE_FRESHNESS_VERSION**  
   Verify version, date, commit/tag/API generation and current-as-of applicability.

3. **CLAIM_EVIDENCE_BINDING**  
   Require each material claim to bind to appropriate evidence rather than unsupported synthesis.

4. **COVERAGE_INDEPENDENCE**  
   Detect duplicated reporting or multiple sources that derive from the same underlying source and therefore do not provide independent corroboration.

5. **CONTRADICTION_COUNTEREVIDENCE**  
   Actively test the construct for disconfirming, limiting, exception, failure-mode, or materially conflicting evidence.

6. **PROJECT_APPLICABILITY_NOVELTY**  
   Determine whether an external capability meaningfully applies to AIOS, whether an equivalent capability already exists, and whether the proper disposition is reuse, adapt, reject, or unresolved.

7. **ASSUMPTION_UNCERTAINTY**  
   Keep observations, assumptions, inference, unknowns and uncertainty boundaries explicit.

8. **UNTRUSTED_CONTENT_INSTRUCTION_ISOLATION**  
   Verify that all retrieved content remains data-only regardless of source authority, and that embedded prompt/instruction/tool/control text cannot alter Brain, provider, adapter, roadmap, TASK, Runtime, Reviewer, Publisher, or policy authority.

9. **AUTHORITY_HANDOFF_BOUNDARY**  
   Ensure research supplies evidence to Brain reasoning without becoming roadmap, TASK, Runtime, Executor, Reviewer, Publisher, or policy authority.

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

The default rule is:

```text
reuse valid research until relevant state invalidates it
```

Possible invalidators include:

- source version/content change;
- relevant project component/content fingerprint change;
- current-as-of boundary expiry;
- claim/source provenance break;
- Research Brief semantic change;
- newly discovered material contradiction.

Invalidation must be claim- or record-scoped where possible. A movement of repository `main` alone is not sufficient to invalidate unrelated claims. Claims must bind to the narrow relevant source/project component fingerprints when deterministically available. It must not force full re-research when unchanged evidence remains valid.

Deterministic software may validate schema, identity, digests, structural bounds, provenance presence, predecessor continuity and declared invalidation relationships. It must not decide whether a material research claim is substantively true or whether conflicting evidence is semantically decisive; those remain Brain judgments.

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

Define the immutable/content-addressed bounded Research Record, exact predecessor refresh lineage, claim/source/independence/freshness/uncertainty grammar, component-scoped invalidation semantics, and freeze the nine-lens research-specific audit profile.

### RA-4 — Two-Pass Research Assurance Protocol

Implement Evidence Construct followed by exactly one bounded counter-evidence acquisition and adversarial audit/reconciliation/closure pass. Prohibit recursive research loops. Yield RESEARCH_CANDIDATE or INSUFFICIENT_EVIDENCE.

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

RA v2.2 does not:

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

RA v2.2 is the active Human/Brain planning baseline after RA-0 and RA-2 architecture audits.

RA-1 through RA-7 remain gated milestones and do not become executable merely because this planning baseline exists. The immediate post-RA-0 action is to author the exact RA-1 Research Contract Foundation TASK under explicit Human/Brain authority, then bind canonical roadmap NEXT to that exact authored TASK before execution.

A newer explicit Human priority may prospectively supersede this track, but future generic continuation may rely on that change only after it is canonicalized.
