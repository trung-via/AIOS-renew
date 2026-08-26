# AIOS-renew Kernel v0.1 Specification

**Status:** FROZEN
**Freeze date:** 2026-08-26
**Version:** 0.1  
**Purpose:** Define the minimum canonical protocol for AI-assisted software execution using ChatGPT as Brain/Reviewer and Codex or Google Antigravity as the single active Executor.

The v0.1 architecture is frozen. Future kernel features require separate tasks and observed evidence. This freeze does not mean the entire AIOS product is finished; it means the minimum kernel contract is stable enough for real engineering use.

---

## 1. Purpose

AIOS-renew exists to convert Human intent into verified engineering outcomes with the least practical amount of:

- model calls;
- repeated reasoning;
- context duplication;
- execution time;
- human intervention;
- executor conflict.

The canonical optimization target is:

```text
Useful Work
──────────────
Time + Tokens + Human Effort
```

AIOS-renew is **not** designed to maximize agent count or autonomy. It is designed to coordinate a small number of clearly separated roles efficiently.

---

## 2. Canonical Roles

### 2.1 Human

Owns intent, approvals where required, and final product direction.

### 2.2 ChatGPT Brain

Owns **WHAT** must be achieved.

Responsibilities:

- interpret Human intent;
- define the problem;
- define scope and constraints;
- define acceptance criteria;
- create the TASK contract.

The Brain must not prescribe implementation details unless they are themselves required architectural constraints.

### 2.3 Executor

Exactly one active executor is allowed for a task at a time:

- Codex; or
- Google Antigravity.

The Executor owns **HOW**.

Responsibilities:

- inspect only relevant repository context;
- choose implementation details;
- modify code;
- run required local verification;
- produce an immutable implementation result;
- report claims and evidence.

### 2.4 ChatGPT Reviewer

Owns semantic review of the implementation result.

Responsibilities:

- compare the implementation delta to the TASK contract;
- verify acceptance coverage;
- inspect evidence;
- detect contract violations and material delta-introduced defects;
- return PASS, CHANGES_REQUIRED, or BLOCKED.

The Reviewer does not implement code.

### 2.5 AIOS Runtime

The Runtime is deterministic coordination software, not another reasoning agent.

Responsibilities may include:

- assignment;
- one-active-executor enforcement;
- SHA binding;
- immutable state references;
- evidence capture;
- package validation;
- integration checks.

The Runtime must not duplicate Brain, Executor, or Reviewer reasoning.

---

## 3. Canonical Pipeline

```text
Human Intent
    ↓
ChatGPT Brain
    ↓
TASK
    ↓
AIOS Runtime
    ↓
ONE active Executor
(Codex OR Antigravity)
    ↓
Implementation + Required Verification
    ↓
RESULT + EVIDENCE
    ↓
ChatGPT Review
   /        \
PASS    CHANGES_REQUIRED
            ↓
       REMEDIATION
            ↓
       Delta Review
            ↓
           PASS
```

A task progresses forward. Review remediation must not restart the task from the beginning.

---

## 4. Core Contracts

### 4.1 TASK — Semantic Contract

TASK describes the required outcome, not the execution instance.

Minimum schema:

```yaml
task_id: TASK-042
revision: 1

goal: >
  Desired engineering outcome.

problem: >
  Problem or behavior being addressed.

assumptions:
  - Relevant Brain assumptions that the Executor may challenge if materially false.

scope:
  inspect:
    - relevant/path/**
  modify:
    - allowed/path/**

non_goals:
  - Explicitly excluded work.

constraints:
  hard:
    - Project invariants that must not be violated.

acceptance:
  - id: AC1
    condition: Observable definition of done.
  - id: AC2
    condition: Another observable requirement.

verification:
  required:
    - targeted verification requirement
```

TASK must not normally contain:

- executor identity;
- execution SHA;
- full repository context;
- chat history;
- raw logs;
- detailed implementation plan;
- model-specific instructions.

### 4.2 RUN — Operational Execution Record

RUN represents one operational execution instance.

Minimum data:

```yaml
run_id: RUN-042-001

task:
  id: TASK-042
  revision: 1

executor: codex
base_sha: <sha>
head_sha: null
workspace: <workspace-reference>
status: ACTIVE
```

RUN is operational metadata. It must reference TASK rather than copy its semantic content.

A new RUN may be created for genuine operational recovery or explicit handoff, but review fixes do not automatically restart a full RUN.

### 4.3 RESULT — Executor Summary

RESULT states what the Executor claims was achieved.

Example:

