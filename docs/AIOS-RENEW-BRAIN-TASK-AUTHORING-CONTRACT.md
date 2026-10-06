# Brain TASK Authoring Contract

This is the compact authoring boundary for canonical AIOS-renew TASKs. It supplements, and does not replace or revise, the frozen v0.1 specification.

## Constitutional preflight

Before drafting or revising a TASK, perform a constitutional preflight against the canonical governance baseline:
- [AIOS Manifesto](AIOS-MANIFESTO.md) — Ensure the work serves verified useful work and the optimization North Star (`Verified Useful Work / (Time + Tokens + Human Effort)`), avoiding unneeded agents, calls, reasoning loops, or activity.
- [AIOS Constitution](AIOS-CONSTITUTION.md) — Verify adherence to constitutional principles: Human intent ownership, separation of authority (Brain WHAT/WHY, Executor HOW, Runtime coordination, Reviewer judgment), bounded mutation authority, claims requiring evidence, reproducible canonical state, and correction continuity. Confirm that proposed delivery mechanisms remain subordinate and replaceable.

## Establish identity first

Before reasoning about a TASK, REVIEW, REMEDIATION, or FIX lineage, establish the canonical repository and worktree, the relevant canonical artifact identifiers and revisions, and the immutable Git SHA to which they bind. Never infer identity or lineage from chat history, screenshots, UI labels, or remembered state.

New Human intent is not a FIX. It requires a new TASK or TASK revision. REVIEW findings and any REMEDIATION must be resolved from canonical artifacts and their immutable reviewed SHA; remediation addresses only the authorized finding and minimum supporting delta.

## Author the outcome

The Brain owns **WHAT and WHY**: the goal, problem, assumptions, non-goals, constraints, scope, and acceptance criteria. The Executor owns **HOW**. Do not prescribe an implementation plan except where a true architectural constraint makes a choice part of the required outcome.

A TASK is executor-neutral: the same contract must be executable by any admitted Executor, with no executor identity, model-specific directions, or adapter-specific workflow embedded in it.

Use `scope.inspect` only as minimum-context guidance. Use `scope.modify` as the hard mutation authority: list only the exact, minimal, repo-relative file paths required by the outcome. Do not use directories, absolute paths, traversal, backslashes, or glob patterns.

Acceptance criteria must be atomic, observable, and collectively complete. Each criterion should describe one independently reviewable outcome, avoid implementation steps, and leave no required behavior merely implied.

For every newly authored TASK, each acceptance criterion must be truthfully claimable by an admitted Executor as a concrete implementation property before Runtime verification. Author the criterion around what the completed implementation establishes, not a future verification outcome or lifecycle event. Runtime verification timing and results, canonical EVIDENCE, Reviewer judgment, publication, roadmap advancement, and other later lifecycle facts remain with their existing authorities. Brain and Human own this semantic authoring judgment; structural validation does not classify acceptance prose.

## Runtime AUTHOR_TASK admission

After publication of the reviewed BO-1 transition, AUTHOR_TASK has one production
mutation path: Runtime validates the final TASK contract and its canonical/provenance
bindings directly. The operational envelope must omit `audited_handoff`; supplying it,
including an explicit null field, is rejected for new identities, revisions and replay.
Runtime does not require, reconstruct, validate, fingerprint or freshness-recheck a
Decision Packet, Stage-1/Stage-2 material, `acceptance_phase_ledger` or TASK_AUTHORING
audit-support sections for AUTHOR_TASK mutation.

Direct admission preserves schema validation, exact payload/identity agreement and
revision continuity, `minimum-sufficient-v1`, authored return-affinity preservation,
expected-main currentness, unrelated-delta rejection and expected-old-main
compare-and-swap. Any conflicting direct contract or provenance binding fails closed.
Identical historical replay remains read-only under its existing identity rules.

New revision-1 `ORIGIN_AFFINE` TASKs still require independently admitted
`origin_authoring_proof` in the operational envelope, outside the TASK payload.
Admission binds exact carrier attempt, TASK id, expected main, route/generation and
envelope digest, with existing HMAC, freshness, replay and same-attempt-idempotence
guards. Legacy TASKs and revisions cannot consume a current-chat origin proof;
revisions preserve their existing authored affinity.

Brain must still perform `CONSTRUCT -> ADVERSARIAL_AUDIT_AND_RECONCILE`. Its
transient semantic support remains a cognitive discipline, not a Runtime mutation
credential. AUTHOR_REMEDIATION and AUTHOR_REPAIR retain their existing audited
handoffs, canonical reconstruction, correction-lineage and freshness validation.

