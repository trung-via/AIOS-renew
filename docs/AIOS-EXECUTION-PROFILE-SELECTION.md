# AIOS Execution Profile Selection v1

Status: HUMAN-APPROVED PLANNING BASELINE  
Approved: 2026-09-23  
Approved against canonical main: `6a6d8b691ff5a694c065d56fc11e51283c109cf5`

Current Human-approved Codex policy (TASK-195): `gpt-6-sol / high` by default,
with supported reasoning efforts `none`, `low`, `medium`, `high`, `xhigh`, `max`
in that order. The repository policy remains the single default and capability
authority; the selection, provenance, and lifecycle boundaries below still apply.
Antigravity remains `gemini-3.8-flash / medium` with supported efforts `low`,
`medium`, `high`.

## Purpose

Add exact, Human-controllable execution-profile selection above the existing Codex and Antigravity native adapters without creating a model router, changing TASK semantics, or weakening deterministic execution provenance.

This planning baseline is a temporary Human-priority side-track. It does not complete implementation, create a RUN, advance engineering lifecycle state, or reinterpret historical artifacts.

## Human-approved target behavior

When the Human selects only an Executor, AIOS resolves the repository-owned default profile:

- `codex` -> `gpt-6-sol` with reasoning effort `high`;
- `antigravity` -> `gemini-3.8-flash` with reasoning effort `medium`.

When the Human explicitly supplies a model and/or reasoning effort, the exact explicit value overrides the corresponding default and must reach the selected native Executor unchanged after bounded validation.

Examples:

- `codex` -> `gpt-6-sol / high`;
- `codex none` -> `gpt-6-sol / none`;
- `codex low` -> `gpt-6-sol / low`;
- `codex medium` -> `gpt-6-sol / medium`;
- `codex high` -> `gpt-6-sol / high`;
- `codex xhigh` -> `gpt-6-sol / xhigh`;
- `codex max` -> `gpt-6-sol / max`;
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

## PRIMARY durable journal policy boundary (TASK-212)

Python Agent `RUN-256-001` exposed a portability defect in the installed AIOS
package governing an external repository. The downstream repository had a valid
`.ai/executor-profiles.yaml` and bound `codex / gpt-6-sol / high` with
`REPOSITORY_DEFAULT` attribution, but the PRIMARY v3 journal read at the
completion gate looked for policy beneath the transient installed worker package.
The canonical failure artifact is `e0e721aad9959880de464705a225e87c9c19820d`;
it records no changed files and a clean failed head equal to the RUN base.

Every PRIMARY v3 journal creation, read, RUN bind, replay, reconciliation, and
status inspection validates the stored exact identity against the explicit
governed repository's policy file. Runtime state paths and package install paths
are not repository authority. A missing or malformed governed policy, invalid
profile, or unsupported effort blocks new v3 authorization before native
invocation. Changed defaults do not replace the journal's bound model, effort,
or source attribution. An immutable identity collision or mismatched per-RUN
sidecar cannot acquire dispatch RUN ownership. Historical PRIMARY v1/v2 records
remain readable under their original profile-unaware semantics without policy
backfill or rewrite. Status inspection remains observational.

TASK-212 fixed the PRIMARY v3 journal policy lookup. That defect is separate
from ordinary REPAIR profile resolution: at downstream pin
`31fd2482cd87d97fd818e05eb5b4dcec69ffeee6`, `_authorization_profile`
already resolves and validates ordinary REPAIR against the explicit governed
repository. With canonical Unified State and a consistent REPAIR authorization,
ordinary REPAIR can continue a clean pre-verification `COMPLETION_GATE` failure
such as `RUN-256-001` under its exact bound profile. Its existing
`REPAIR-256-001` revision-1 source-REPAIR-only instruction requires immutable
supersession before that ordinary delivery.

TASK-213 activation of the exact TASK-212 source-REPAIR target does not widen
subject lineage: `bootstrap-source-repair` still requires a consumed-and-completed
failed `source-bootstrap-v2` edge, which this ordinary PRIMARY-wakeup RUN lacks.
The Brain Portability plan records the separate downstream recovery order;
this profile correction itself performs no delivery, review, publication, or
migration.

