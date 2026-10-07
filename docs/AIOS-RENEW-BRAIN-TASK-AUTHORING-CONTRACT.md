# Brain TASK Authoring Contract

This is the compact authoring boundary for canonical AIOS-renew TASKs. It supplements, and does not replace or revise, the frozen v0.1 specification.

## Navigation classification

Current generic self-host operational navigation: [AIOS Self-Host End-to-End Flow v1](AIOS-SELF-HOST-END-TO-END-FLOW-v1.md).
REPLACE_WITH_POINTER applies to that traversal only.
RETAIN_NORMATIVE: constitutional/identity preflight, Brain WHAT/WHY, exact scope,
acceptance authorability, semantic audit and verification authoring obligations.
Use the entrypoint section 9 bounded impact map before closing self-host change
scope, including all durable outer TASK-314/TASK-315 executor_required consumers.
The map cannot choose work or grant modification scope. Canonical published
ingress/protocol governs any superseded audit-plumbing claim below; retaining
semantic audit authority does not reactivate retired mutation prerequisites.

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

## Declare acceptance proof phase in Stage 2

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

Before final `CANDIDATE` handoff, reconcile every `PROOF_LATER` requirement out of
final acceptance and retain its intent on existing verification, constraint or
non-goal surfaces as semantically appropriate. If reconciliation cannot close the
risk, return a closure `BLOCKER` and `NO_DECISION`. A valid final ledger covers the
reconciled candidate's acceptance ids exactly once, all `CLAIM_NOW`.

AUTHOR_TASK ingress requires this ledger for every new identity or revision and
rejects missing, duplicate, extra, substituted or `PROOF_LATER` entries before
mutation. BP-4A only validates bounded declared shape and fingerprints it; final
acceptance coverage and the all-`CLAIM_NOW` gate belong to ingress. Runtime,
provider protocol and ingress never infer phase from acceptance prose.

The ledger is transient cognitive support, never lifecycle truth, evidence or a
persistent reasoning record. Do not add it to the TASK candidate or canonical TASK
bytes; frozen acceptance entries still contain only `id` and `condition`.
REMEDIATION_AUTHORING, REPAIR_AUTHORING and other flows retain their existing
contracts and reject this TASK-only material. Identical historical TASK replay
remains non-mutating and does not require a ledger; prospective changes cannot
bypass the gate. This contract creates no RUN, execution, verification, verdict,
publication or roadmap advancement.

## Specify verification once

Put every canonical verification command only in `verification.required`. Do not repeat commands as execution instructions in the goal, problem, assumptions, non-goals, or constraints.

After reviewed publication of TASK-317, every newly authored TASK identity or revision must declare `verification.policy: minimum-sufficient-v2`. The `required` list must be non-empty and contain deterministic, non-interactive, minimum-sufficient commands in the required order. A REMEDIATION for a policy-bearing TASK must use that TASK's exact policy under `verification`, with its commands in `verification.affected`. Legacy artifacts, including `minimum-sufficient-v1`, remain readable and usable; identical same-revision historical TASK replay remains idempotent. TASK-317 itself remains a v1 bootstrap TASK. This change does not migrate historical TASK or correction artifacts.

The v1 contract rejects exact duplicates and only those equivalent or subsumed pytest commands whose coverage relationship is mechanically proven by the repository-owned grammar. That grammar keeps `pytest` and `python -m pytest` as separate launcher families and recognizes only quiet output flags, explicit positional test paths, and an optional `-k` narrowing expression. Unsupported, malformed, shell-composed, or ambiguous commands are opaque and are never removed based on similarity.

A recognized full-suite pytest command (one with no path and no `-k` filter) requires a non-empty `full_suite_reason` of at most 512 characters. The field is invalid without such a command. This is structural policy only: Brain and Reviewer authority retain semantic judgment over the command selection and whether the reason is persuasive.

Prefer focused checks that establish the acceptance criteria. Do not add Git cleanliness, HEAD, changed-files, or similar repository-integrity checks by default: Runtime already owns those gates. V1 REPAIR may deterministically normalize overlap created when it combines already-authorized TASK and origin REMEDIATION lists. V2 retains every exact authored requirement, removes exact duplicates when combining sources, and puts authored correction commands before the TASK baseline. Runtime derives the still-required V2 list and constructs EVIDENCE; no evidence authority moves to authoring or repair policy.