```yaml
head_sha: <sha>

claims:
  - id: C1
    satisfies:
      - AC1
    claim: Cancellation never retries.
    evidence:
      - E1

changed_files:
  - src/core/retry_policy.py

unresolved: []
```

RESULT is a concise summary. It must not duplicate raw evidence logs.

### 4.4 EVIDENCE — Proof

EVIDENCE contains deterministic proof or repository artifacts supporting claims.

Example:

```yaml
evidence_id: E1
run_id: RUN-042-001
subject_sha: <sha>
type: TEST

source:
  command: pytest tests/core/test_retry_policy.py

result:
  exit_code: 0
  summary: 24 passed

raw:
  path: .ai/evidence/E1.log
```

Evidence should be compact by default. Raw output is loaded only when needed.

### 4.5 REVIEW — Semantic Judgment

Primary review example:

```yaml
review_id: REVIEW-042-001
reviewed_sha: <sha>
mode: PRIMARY
verdict: CHANGES_REQUIRED

acceptance:
  AC1: FAIL
  AC2: PASS

findings:
  - id: R1
    basis: AC1
    action: CODE_FIX
    location: src/core/retry_policy.py
    issue: Cancellation can still reach retry scheduling.
    expected: No retry may be scheduled after cancellation.
```

Allowed verdicts:

```text
PASS
CHANGES_REQUIRED
BLOCKED
```

Blocking findings must be grounded in one of:

- TASK contract violation;
- material delta-introduced defect;
- evidence gap.

### 4.6 REMEDIATION — Narrow Follow-Up

Remediation addresses explicit findings only.

Two minimal types are allowed conceptually:

```text
CODE_FIX
EVIDENCE_ONLY
```

A remediation must not become a hidden second task.

---

## 5. Primary Execution Rules

The primary Executor may perform normal local implementation iteration:

```text
inspect
→ code
→ targeted test
→ fix local failure
→ targeted test
```

This is normal execution, not a review FIX cycle.

The Executor must not:

- rediscover the business problem already resolved by Brain;
- redesign the task without a material conflict;
- perform unrelated refactors;
- widen scope without a required supporting reason;
- create a competing canonical implementation.

---

## 6. Review and Remediation Rules

### 6.1 Primary Review

Primary Review evaluates:

```text
TASK
+
RESULT
+
implementation delta
+
evidence summaries
```

It does not audit the whole repository by default.

### 6.2 Narrow Remediation

When review returns a finding:

```text
R1
 ↓
Remediation for R1 only
 ↓
Affected verification only
 ↓
Delta Review
```

A remediation must not:

- rerun the original TASK as a fresh task;
- re-plan the whole implementation;
- rediscover the repository;
- fix unrelated old defects;
- add new user intent;
- automatically run the full original review again.

### 6.3 Delta Review

Delta Review asks:

1. Was the explicit prior finding resolved?
2. Did the remediation directly introduce a material new defect?

It does not re-review the entire original task unless the remediation invalidated prior review conclusions.

### 6.4 New Intent

A new requirement from the Human is not a FIX.

It requires either:

- a TASK revision; or
- a new TASK.

---

## 7. Verification Policy

Verification is progressive.

Default order:

```text
Targeted
   ↓
Affected-area regression when justified
   ↓
Full / canonical suite only when justified
```

Full canonical verification is not a ritual and is not required after every task or narrow remediation.

A verification command must not be repeated against unchanged relevant state without a new reason.

The same test execution must not be repeated merely so another layer can "verify" that the Executor already ran it. Runtime may capture the original execution as evidence.

Evidence remains reusable until the relevant state that supports it has been invalidated.

---

## 8. Executor Independence

Codex and Antigravity implement the same semantic TASK contract and return the same canonical output contract.

Their internal mechanisms may differ.

Principle:

> Same contract, native executor behavior, canonical boundaries.

Executor-specific adapters may:

- bind input;
- invoke the executor;
- capture output;
- normalize the result.

Adapters must not:

- reinterpret the TASK using another model;
- create an additional planning agent;
- perform semantic review;
- duplicate executor reasoning.

AIOS does not standardize the internal reasoning sequence of Codex or Antigravity.

---

## 9. Core Kernel Laws

### Law 1 — One Active Execution Authority

A task may have only one active canonical mutation authority at a time.

### Law 2 — Brain Owns WHAT; Executor Owns HOW

Semantic requirements belong to Brain. Implementation decisions belong to the Executor.

### Law 3 — Minimum Necessary Context

Each model receives only the context needed for its current responsibility.

### Law 4 — No Redundant Discovery

Do not repeat problem discovery already resolved upstream. Necessary local implementation reasoning remains allowed.

