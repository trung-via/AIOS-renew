# AIOS Execution Profile Selection v1

Status: HUMAN-APPROVED PLANNING BASELINE  
Approved: 2026-09-23  
Approved against canonical main: `6a6d8b691ff5a694c065d56fc11e51283c109cf5`

## Purpose

Add exact, Human-controllable execution-profile selection above the existing Codex and Antigravity native adapters without creating a model router, changing TASK semantics, or weakening deterministic execution provenance.

This planning baseline is a temporary Human-priority side-track. It does not complete implementation, create a RUN, advance engineering lifecycle state, or reinterpret historical artifacts.

## Human-approved target behavior

When the Human selects only an Executor, AIOS resolves the repository-owned default profile:

- `codex` -> `gpt-6-sol` with reasoning effort `medium`;
- `antigravity` -> `gemini-3.8-flash` with reasoning effort `medium`.

When the Human explicitly supplies a model and/or reasoning effort, the exact explicit value overrides the corresponding default and must reach the selected native Executor unchanged after bounded validation.

Examples:

- `codex` -> `gpt-6-sol / medium`;
- `codex high` -> `gpt-6-sol / high`;
- `codex low` -> `gpt-6-sol / low`;
- `antigravity` -> `gemini-3.8-flash / medium`;
- `antigravity high` -> `gemini-3.8-flash / high`;
- an explicitly named alternate provider-native model remains allowed when the selected native Executor supports it.

Unsupported or unavailable explicit selections fail closed. There is no automatic fallback, retry, reroute, model scoring, difficulty classification, or adaptive model/effort selection.

## Authority and boundary decisions

1. Human explicit selection outranks repository defaults.
2. Repository defaults are deterministic configuration, not reasoning authority.
3. Model identifiers are provider-native implementation metadata and are not globally allowlisted by AIOS beyond bounded syntax validation.
4. Reasoning effort is a bounded provider capability and must be validated before invocation.
5. Resolution occurs once per new execution authorization before dispatch/native invocation.
6. The resolved profile is immutable for that admitted authorization. Restart/reconciliation must reuse the exact bound profile even if defaults later change.
7. A new PRIMARY, REMEDIATION, or REPAIR authorization resolves a fresh profile. It does not implicitly inherit the previous RUN profile when Human did not explicitly request that inheritance.
8. Exact model/effort becomes part of dispatch identity for new versioned carrier/dispatch contracts so replay with different profile values fails closed.
9. Exact resolved values must be durably attributable before native invocation through an additive subordinate execution-profile artifact rather than by rewriting frozen RUN v0.1 semantics.
10. TASK remains executor/model neutral. Model/effort is operational execution metadata, not TASK meaning.
11. Runtime verification, Reviewer authority, Publisher authority, RunLease, ExecutorBoundary, mutation permissions, sandbox selection, and canonical evidence ownership do not move.
12. Historical TASK/RUN/dispatch/carrier artifacts are never reinterpreted under new defaults.

## Compatibility strategy

Implementation is split into two bounded milestones.

### EP-1 — TASK-162 Execution Profile Foundation v1

Build additive profile-resolution and provenance plumbing while preserving current production execution behavior until activation. Establish:

- repository-owned execution-profile policy contract;
- `ResolvedExecutionProfile` validation/resolution;
- exact profile injection into Codex and Antigravity adapters;
- immutable per-RUN/profile provenance written before native invocation;
- deterministic executor/profile consistency checks;
- focused compatibility and failure tests.

EP-1 must not activate the new production defaults across Human/remote carriers and must not modify frozen TASK or RUN schemas.

### EP-2 — TASK-163 Human Selection, Carrier Binding, and Default Activation

After EP-1 receives canonical PASS/publication, propagate exact profile selection through Human CLI/continue and PRIMARY, REMEDIATION, and REPAIR carriers/dispatch journals, version the strengthened contracts, activate the new defaults, and prove end-to-end native invocation.

## Roadmap relationship

The canonical Brain Portability track remains active. BP-V4 is paused only by this explicit Human priority and is not cancelled or reinterpreted.

Sequence:

```text
TASK-162 — Execution Profile Foundation v1
    ↓
TASK-163 — Human Selection + Carrier Binding + Default Activation
    ↓
return to BP-V4 Parallel Verification and Performance Guard
```

Historical BP-V4 work and all existing evidence remain immutable.

## Non-goals

- automatic model router or model scoring;
- autonomous low/medium/high selection from perceived task difficulty;
- fallback from one model, effort, or Executor to another;
- changes to Antigravity MiniMax semantics;
- changes to canonical TASK meaning;
- changes to frozen RUN schema or one-active-executor lease semantics;
- changes to sandbox/mutation authority;
- moving canonical verification or semantic review to an LLM;
- rewriting historical execution identity under the new defaults.