## Declare acceptance proof phase during the Brain audit

For prospective `TASK_AUTHORING`, Brain alone classifies acceptance proof phase
during the existing adversarial audit and reconciliation:

- `CLAIM_NOW`: a concrete implementation property the Executor can truthfully
  claim at completion, before Runtime verification.
- `PROOF_LATER`: truth dependent on Runtime verification or results, canonical
  EVIDENCE, Reviewer judgment, publication, roadmap advancement or another later
  lifecycle fact.

The Stage-2 semantic material carries one `acceptance_phase_ledger` array alongside
`construct_audit`, `reconciled_candidate`, `closure` and `outcome`. Each entry has
exactly `id` (non-empty text, at most 256 UTF-8 bytes) and `phase` (`CLAIM_NOW` or
`PROOF_LATER`). The ledger is bounded to 256 entries and 32768 UTF-8 JSON bytes.
It is normalized and bound into `stage2_fingerprint` and serialized Brain decision
identity. Substituting the ledger changes that identity.

Before the final `CANDIDATE`, reconcile every `PROOF_LATER` requirement out of
final acceptance and retain its intent on existing verification, constraint or
non-goal surfaces as semantically appropriate. If reconciliation cannot close the
risk, return a closure `BLOCKER` and `NO_DECISION`. A valid final ledger covers the
reconciled candidate's acceptance ids exactly once, all `CLAIM_NOW`.

Brain's semantic audit must establish exact final acceptance coverage and all
`CLAIM_NOW` entries. BP-4A retains its bounded declared-shape and fingerprint
validation within that audit protocol. The historical H3 AUTHOR_TASK ingress
ledger gate is superseded by BO-1: Runtime neither receives nor validates this
ledger as mutation authority. Runtime, provider protocol and ingress never infer
phase from acceptance prose.

The ledger is transient cognitive support, never lifecycle truth, evidence or a
persistent reasoning record. Do not add it to the TASK candidate or canonical TASK
bytes; frozen acceptance entries still contain only `id` and `condition`.
REMEDIATION_AUTHORING, REPAIR_AUTHORING and other flows retain their existing
contracts and reject this TASK-only material. TASK authoring and historical
read-only replay both omit this material from Runtime ingress. This contract
creates no RUN, execution, verification, verdict, publication or roadmap advancement.

## Specify verification once

Put every canonical verification command only in `verification.required`. Do not repeat commands as execution instructions in the goal, problem, assumptions, non-goals, or constraints.

Every newly authored TASK identity or revision must declare `verification.policy: minimum-sufficient-v1`. The `required` list must be non-empty and contain deterministic, non-interactive, minimum-sufficient commands in the required order. A newly authored REMEDIATION for such a TASK must use the same policy under `verification`, with its commands in `verification.affected`. Legacy TASK and REMEDIATION artifacts remain readable and usable; the rule is prospective, and an identical same-revision historical TASK replay remains idempotent.

The v1 contract rejects exact duplicates and only those equivalent or subsumed pytest commands whose coverage relationship is mechanically proven by the repository-owned grammar. That grammar keeps `pytest` and `python -m pytest` as separate launcher families and recognizes only quiet output flags, explicit positional test paths, and an optional `-k` narrowing expression. Unsupported, malformed, shell-composed, or ambiguous commands are opaque and are never removed based on similarity.

A recognized full-suite pytest command (one with no path and no `-k` filter) requires a non-empty `full_suite_reason` of at most 512 characters. The field is invalid without such a command. This is structural policy only: Brain and Reviewer authority retain semantic judgment over the command selection and whether the reason is persuasive.

Prefer focused checks that establish the acceptance criteria. Do not add Git cleanliness, HEAD, changed-files, or similar repository-integrity checks by default: Runtime already owns those gates. REPAIR may deterministically normalize only overlap created when it combines already-authorized v1 TASK and origin REMEDIATION lists. Runtime still executes the resulting canonical list and constructs EVIDENCE; no evidence authority moves to authoring or repair policy.

For ordinary newly authored AIOS-renew full-suite proof on the supported Windows AMD64 self-host class, author the exact command `python scripts/aios_parallel_full_suite.py` in `verification.required` with `full_suite_reason`. This explicit selected profile uses four workers and emits a soft performance observation under `.ai/verification-profiles.yaml`. Brain may author a serial or other full-suite command when the TASK semantics require it, with a reason. Runtime executes the authored command exactly; it does not substitute profiles. Historical TASK commands remain immutable.

Do not instruct the Executor to push. Executor implementation ends at the permitted final local commit; synchronization and publication remain outside Brain-authored implementation instructions.
