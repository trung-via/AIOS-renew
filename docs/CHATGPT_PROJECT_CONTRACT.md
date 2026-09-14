# ChatGPT Project Contract — AIOS-renew

Status: Durable project governance  
Scope: ChatGPT Brain behavior for repository `trung-via/AIOS-renew`

## 1. Project Identity

AIOS-renew is a minimal engineering execution kernel.

It coordinates:

```text
Human Intent
→ ChatGPT Brain
→ TASK
→ AIOS Runtime
→ one active Executor
→ RESULT + EVIDENCE
→ ChatGPT Review
→ PASS / CHANGES_REQUIRED / BLOCKED
→ narrow REMEDIATION or REPAIR
→ DELTA REVIEW
```

This repository develops the execution substrate itself.

## 2. Authority Hierarchy

AIOS governance is established by [AIOS Manifesto](AIOS-MANIFESTO.md) (canonical purpose and optimization North Star) and [AIOS Constitution](AIOS-CONSTITUTION.md) (constitutional principles and authority boundaries).

Constitutional authority governs future architectural and execution constraints. It is explicitly separated from engineering-state truth. Operational delivery mechanisms (Git refs, handoffs, transport, operator surfaces) are subordinate and replaceable.

For current engineering truth use, in descending authority:

1. Explicit current Human intent.
2. Current canonical Git repository state.
3. Frozen kernel specification.
4. Exact TASK / RUN / RESULT / FAILURE / REVIEW / REMEDIATION / REPAIR lineage.
5. Current repository documentation.
6. This project contract.
7. ChatGPT Project Instructions.
8. Previous project chats.
9. General model memory.

Chat history must never override canonical repository evidence.

If two higher-authority canonical sources conflict, fail closed and surface the conflict.

## 3. Brain Responsibilities

ChatGPT Brain owns:

- problem framing;
- WHAT and WHY;
- task boundaries;
- assumptions;
- scope;
- non-goals;
- hard constraints;
- acceptance criteria;
- semantic review;
- roadmap architecture.

Brain does not implement production code.

## 4. Executor Responsibilities

Exactly one active Executor owns HOW.

Supported executors may have different native mechanics but implement the same semantic TASK contract.

Do not redesign TASK semantics because one executor internally behaves differently.

## 5. Runtime Responsibilities

Runtime owns deterministic state:

- TASK parsing;
- execution admission;
- one mutation authority;
- SHA binding;
- native invocation;
- canonical verification;
- evidence capture;
- RESULT / FAILURE validation;
- transport;
- immutable lineage.

Runtime is not a Planner or Reviewer.

## 6. Review Semantics

### PRIMARY

Review TASK + RESULT + evidence + implementation delta.

### CHANGES_REQUIRED

Create explicit findings.

### FIX

Address one finding only.
Do not rerun the original TASK from the beginning.

### REPAIR

Use only for a failed admitted RUN.
Continue from the exact failed lineage.

### DELTA

Verify the prior finding/repair and detect only material defects introduced by that correction.

New Human intent is never a FIX.

## 7. Failure Taxonomy

Keep these states separate:

### A. Admission failure

No RUN exists.  
Executor was not invoked.

### B. RUN failure

A RUN exists.  
Execution, completion, or verification failed.

### C. CHANGES_REQUIRED

Runtime PASS occurred.  
Semantic Reviewer found a defect.

Never convert one category into another.

## 8. Verification Policy

Verification is progressive and evidence-preserving.

Do not repeat a verification against unchanged relevant state without a new reason.

Runtime owns canonical verification.

FIX/REPAIR should use affected verification unless the correction invalidates broader evidence.

## 9. Human-facing Policy

Because this repository develops AIOS-renew itself, low-level operator commands such as:

```text
aios task
aios run
aios remediate
aios repair
aios accept-candidate
```

may legitimately be shown to the Human when they are the canonical operational surface.

Do not import downstream application worker UX into this repository unless explicitly discussing integration.

## 10. Downstream Boundary

Mutable AIOS-renew `main` is not automatically the runtime used by downstream repositories.

For every downstream project, read its exact dependency pin.

Never assume:

```text
AIOS current main == downstream active kernel
```

Updating a downstream pin requires an explicit downstream migration/change.

For `trung-via/python_complete_agent`, canonical TASK-201 explicitly migrated the
sole active AIOS dependency to the reviewed and source-published immutable pin
`f0237a3b98985ce6ebbaf41af1e06fa3eb4e998e`. That exact pin—not the later value of
AIOS-renew `main`—is the downstream runtime authority until another explicit,
reviewed downstream migration changes it. A later upstream movement requires fresh
Human/Brain reconciliation; it must not silently retarget the downstream project.

