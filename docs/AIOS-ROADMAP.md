# AIOS-renew Roadmap

Status: ACTIVE ROADMAP  
Canonical direction approved by Human: 2026-09-04

AIOS-renew optimizes for:

```text
Verified Useful Work / (Time + Tokens + Human Effort)
```

This roadmap is subordinate to the [AIOS Manifesto](AIOS-MANIFESTO.md), [AIOS Constitution](AIOS-CONSTITUTION.md), frozen Kernel specification, and exact canonical engineering lineage. It records current direction; it does not retroactively alter frozen contracts or historical TASK/RUN facts.

## Current Direction

AIOS-renew must remain a small governed engineering execution kernel. It owns canonical contract, authority, deterministic verification, evidence, lineage, and review/publication boundaries. Native coding-agent harness capabilities belong to the selected Executor unless AIOS must retain them to preserve an executor-independent trust or authority boundary.

Do not build AIOS-native replacements for capabilities already provided appropriately by Codex or Antigravity, including generic worktree management, subagent orchestration, skills, hooks, MCP, sandbox engines, browser agents, agent planners, model routers, or agent-swarm infrastructure.

## K0 — Lean Kernel

### K0.0 — Governance Baseline — DONE

Canonicalized by TASK-050:

- AIOS Manifesto v1.0;
- AIOS Constitution v1.0;
- constitutional preflight for Brain TASK authoring;
- governance/engineering-truth separation.

### K0.1 — Constitutional Responsibility Audit — DONE AS ARCHITECTURAL INPUT

The current responsibility model is:

| Concern | Authority / Owner |
| --- | --- |
| Human intent, priorities, risk acceptance | Human |
| WHAT / WHY / TASK contract | Brain |
| Admission, mutation authority, canonical execution state | AIOS Kernel / Runtime |
| HOW / implementation | Selected Executor |
| Native worktrees, subagents, tools, browser, skills, hooks, MCP | Selected Executor |
| Native sandbox / permission mechanics | Executor adapter / native harness |
| Exactly-one admitted native invocation | AIOS Dispatcher |
| Deterministic repository completion and canonical verification | AIOS Runtime |
| Canonical RESULT / EVIDENCE truth | AIOS Runtime |
| Semantic PASS / CHANGES_REQUIRED / BLOCKED | Reviewer |
| Wakeup, transport, reconciliation, notifications | Outer automation |
| Publication | Separate publication authority |

This inventory is an architectural input, not a reason to repeat already-proven work or create a duplicate documentation phase.

### K0.2 — Core Authority Extraction — DONE

Implemented and published through the current lineage:

- TASK-051: thin deterministic Dispatcher boundary;
- TASK-052: explicit Runtime completion boundary;
- TASK-053: executor-neutral bounded native execution deadline;
- TASK-054: single package-version authority.

The purpose of K0.2 was authority separation and de-duplication, not source-size minimization.

### K0.3 — Native Adapter Thinning — DONE

Implemented and published by TASK-055. Dispatcher retains provider-neutral admitted execution policy and exactly-one dispatch authority, while Codex- and Antigravity-specific native mechanics are contained behind their corresponding adapter boundaries.

AIOS keeps provider-neutral execution policy and authority semantics such as:

- selected executor identity;
- admitted mutation authority;
- exactly-one invocation;
- bounded native execution;
- workspace binding;
- no executor push;
- structural ResultPackage boundary;
- Runtime-owned canonical verification and EVIDENCE.

Provider-specific CLI flags, sandbox/mode mapping, native command construction, native response-envelope handling, and provider-specific execution instructions belong behind the corresponding native adapter boundary.

K0.3 preserved effective current behavior. It did not redesign permissions, transport, executor selection, retry/failover, or add a new orchestration layer.

### K0.4 — Lean Kernel Conformance Gate — NEXT / FINAL REQUIRED K0 STEP

K0.4 is the final required AIOS gate before returning to Python Agent product work.

It must prove, with minimum-sufficient deterministic and native conformance evidence, that both supported executors preserve the same canonical semantics where applicable, including:

- PRIMARY execution;
- read-only and mutation authority;
- exactly-one selected Executor invocation;
- fail-closed scope / SHA / dirty-state completion gates;
- narrow REMEDIATION continuity;
- REPAIR continuity;
- bounded timeout with no retry/fallback/reroute;
- Runtime-owned canonical verification executed once per completion attempt;
- structural Executor output followed by Runtime-owned canonical EVIDENCE;
- no executor publication/push authority.

K0.4 should also retain a small baseline of execution time, verification time, available token usage, and Human intervention so future efficiency/automation work can be justified against the North Star.

A separate A0 conformance phase is not required; its useful purpose is absorbed into K0.4 to avoid duplicate verification work.

## Hard Gate After K0.4

When K0.4 is semantically reviewed PASS:

```text
STOP DEFAULT KERNEL DEVELOPMENT
        ↓
BRAIN SYNC DOWNSTREAM PYTHON AGENT
        ↓
READ EXACT PINNED AIOS DEPENDENCY
        ↓
EXPLICIT DOWNSTREAM MIGRATION IF JUSTIFIED
        ↓
CONTINUE PYTHON AGENT PRODUCT ROADMAP
```

Do not open further kernel work merely to reduce LOC, rename abstractions, make architecture aesthetically cleaner, or replicate newly available executor-harness capabilities.

New kernel work after this gate requires observed engineering evidence of a kernel-boundary defect or a separately Human-authorized semantic change.

## Transport Extraction — MOVED OUT OF K0

The current review/transport compatibility path remains until an outer replacement has proven equivalent canonical delivery and lineage behavior.

Do not remove a working transport before replacement parity exists. Transport extraction belongs to the outer automation track, not the required pre-Python-Agent kernel gate.

## A-Series — Optional Outer Automation Track

A-Series does not block Python Agent product development.

1. **A1 — GitHub Actions Self-hosted Wakeup**: remote Human/Brain trigger to canonical Operator execution.
2. **A2 — Durable Dispatch Identity + Reconciliation**: duplicate event becomes deterministic no-op; crash/restart remains attributable.
3. **A3 — Remote Status / Approval Surface**: expose useful execution state without creating authority.
4. **A4 — Transport Extraction**: replace the compatibility transport only after the outer mechanism proves parity, then remove obsolete transport code.
5. **A5 — Evidence Bundle Strengthening**: only where measured gaps justify additional evidence packaging.
6. **A6 — Automated REVIEW-to-REMEDIATION Wakeup**: outer automation preserves narrow correction authority.
7. **A7 — Autonomous Reviewer Shadow Mode**: observe and compare before any review-decision authority is granted.
8. **A8 — Safe Publisher**: separate, explicit publication authority.
9. **A9 — Low-risk Zero-touch Lane**: only after measured reliability and bounded authority are demonstrated.
10. **A10 — Scale**: only when measured ROI justifies additional concurrency or agent use.

## H-Series — Optional Efficiency Track

H-Series is not an authority layer. It may improve:

- minimum-sufficient context;
- reuse until invalidated;
- executor-native skills/profiles;
- tool/context curation;
- compaction;
- model/effort selection;
- execution telemetry.

H-Series must not own TASK semantics, mutation authority, canonical verification, review verdict, dispatch authority, or publication.

## M-Series — Retired as an Active Sequential Roadmap

Useful M-Series ideas are absorbed into the current architecture or deferred by evidence:

- canonical state, executor neutrality, lease, and deterministic dispatch are already represented in the Kernel/Dispatcher/Runtime boundaries;
- third-executor support is deferred until there is measured value;
- multi-agent execution is an Executor-native conformance concern, not an AIOS orchestration framework;
- hot handoff is deferred and is not a kernel completion criterion;
- SDK/API integration is an adapter/outer-automation mechanism when actual automation requires it.

Do not execute M1→M11 again as a separate roadmap.

## Roadmap Rule

The active required sequence is intentionally short:

```text
DONE: K0.0 Governance
DONE: K0.1 Responsibility Audit
DONE: K0.2 Core Authority Extraction
DONE: K0.3 Native Adapter Thinning
        ↓
NEXT / FINAL: K0.4 Lean Kernel Conformance Gate
        ↓ PASS
STOP DEFAULT KERNEL DEVELOPMENT
        ↓
RETURN TO PYTHON AGENT
```

Everything else must justify itself against the Manifesto, Constitution, observed evidence, and the North Star.