### Law 5 — Review the Delta

Review the TASK contract and implementation delta, not the repository by default.

### Law 6 — Claims Require Evidence

Meaningful completion claims require supporting evidence.

### Law 7 — Immutable State Binding

Execution and review must bind to explicit immutable repository state, normally Git SHA.

### Law 8 — Fix with Continuity, Not Rediscovery

Remediation continues from existing lineage and known findings.

### Law 9 — No Unbounded Loops

Repeated remediation must be bounded and escalated instead of consuming quota indefinitely.

### Law 10 — Never Repeat Unchanged Work Without New Information

A step must not be rerun against unchanged relevant state without a reason.

### Law 11 — Fix the Finding, Not the Task

A remediation addresses explicit findings and the minimum supporting changes required.

### Law 12 — Evidence Survives Until Invalidated

Evidence is not automatically discarded merely because another unrelated part of the task changed.

### Law 13 — New Intent Is Not a Fix

New requirements require a TASK revision or new TASK.

### Law 14 — Deterministic Coordination Before AI Reasoning

Use software for identity, state, SHA, scope, leases, evidence capture, and integration checks whenever deterministic logic is sufficient.

### Law 15 — Same Contract, Executor-Specific Native Adapter

Codex and Antigravity share the same semantic contract; executor-specific operational differences belong only in thin adapters.

---

## 10. Minimal Runtime Responsibilities

AIOS-renew v0.1 Runtime should remain thin.

Minimum responsibilities:

```text
TASK reference
↓
assignment
↓
one active executor
↓
base SHA binding
↓
result SHA capture
↓
evidence references
↓
review package validation
↓
integration safety check
```

The Runtime is not:

- a Planner Agent;
- a Verifier Agent;
- a second Reviewer;
- a multi-agent supervisor.

---

## 11. Explicit Non-Goals for Kernel v0.1

The following are deliberately excluded unless real measurements later prove they are necessary:

- H-Series as a mandatory kernel layer;
- AIOS Bridge monolithic migration;
- multi-agent voting;
- multiple reviewers;
- autonomous planner agent;
- automatic model router;
- model scoring;
- long-term learning engine;
- self-reflection loops;
- complex artifact graphs;
- dependency graph orchestration;
- orchestration DSL;
- Redis;
- message brokers;
- orchestration database;
- file-level distributed locking;
- sophisticated impact dependency engine;
- automatic retry;
- automatic reroute;
- token-budget machinery inside TASK;
- priority/severity/effort metadata unless later justified.

---

## 12. Known Risks / Future Considerations

The following findings came from architecture and legacy-system audit. They are **not v0.1 implementation requirements** unless separately promoted by evidence:

- Codex and Antigravity may have different native continuity/session behavior.
- Legacy mandatory full canonical test policy may be expensive for narrow changes.
- Executor-specific legacy instructions may create different behavior between executors.
- Evidence invalidation may eventually benefit from more automation.
- Executor conformance testing may later be useful for new executor backends.
- H-Series capabilities may later be reintroduced selectively if benchmarked benefit is demonstrated.
- Persistent executor continuity may later be useful if actual Codex remediation measurements justify it.

These items must not be promoted into kernel complexity without measured need.

---

## 13. Canonical Mental Model

The system should remain understandable as:

```text
ChatGPT
  BRAIN
    │
    ▼
  TASK
    │
    ▼
Codex OR Antigravity
  EXECUTOR
    │
    ▼
RESULT + EVIDENCE
    │
    ▼
ChatGPT
  REVIEW
   / \
PASS FIX
      │
      ▼
NARROW REMEDIATION
      │
      ▼
DELTA REVIEW
```

Everything else exists only to make this flow reliable without duplicating its reasoning.

---

## 14. v0.1 Freeze Criteria

Kernel v0.1 may be considered architecturally frozen when:

1. TASK contract is accepted.
2. Executor boundaries are accepted.
3. RESULT/EVIDENCE boundaries are accepted.
4. REVIEW/REMEDIATION boundaries are accepted.
5. Codex and Antigravity can both execute the same semantic TASK contract without requiring different TASK definitions.
6. No mandatory pipeline step performs redundant semantic reasoning.
7. No default remediation restarts the task from the beginning.
8. Full canonical verification is not required by default for every narrow change.

Implementation may then begin from this specification rather than from legacy AIOS behavior.

---

# Canonical Principle

> **AIOS-renew coordinates reasoning; it does not duplicate reasoning.**

> **Brain defines WHAT. One Executor implements HOW. Reviewer judges the DELTA. Deterministic software handles everything else it can.**
