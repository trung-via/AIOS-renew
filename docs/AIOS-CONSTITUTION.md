# AIOS Constitution

Status: Canonical Governance Baseline v1.0  
Scope: Constitutional Principles, Authority Hierarchy, and Boundary Constraints

This document establishes the constitutional foundation of AIOS. It defines the core principles governing authority, state, and execution within the system.

---

## 1. Constitutional Principles

### 1. Human Intent, Evidence Truth
"Human owns intent, priorities, risk acceptance and constitutional change. Canonical engineering truth must be established by state and evidence, not assertion."

### 2. Separation of Authority
"Semantic, implementation, execution, review and policy authorities must remain explicitly separated."

### 3. Bounded Mutation Authority
"Every canonical mutation domain has exactly one active authority at a time."

### 4. Sufficient Context, Reuse Until Invalidated
"Provide sufficient bounded context, and never pay twice for valid knowledge, work or evidence without relevant new information."

### 5. Probabilistic Intelligence, Deterministic Enforcement
"AI may reason, recommend and optimize; canonical authority, identity, scope, permissions, state and safety gates are enforced deterministically whenever possible."

### 6. Claims Require Evidence
"Every material completion claim requires appropriately typed evidence bound to the state it supports; uncertainty must remain explicit."

### 7. Reproducible Canonical State
"Execution, verification and review decisions must bind to enough immutable state to reproduce or reliably explain the outcome."

### 8. Correction with Continuity; New Intent with New Contract
"Corrections preserve lineage and address only explicitly authorized findings or causal finding groups. New intent requires a revised or new semantic contract."

### 9. Observable, Attributable, Replayable Automation
"Every automation must be observable, attributable, and replayable."

### 10. Bounded Autonomy, Maximum Useful Work
"Autonomy, loops, permissions, time, tokens and resource consumption are bounded and measured. Optimization serves verified useful work, not agent activity."

---

## 2. Constitutional Authority and Amendment

Explicit Human constitutional change is the only authority that may change constitutional principles.

No Brain, Executor, Reviewer, Runtime, Router, transport, or model may silently expand its own authority or reinterpret constitutional boundaries into new authority.

### 2.1. Supreme Human exception and recovery authority (Human decision, 2026-10-27)

Human is the final authority over project priorities, risk acceptance, constitutional amendments, and **explicit exceptional execution or publication decisions**. An ordinary deterministic Runtime admission, verification, or publication-eligibility gate is not a constitutional veto over an authenticated and exact-scoped Human exception.

For an exceptional recovery or deployment, Human may explicitly authorize an identified immutable source candidate to proceed despite specified unavailable or failed ordinary Runtime gates. The exceptional path must remain callable without invoking the malfunctioning Runtime or native Executor component that is itself being repaired. This power belongs to Human, not to an Executor, issue carrier, retry loop, model, or automatic policy.

An exceptional decision must bind the exact repository, source commit/tree, intended target, purpose, bypassed ordinary gates, and accepted uncertainty. Git ref compare-and-swap or equivalent exact-source binding protects against substitution. Human may revoke or supersede a *prospective* authorization; immutable historical facts are not rewritten. No standing open-ended override, self-authorization, hidden fallback, or automatic bypass is created.

**Decision authority is distinct from evidentiary truth.** An exceptional deployment must be explicitly identified as `HUMAN_OVERRIDE` / `NOT_RUNTIME_VERIFIED` when applicable. It must not be represented as a Runtime-created RUN, verification PASS, Reviewer PASS, ordinary Publisher eligibility, or proven engineering success if those events have not happened. Human can decide to accept that risk and deploy; no component may convert the decision into false technical evidence.

This provision is prospective. It changes governance priority for later exceptional actions without altering historical TASK/RUN/RESULT/FAILURE/REVIEW/REPAIR/REMEDIATION artifacts or making a frozen Kernel version retroactively claim a feature it did not implement. For ordinary work, original Runtime verification, independent Reviewer and Publisher remain the normal evidence and publication route.

---

## 3. Constitutional Authority vs. Engineering Truth

Constitutional authority must remain explicitly distinguished from engineering-state truth:

- **Constitutional Authority**: Governs future authority, architectural constraints, and boundary rules across AIOS.
- **Manifesto Intent**: Provides guiding purpose and the optimization North Star (`Verified Useful Work / (Time + Tokens + Human Effort)`), but is not execution-state truth, completion evidence, or an artifact that can override exact canonical lineage.
- **Engineering-State Truth**: Established strictly by current canonical Git state, frozen specifications, and exact immutable TASK / RUN / RESULT / FAILURE / REVIEW / REMEDIATION / REPAIR lineage within their applicable scopes.

This Constitution governs future authority and architectural constraints prospectively; it does not retroactively change frozen Kernel v0.1 semantics, historical artifacts, or already-established engineering facts. Ordinary future constitutional/kernel changes use explicit Human authority and a separate TASK. An exact authenticated Human exception under section 2.1 may instead authorize a bounded extraordinary recovery or deployment directly; that decision cannot masquerade as an ordinary Kernel RUN, verification, review or publication.

---

## 4. Subordinate and Replaceable Delivery Mechanisms

Git branches, refs, transport artifacts, handoff files, GitHub, operator surfaces, and similar delivery mechanisms are subordinate, replaceable implementation/transport mechanisms.

None of these operational mechanisms becomes constitutional authority merely because current AIOS uses it. They exist to serve canonical execution, verification, and review state, and may be evolved, refactored, or replaced under proper authority without altering constitutional principles.
