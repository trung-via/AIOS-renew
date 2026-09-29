# AIOS Research Assurance Architecture and Roadmap v2

Status: HUMAN-APPROVED PLANNING BASELINE  
Approved by Human: 2026-09-29  
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
- bounded content identity or excerpt identity;
- relationship to other sources where independence matters.

Acquisition must not produce architecture decisions, roadmap priority, TASK semantics, review verdicts, or lifecycle state.

The architecture must support replaceable acquisition mechanisms without making one provider's native web/GitHub/search ability a hidden prerequisite.

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

Final outcomes are:

```text
RESEARCH_CANDIDATE
INSUFFICIENT_EVIDENCE
```

## 9. Research Audit Profile v2 lenses

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

8. **AUTHORITY_HANDOFF_BOUNDARY**  
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

Semantic Brain providers and source-acquisition adapters are separate concerns. A Brain provider need not have direct GitHub/web access if the complete bounded research material can be supplied. An acquisition adapter does not become Brain merely because it can search or fetch.

No model scoring, voting, automatic provider choice, retry, fallback or failover authority is introduced.

## 11. Integration rule

The first-class `RESEARCH` Flow must not be added to the existing closed Flow Resolver/Flow Card/Decision Packet/Brain Provider stack until the research contract, acquisition boundary, Research Record semantics and two-pass protocol are sufficiently defined.

This ordering prevents core AIOS integration from freezing the wrong research semantics.

When integration occurs, RESEARCH remains Brain-owned cognitive support and must preserve existing flow/lifecycle authority separation.

## 12. Reuse and invalidation

The default rule is:

```text
reuse valid research until relevant state invalidates it
```

Possible invalidators include:

- source version/content change;
- relevant project main/component change;
- current-as-of boundary expiry;
- claim/source provenance break;
- Research Brief semantic change;
- newly discovered material contradiction.

Invalidation must be claim- or record-scoped where possible. It must not force full re-research when unchanged evidence remains valid.

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

Status at activation: NEXT.

Read-only architecture audit before implementation. Confirm the minimum authority-safe design, threat/failure model, source taxonomy, acquisition boundary, evidence/claim model, freshness/invalidation semantics, reuse rules, and exact interfaces with existing AIOS primitives.

No production implementation TASK is authorized merely by entering RA-0.

### RA-1 — Research Contract Foundation

Define the bounded Research Brief and its identity, scope/non-goals, project/currentness basis, source policy, resource bounds, handoff and invalidation contract.

Do not add the first-class RESEARCH Flow to core AIOS yet.

### RA-2 — Source Acquisition & Provenance Boundary

Define replaceable subordinate acquisition adapters and normalized source-observation/provenance material without semantic decision authority, roadmap authority, or lifecycle authority.

### RA-3 — Research Evidence Record & Audit Profile

Define the durable bounded Research Record, claim/source/independence/freshness/uncertainty grammar, content identity and invalidation semantics, and freeze the research-specific audit profile/lenses.

### RA-4 — Two-Pass Research Assurance Protocol

Implement Evidence Construct followed by exactly one bounded counter-evidence acquisition and adversarial audit/reconciliation/closure pass. Prohibit recursive research loops. Yield RESEARCH_CANDIDATE or INSUFFICIENT_EVIDENCE.

### RA-5 — Provider & Tool Portability Contracts

Define provider-neutral Brain research request/return contracts and replaceable acquisition-adapter contracts while preserving their authority separation. No automatic provider/tool router, voting, scoring, retry, fallback or failover.

### RA-6 — AIOS Integration & Continuity Conformance

Only after RA-1 through RA-5 semantics are stable, integrate the first-class RESEARCH Flow with Flow Cards/context/packet/provider surfaces and prove fresh-context, cross-provider, source-substitution, invalidation and Research Record reuse behavior.

Conformance is protocol/authority conformance, not identical semantic conclusions between providers.

### RA-7 — Real Project Proof & Closure

Run controlled real-project proof covering both:

- stable/immutable-source research; and
- mutable/time-sensitive research.

Prove valid-record reuse, relevant invalidation detection, bounded refresh with unaffected evidence reuse, audited handoff into fresh ARCHITECTURE reasoning, and no automatic roadmap/TASK progression.

RA closes only after Reviewer-quality semantic assessment confirms the proof preserves the intended authority boundaries and continuity properties.

## 15. Non-goals

RA v2 does not:

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

RA v2 becomes the active Human/Brain planning track only through explicit roadmap canonicalization.

The unique generic continuation after activation is RA-0.

RA-1 through RA-7 remain PLANNED and do not become executable merely because this planning baseline exists. RA-0 must first audit and, if necessary, refine this baseline before implementation TASK authoring begins.

A newer explicit Human priority may prospectively supersede this track, but future generic continuation may rely on that change only after it is canonicalized.