TASK-256's ordinary REPAIR and publication are now complete. Python Agent's
canonical TASK-259 revision 1 then encountered a separate pre-RUN PRIMARY
carrier failure while installed generation `31fd2482...` checked a PRIMARY v3
journal beneath the transient worker package. Because no RUN or FAILURE was
created, source-REPAIR cannot be used. Normal `migrate-primary` also requires
an already-authored target control commit and target pin; TASK-259 is the work
authorized to create them. The explicit TASK-215
`bootstrap-source-upgrade-primary` command stages only the exact
`31fd2482...` to `44eee353...` source-control v2 handoff after isolated
installed-source attestation. TASK-216 corrects its history check for the
deliberate split between the reviewed activation/control Operator source and
the exact installed migration-source witness. The witness attests the installed
non-editable migration-capable `31fd2482...` distribution and its own operator
path; the separate `44eee353...` checkout is the target generation. Completed
history must end at that witnessed installed source, while ordinary callers
retain imported-package path consistency. The target's existing
`bootstrap-source-primary --accept-handoff` path owns TASK-259 RUN admission
and applies the governed Python Agent repository's profile policy. The staging
command accepts no model or effort override; the version-2 intent adds no
model, effort, or caller-selected lifecycle action. Staging creates no
execution-profile journal, RUN, FAILURE, or Executor observation. The eventual
target-owned RUN-259 resolves and binds the normal governed downstream
repository profile at target admission. Neither a Human-local transport shell
nor the reviewed staging Operator selects an execution profile for that RUN.
This is an exceptional bounded bridge; normal `migrate-primary` remains the
migration path when target control and pin exist.

TASK-217 prospectively clarifies the transport for this demonstrated
circular-bootstrap case. Current explicit Human intent may select one
Human-local PowerShell/shell invocation to write the exact v2 intent, select
the exact published TASK-217 activation/control source, locate the installed
worker Python, and enter its reviewed `bootstrap-source-upgrade-primary`
Operator through `scripts/aios_control_entry.py`. The shell and entry script
add no profile, migration, or lifecycle authority. This is one-time subordinate
transport, never automatic fallback, retry, Executor or Runtime selection, or
reusable automation authority. Reusable automated transport still requires a
separately reviewed repository-owned carrier. This prospective clarification
does not rewrite TASK-215/TASK-216 artifacts or the exact v2 identity,
installed-source provenance, migration-history, and target-owned RUN boundary.

After TASK-217 publication, the prospective sequence is exact published
TASK-217 activation/control source -> explicit Human-local bounded carrier
for unchanged Python Agent TASK-259 r1 -> exact version-2 edge
`31fd2482...` to `44eee353...` -> target-owned RUN-259 with normal governed
repository profile resolution -> semantic review and publication -> fresh
TASK-207 r9. No downstream step is completed by TASK-217.

## Future model evolution invariant

For an already-supported Executor whose native invocation contract remains compatible,
changing the default model in the future must be a repository-owned profile-policy change
plus focused validation/smoke, not an adapter, Dispatcher, RUN, carrier, or lifecycle
contract redesign.

Examples include prospective changes such as `gpt-6-sol -> gpt-7-sol` through Codex
or `gemini-3.8-flash -> gemini-4-flash` through Antigravity when the corresponding
native CLI continues to accept the same model-selection and effort-selection surface.

The architecture must therefore satisfy all of the following:

- default model/effort values have one repository-owned policy authority and are not
  duplicated as adapter-owned constants;
- model identifiers remain opaque provider-native identifiers subject only to bounded
  syntax validation, not a repository-global model allowlist;
- changing a compatible default must not require changes to frozen TASK/RUN semantics,
  Dispatcher selection semantics, Human carrier schemas, or lifecycle authority;
- explicit Human alternate-model selection must not require code changes merely because
  the model identifier is new;
- provider capability drift such as a changed effort vocabulary may require a narrow
  adapter/capability-validation update, but must not require rebuilding the profile
  foundation;
- a genuinely new provider or incompatible native invocation surface may require a new
  adapter/adoption task, but that is distinct from ordinary default-model evolution;
- changing current defaults never reinterprets historical RUN execution identity because
  each admitted native invocation retains its exact resolved profile provenance.

TASK-162 must establish this invariant structurally. TASK-163 must prove it through the
Human-facing selection and activation path.

## Compatibility strategy

Implementation is split into two bounded milestones.

### EP-1 — TASK-162 Execution Profile Foundation v1

Build additive profile-resolution and provenance plumbing while preserving current production execution behavior until activation. Establish:

- repository-owned execution-profile policy contract with a single default authority designed for future compatible model changes without adapter/Dispatcher redesign;
- `ResolvedExecutionProfile` validation/resolution;
- exact profile injection into Codex and Antigravity adapters;
- immutable per-RUN/profile provenance written before native invocation;
- deterministic executor/profile consistency checks;
- focused compatibility and failure tests;
- regression proof that a synthetic future provider-native model identifier can flow through the existing Executor profile path without a global model allowlist or adapter architecture change.

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