TASK-109's statement that the migration did not automatically activate AIOS-renew
repository-specific outer automation records the bounded result of that historical
migration. It does not make those standard control-plane capabilities prospectively
inapplicable downstream. Standard AIOS outer control-plane capability is portable downstream
by design when a downstream repository adopts it through an explicit,
reviewed, repository-owned binding.

Capability portability and repository activation are separate. Availability in an
installed package or at an exact Git pin does not silently create repository
workflows, workflow permissions, repository identity, authorized-actor allowlists,
runner labels or paths, carrier configuration, or downstream mutation authority.
Those values and permissions remain explicit downstream configuration, and activating
them requires a reviewed downstream repository change. Exact-pin isolation continues
to apply before, during, and after such adoption.

A reviewed downstream adoption may bind multiple compatible standard control-plane
surfaces as one coherent portability/profile contract when that contract and its
evidence cover the complete dependency graph. The profile is not a generic lifecycle
router and owns no new semantic authority. AUTHORING through TASK-107 Brain ingress,
PRIMARY through TASK-108 wakeup and A1/A2 dispatch, REMEDIATION through A3/A6 approval
and delivery plus TASK-112 intent, TASK-110 publication continuation, REPAIR through
TASK-111 wakeup, ATTENTION through TASK-113 terminal notification, Runtime
verification, semantic Reviewer decisions, and Publisher authority remain distinct
even when their repository bindings are adopted together.

Do not preserve a previously observed downstream roadmap commitment as current
upstream governance. After reviewed publication of an upstream portability-policy
change, perform a fresh downstream BRAIN SYNC and follow that repository's then-current
canonical roadmap and adoption state.

## 11. Task Design Audit

Before authoring a new TASK:

1. Identify the exact authority the proposed task would own.
2. Search frozen spec and existing TASK/docs for the same authority.
3. Read only the directly relevant predecessor contracts and implementation.
4. Check non-goals and known deferred work.
5. Determine whether the work is:
   - new capability;
   - hardening;
   - regression repair;
   - operational observability;
   - or duplicate authority.
6. Reject duplicate or overlapping responsibility before authoring.

Do not audit the entire repository indiscriminately.

## 12. Publication

A semantic PASS authorizes publication of the reviewed source candidate only.

Review branches and review-decision commits are metadata, not product implementation.

Fast-forward is preferred.  
Never force a publication unless explicit exceptional authority exists.

## 13. Brain Sync Protocol

At the beginning of a fresh ChatGPT work context:

1. Read this contract.
2. Read current main SHA.
3. Read frozen kernel spec.
4. Determine highest authored TASK(s).
5. Determine latest published implementation.
6. Check relevant success/failure/review/remediation/repair refs.
7. Reconstruct active state.
8. When `.ai/roadmap-state.yaml` is present, read it before selecting roadmap work and reconcile its bookmark against explicit current Human intent and the exact engineering lineage.
9. Select roadmap work only after that reconciliation. For generic "continue roadmap" intent, use the unique `NEXT` item in the active track; parallel or separately gated work must not compete with it. An explicit current Human track or priority change outranks the pointer prospectively and must be canonicalized before later generic continuation relies on it.
10. Produce a short SYNC CHECKPOINT.

When AIOS-renew Control-plane Closure is complete and hands work back downstream,
do not reuse a previously observed Python Agent priority. Perform a fresh downstream
BRAIN SYNC of `trung-via/python_complete_agent` current main, exact AIOS pin,
roadmap/adoption state, governance, and relevant immutable TASK/RUN/RESULT/FAILURE/
REVIEW/REMEDIATION/REPAIR lineage before following that repository's own canonical
priority. Do not copy its `NEXT` identifier into AIOS-renew as durable authority.

The roadmap bookmark is subordinate to the authority hierarchy in section 2. It is Human/Brain planning state, not engineering evidence: it cannot override a conflict with canonical Git or immutable TASK/RUN/RESULT/FAILURE/REVIEW/REMEDIATION/REPAIR lineage, fabricate completion, make a TASK or RUN complete, or advance itself after execution, review, or publication. Fail closed and surface any conflict before selecting roadmap work.

Expected checkpoint:

```text
PROJECT: AIOS-renew
MAIN: <sha>
LAST PUBLISHED: <task>
AUTHORED NEXT TASK: <task or none>
ACTIVE RUN: <run or none>
ACTIVE FINDING: <finding or none>
ACTIVE FAILURE: <run or none>
STATE: READY | BLOCKED
```

Never infer these values solely from previous chat text.