For ordinary newly authored AIOS-renew full-suite proof on the supported Windows AMD64 self-host class, author the exact command `python scripts/aios_parallel_full_suite.py` in `verification.required` with `full_suite_reason`. The selected profile remains the Human-selected twelve-worker policy in `.ai/verification-profiles.yaml`; its soft performance observation remains subordinate. Brain may author a serial or other full-suite command when the TASK semantics require it, with a reason. Runtime executes the authored command exactly; it does not substitute full-suite profiles. Historical TASK commands remain immutable.

### One minimum-sufficient-v2 Runtime boundary

Brain owns the semantic envelope (what must be proven). V2 is one prospective policy absorbing attributed and deterministic delta verification; neither capability is a separate verification authority. `derive_minimum_verification` consumes only the authored baseline, exact base/candidate commits, committed changed files, complete canonical failed identities, admitted correction `modification_scope`, and bound Runtime evidence. It returns ordered new executions, explicit reused proof, covered exact correction probes and broader-fallback records. Changed paths never imply a semantic dependency graph. In this implementation cross-candidate reuse conservatively requires equality of the entire tracked tree; changes to any tracked file rerun the broader requirement. Missing, malformed, conflicting or stale material cannot discharge a requirement.

Canonical observations preserve the full exact nodeid/phase population, including passing phases needed for comparison and collection failures. Parameter text remains intact; only file-path separators are normalized. The separate Human display retains at most 20 identities and clips display nodeids at 240 characters. Canonical bounds are 100,000 phase reports, 10,000 failed identities, 16,384 characters per nodeid and failure detail, and 16 MiB per canonical JSON object. Exceeding a canonical bound fails closed with an explicit incomplete/unstable observation; display truncation never becomes identity truth. Every comparable failure binds the selected profile, toolchain (including interpreter, platform, pytest, xdist and installed distribution inventory), and the complete bounded detail plus its deterministic SHA-256 fingerprint. Only the exact checkout root is normalized in detail; volatile or divergent values remain observable. Early-stop populations and profile/toolchain changes during execution are incomplete or unstable. Runtime preserves exported raw profile observations even when collection or conformance errors stop the suite wrapper before its envelope is emitted.

Attribution compares exact phase observations under equal command, profile and toolchain, with Runtime-bound environment, repository verification-tool/observer and selected-policy identities. Exact base PASS/candidate FAIL is `CANDIDATE_REGRESSION`. Exact equivalent base FAIL/candidate FAIL is `PRE_EXISTING_BASELINE`. Candidate-only, skipped, unstable, incomplete, non-comparable or fingerprint-divergent observations are `UNRESOLVED`. Matching nodeid alone proves nothing. Candidate regressions and unresolved observations remain blocking.

Raw command output and nonzero exit codes are preserved. A distinct task-level outcome is recomputed from bound observations: exit 1 can be nonblocking only when a nonempty complete failure population is entirely `PRE_EXISTING_BASELINE`. Collection/internal errors, worker crashes, interrupts, missing failures and failed conformance remain blocking. An authored full-suite guard remains required for PRIMARY, REMEDIATION and REPAIR, even after an affected probe fails. Only still-valid evidence can discharge that same requirement without another execution.

Evidence binds subject, base, complete tracked tree, exact command, profile, toolchain, authored envelope/correction scope, committed delta and canonical failure set. Reuse additionally checks immutable raw-output digests, preserves the original observation subject and records its explicit current-subject binding. Missing raw data or changes to any relevant binding invalidate reuse. Deterministic plan records distinguish `REUSED` from `EXECUTED`; base observations are reused only with their exact conditions and raw bindings. Conflicting repeated observations fail closed.

For REMEDIATION and REPAIR, an exact failed-test probe may run first only when its canonical nodeids belong to explicitly authorized correction test files and are mechanically covered by an authored command. The admitted reviewed/failed subject and its committed correction delta determine that scope check; they do not replace the RUN's original attribution base or turn an earlier candidate regression into baseline debt. Parameterized identities are passed intact. This probe never discharges an unproven broader requirement. Proven baseline failures never add files to modification_scope or select a correction. V2 has no planning, semantic review, correction selection, publication, retry/failover or roadmap authority. RUN-316-002 remains frozen; TASK-317 authorizes no REPAIR-316-002 and changes none of its artifacts.

Do not instruct the Executor to push. Executor implementation ends at the permitted final local commit; synchronization and publication remain outside Brain-authored implementation instructions.
