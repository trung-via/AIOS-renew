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

## Post-Closure Human Priority — Downstream Control-plane Portability Policy

The Human has authorized `downstream-control-plane-portability-policy`, bound to
TASK-114 revision 1, as the post-closure planning priority. `AUTHORIZED` records Human
intent; it is not an implementation-completion, semantic-PASS, or publication claim.
This priority does not reopen the completed Control-plane Closure track and does not
promote A4, A5, A7, A9, A10, H-Series, or new kernel development.

Standard AIOS outer control-plane capabilities are portable downstream by design.
Repository-specific configuration is an activation boundary, not a reason to classify
the capability as semantically inapplicable. A downstream repository may adopt a
coherent reviewed control-plane binding/profile whose contract and evidence cover the
complete compatible dependency graph.

Portability never bypasses exact-pin isolation or silently activates a repository.
Package or exact-pin availability alone creates no workflow, permission, repository
identity, actor allowlist, runner label or path, carrier binding, or downstream mutation
authority. Activation remains an explicit, reviewed, downstream-owned repository
change. AUTHORING through TASK-107 Brain ingress, PRIMARY through TASK-108 wakeup and
A1/A2 dispatch, REMEDIATION through A3/A6 approval and delivery plus TASK-112 intent,
TASK-110 publication continuation, REPAIR through TASK-111 wakeup, ATTENTION through
TASK-113 terminal notification, Runtime verification, semantic Reviewer decisions,
and Publisher authority remain distinct. A portability profile neither merges them
nor creates a generic lifecycle router or new semantic authority.

## Completed Sequential Priority — Control-plane Closure

The current machine-readable sequencing bookmark is [`.ai/roadmap-state.yaml`](../.ai/roadmap-state.yaml). It binds `control-plane-closure` as the active track with this ordered sequence:

1. **Admission Failure v2 — DONE**: completed by TASK-081 at published SHA `65c1597eb59b06041fba1d749f2dea87e2c00833`.
2. **Correction Preflight — DONE**: completed by TASK-085 at published SHA `36ff663675ac04b94da406536ac523e820b1b21e`.
3. **Unified State + Next Action — DONE**: completed by TASK-086 at published SHA `e56abece22509dc6e7f4d3b641c9d5600798ec67`.
4. **Unified Human Surface — DONE**: completed by TASK-087 at published SHA `a607fb2cf1c57fe35a9a15504df0e98d28de2f5b`.
5. **Downstream Adoption — DONE**: closed by TASK-109 only after canonical Python Agent TASK-201 received semantic PASS, its reviewed source was published, and a fresh downstream BRAIN SYNC proved that `trung-via/python_complete_agent` had migrated its sole active AIOS dependency to the exact immutable pin `f0237a3b98985ce6ebbaf41af1e06fa3eb4e998e`.

Control-plane Closure is `COMPLETE` and has zero `NEXT` items. Completion does not promote A4, A5, A7, A9, A10, or any other optional or separately gated milestone merely to keep an upstream `NEXT` item. An explicit current Human priority change must be canonicalized before later generic continuation relies on it.

Downstream Adoption required an explicit reviewed Python Agent migration from its prior exact pin to `f0237a3b98985ce6ebbaf41af1e06fa3eb4e998e`; the presence of capabilities on mutable AIOS-renew `main` was never sufficient. The adopted exact pin remains the downstream runtime authority until another explicit downstream migration changes it. TASK-109's statement that AIOS-renew repository-specific outer automation was not automatically activated, and that TASK-201 owned the classification for that migration, remains an accurate historical fact. It does not make standard AIOS control-plane capability prospectively inapplicable: current policy separates portable package capability from explicit, reviewed, downstream-owned repository binding and activation.

The completed-track handoff does not copy Python Agent's roadmap pointer or a previously observed downstream commitment into this upstream roadmap. After TASK-114 receives semantic PASS and its reviewed source is published, perform a fresh downstream BRAIN SYNC of current Python Agent main, exact AIOS pin, roadmap and adoption state, governance, and relevant immutable lineage, then follow that repository's current canonical authority.

This bookmark is Human/Brain planning state only. Exact canonical Git state and immutable TASK/RUN/RESULT/FAILURE/REVIEW/REMEDIATION/REPAIR lineage remain the sole engineering-state truth. A `DONE` item requires an exact supporting TASK and published SHA; the bookmark neither proves implementation success nor makes a TASK or RUN complete, and it is never advanced automatically by execution, review, or publication.

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

### K0.4 — Lean Kernel Conformance Gate — DONE

Completed and semantically reviewed PASS via TASK-056 (RUN-056-001).

It proved, with deterministic and native conformance evidence, that both supported executors preserve the same canonical semantics:

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

### Post-K0 Hardening (TASK-057 through TASK-065) — DONE

Following K0.4 completion and the hard gate stopping default kernel expansion, subsequent TASK-057 through TASK-065 performed evidence-driven hardening of observed kernel, operator, and runtime boundaries without reopening general kernel development:

- **TASK-057**: Runtime-owned REPAIR changed_files authority;
- **TASK-058**: Deterministic historical REPAIR recovery (revision 2);
- **TASK-059**: Native execution-efficiency/interruption hardening;
- **TASK-060**: 60-minute response budget plus 65-minute outer watchdog;
- **TASK-061**: Deterministic model defaults;
- **TASK-062**: PRIMARY auto-sync;
- **TASK-063**: Safe publication (realizing A8);
- **TASK-064**: Verification-only NO_CHANGE continuation;
- **TASK-065**: Native token observation.

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

A-Series does not block Python Agent product development. It provides subordinate outer automation around the governed kernel:

1. **A1 — GitHub Actions Self-hosted Wakeup**: remote Human/Brain trigger to canonical Operator execution on a designated Windows self-hosted runner. Implemented by TASK-066 (`.github/workflows/aios-self-hosted-wakeup.yml`).
2. **A2 — Durable Dispatch Identity + Reconciliation — DONE**: duplicate event becomes deterministic no-op; crash/restart remains attributable. Semantics established by TASK-068 and integrated onto the published current-main lineage by TASK-073 (`aios wakeup` plus repository-local `.git/aios` dispatch state).
3. **A3 — Remote Status / Approval Surface — DONE**: bounded read-only dispatch observation and exact SHA-bound Human remediation approval, with no execution authority. Completed by TASK-074.
4. **A4 — Transport Extraction**: replace the compatibility transport only after the outer mechanism proves parity, then remove obsolete transport code. Separately gated.
5. **A5 — Evidence Bundle Strengthening**: only where measured gaps justify additional evidence packaging. Separately gated.
6. **A6 — Automated REVIEW-to-REMEDIATION Wakeup — DONE AS PARALLEL WORK**: completed by TASK-084 at published SHA `93cc833e03c15bd1c57a53093476af671c5f6027`. An exact existing A3 approval can be delivered once through a durable correction identity and explicit Executor into the unchanged canonical REMEDIATION path. TASK-082 established the reviewed semantics, but neither divergent TASK-082 candidate was published onto current main. A6 is not part of the active Control-plane Closure sequence and does not compete with its `NEXT` item.
7. **A7 — Autonomous Reviewer Shadow Mode**: observe and compare before any review-decision authority is granted. Separately gated.
8. **A8 — Safe Publisher**: separate, explicit publication authority. Completed by TASK-063 (`.github/workflows/aios-auto-publish.yml` and `aios_renew.publication`).
9. **A9 — Low-risk Zero-touch Lane**: only after measured reliability and bounded authority are demonstrated. Separately gated.
10. **A10 — Scale**: only when measured ROI justifies additional concurrency or agent use. Separately gated.

A4, A5, A7, A9, and A10 remain separately gated. A2 dispatch attribution, A3 approval, and A6 correction-delivery attribution remain operational-only; none creates review, correction-authoring, verification, recovery, retry, routing, or publication authority.

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

The completed kernel and outer-automation lineage is summarized as follows; it is historical context, not a competing active sequence:

```text
DONE: K0.0 Governance
DONE: K0.1 Responsibility Audit
DONE: K0.2 Core Authority Extraction
DONE: K0.3 Native Adapter Thinning
DONE: K0.4 Lean Kernel Conformance Gate (TASK-056)
        ↓ PASS
STOP DEFAULT KERNEL DEVELOPMENT
        ↓
DONE: Post-K0 Hardening (TASK-057..065)
        ↓
Outer Automation Track:
DONE: A8 Safe Publisher (TASK-063)
DONE: A1 GitHub Actions Self-hosted Wakeup (TASK-066)
DONE: A2 Durable Dispatch Identity + Reconciliation (TASK-068, current-main integration TASK-073)
DONE: A3 Remote Status / Approval Surface (TASK-074)
DONE: A6 Automated REVIEW-to-REMEDIATION Wakeup (TASK-084)
        ↓
COMPLETE SEQUENTIAL TRACK: Control-plane Closure
DONE: Admission Failure v2 (TASK-081)
DONE: Correction Preflight (TASK-085)
DONE: Unified State + Next Action (TASK-086)
DONE: Unified Human Surface (TASK-087)
DONE: Downstream Adoption (TASK-109 after Python Agent TASK-201 migration to exact AIOS pin f0237a3b98985ce6ebbaf41af1e06fa3eb4e998e)
```

Control-plane Closure has zero `NEXT` items. Separately gated A4, A5, A7, A9, A10, and other feasible parallel milestones do not become current work from generic continuation intent. Everything else must justify itself against explicit Human intent, the machine-readable bookmark, the Manifesto, Constitution, observed evidence, and the North Star.

The authorized post-closure portability-policy priority does not alter this historical
completion sequence. After its semantic PASS and reviewed source publication, control
hands back through a fresh downstream BRAIN SYNC; no downstream `NEXT` identifier is
made durable upstream.
