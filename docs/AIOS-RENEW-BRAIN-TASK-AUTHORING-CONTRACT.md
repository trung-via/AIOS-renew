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

## Direct final TASK mutation boundary after BO-1

AUTHOR_TASK has one production ingress: Runtime validates the final TASK contract and
canonical/provenance bindings directly. Omit `audited_handoff`; supplying that carrier
field, including null, is rejected. Runtime does not require, reconstruct, validate, fingerprint
or freshness-recheck Decision Packets, Stage-1/Stage-2 material, acceptance-phase ledgers
or TASK_AUTHORING audit-support sections as mutation prerequisites.

Final schema/identity/revision validation, the current minimum-sufficient verification
policy, authored affinity preservation, expected-main currentness, unrelated-delta checks
and expected-old-main compare-and-swap remain mandatory. New revision-1 ORIGIN_AFFINE
TASKs independently require admitted `origin_authoring_proof` bound to the exact carrier
attempt, TASK id, expected main, route/generation and envelope digest, with existing HMAC,
freshness, replay and same-attempt idempotence protections. Legacy authoring and revisions
cannot consume a new origin proof; revisions preserve canonical affinity.

Brain must still perform `CONSTRUCT` then `ADVERSARIAL_AUDIT_AND_RECONCILE`. Its support
material is semantic discipline, not canonical engineering truth or Runtime mutation
authority. AUTHOR_REMEDIATION and AUTHOR_REPAIR retain their existing audited handoff,
canonical reconstruction, lineage and freshness contracts. These changes apply upon
publication of BO-1; the bounded transition TASK may use the previously published gate.

## Declare acceptance proof phase in Stage 2 as Brain semantic discipline

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

Brain owns exact final acceptance coverage and the all-`CLAIM_NOW` reconciliation
obligation. BP-4A validates bounded declared shape and fingerprints the semantic material
within the Brain audit procedure. Runtime and ingress never infer phase from acceptance
prose and do not consume the ledger for AUTHOR_TASK mutation. The historical H3 ingress
ledger gate is superseded for AUTHOR_TASK only by the post-BO-1 direct boundary above.

The ledger is transient cognitive support, never lifecycle truth, evidence or a
persistent reasoning record. Do not add it to the TASK candidate or canonical TASK
bytes; frozen acceptance entries still contain only `id` and `condition`.
REMEDIATION_AUTHORING, REPAIR_AUTHORING and other flows retain their existing
contracts and reject this TASK-only material. Identical historical TASK replay
remains non-mutating; new TASK identities and revisions also do not require a Runtime
ledger gate. This contract creates no RUN, execution, verification, verdict,
publication or roadmap advancement.

## Specify verification once

Put every canonical verification command only in `verification.required`. Do not repeat commands as execution instructions in the goal, problem, assumptions, non-goals, or constraints.

After TASK-356 has a Runtime RESULT, independent Reviewer PASS and Publisher publication, every newly authored TASK identity or revision must explicitly declare `verification.policy: minimum-sufficient-v1`. The `required` list must be non-empty and contain deterministic, non-interactive, minimum-sufficient commands in the authored order. Omitting the policy or authoring V2 for a new identity/revision is rejected after activation. Until that activation the published V2 ingress gate still applies; TASK-356 itself remains a V2 bootstrap TASK and is not converted by its implementation.

This prospectively supersedes the historical TASK-317 rule requiring V2 for new authoring. TASK-317 remains a V1 bootstrap TASK. Historical V1/V2 TASK identities, already admitted RUNs and their FAILURE/RESULT/EVIDENCE/REVIEW lineage retain their exact policy, bindings, decisions and immutable raw evidence. Identical same-revision historical TASK replay remains exact, idempotent and non-mutating; it cannot change policy in place. TASK-355 r5 remains V2 and its prior RUN/FAILURE evidence is frozen. Using the V1 default for that objective requires an explicit later TASK revision and a separately admitted PRIMARY. No historical failure is reinterpreted or reopened.

For V1 PRIMARY, Runtime uses the existing direct `execute_verification` path: only the exact nonempty ordered `verification.required` commands run, once in an isolated exact candidate subject, with immutable raw evidence and ordinary stop-on-nonzero. No V2 candidate-only delta, candidate/base collection, comparison, attribution, invented reuse or implicit full-suite work is added. Raw exit 0 is necessary for each exact required command to count as successful. Nonzero, interrupt, missing, malformed or conflicting evidence remains blocking for PRIMARY, REMEDIATION and REPAIR; baseline equivalence cannot waive a V1 nonzero or manufacture PASS. Runtime retains exact command/RUN/subject identity and transport checks, and Reviewer retains semantic acceptance authority.

A REMEDIATION for a policy-bearing TASK must still use that TASK's exact policy under `verification`, with its authorized commands in `verification.affected`. V1 REPAIR retains the existing deterministic overlap normalization when combining already-authorized TASK and origin REMEDIATION lists. Required correction proof cannot be skipped. Runtime alone constructs canonical EVIDENCE; neither authoring nor Executor gains verification authority.

The v1 contract rejects exact duplicates and only those equivalent or subsumed pytest commands whose coverage relationship is mechanically proven by the repository-owned grammar. That grammar keeps `pytest` and `python -m pytest` as separate launcher families and recognizes only quiet output flags, explicit positional test paths, and an optional `-k` narrowing expression. Unsupported, malformed, shell-composed, or ambiguous commands are opaque and are never removed based on similarity.

A recognized full-suite pytest command (one with no path and no `-k` filter) requires a non-empty `full_suite_reason` of at most 512 characters. The field is invalid without such a command. This is structural policy only: Brain and Reviewer authority retain semantic judgment over the command selection and whether the reason is persuasive.

Prefer focused checks that establish the acceptance criteria. Do not add Git cleanliness, HEAD, changed-files, or similar repository-integrity checks by default: Runtime already owns those gates. Full-suite proof is required only when explicitly authored and justified by the TASK semantics; focused success never triggers an automatic additional suite. TASK-356 authorizes exactly its four focused required commands, with coverage limited to those selected files/tests and no repository full suite.

When TASK semantics explicitly require ordinary AIOS-renew full-suite proof on the supported Windows AMD64 self-host class, author the exact command `python scripts/aios_parallel_full_suite.py` in `verification.required` with `full_suite_reason`. The selected profile remains the Human-selected twelve-worker policy in `.ai/verification-profiles.yaml`; its soft performance observation remains subordinate. Brain may author a serial or other full-suite command when the TASK semantics require it, with a reason. Runtime executes the authored command exactly; it does not substitute full-suite profiles or add that command to focused work. Historical TASK commands remain immutable.

### Historical and already admitted minimum-sufficient-v2 Runtime boundary

The following V2 rules remain unchanged only for historical or already admitted V2 work; they do not apply to the prospective V1 default. Brain owns the semantic envelope (what must be proven). V2 absorbs attributed and deterministic delta verification; neither capability is a separate verification authority. V2 retains every exact authored requirement, removes exact duplicates when combining sources, and puts authored correction commands before the TASK baseline. `derive_minimum_verification` consumes only the authored baseline, exact base/candidate commits, committed changed files, complete canonical failed identities, admitted correction `modification_scope`, and bound Runtime evidence. It returns ordered new executions, explicit reused proof, covered exact correction probes and broader-fallback records. Changed paths never imply a semantic dependency graph. In this implementation cross-candidate reuse conservatively requires equality of the entire tracked tree; changes to any tracked file rerun the broader requirement. Missing, malformed, conflicting or stale material cannot discharge a requirement.

Canonical observations preserve the full exact nodeid/phase population, including passing phases needed for comparison and collection failures. Parameter text remains intact; only file-path separators are normalized. The separate Human display retains at most 20 identities and clips display nodeids at 240 characters. Canonical bounds are 100,000 phase reports, 10,000 failed identities, 16,384 characters per nodeid and failure detail, and 16 MiB per canonical JSON object. Exceeding a canonical bound fails closed with an explicit incomplete/unstable observation; display truncation never becomes identity truth. Every comparable failure binds the selected profile, toolchain (including interpreter, platform, pytest, xdist and installed distribution inventory), and the complete bounded detail plus its deterministic SHA-256 fingerprint. Only the exact checkout root is normalized in detail; volatile or divergent values remain observable. Early-stop populations and profile/toolchain changes during execution are incomplete or unstable. Runtime preserves exported raw profile observations even when collection or conformance errors stop the suite wrapper before its envelope is emitted.

Attribution compares exact phase observations under equal command, profile and toolchain, with Runtime-bound environment, repository verification-tool/observer and selected-policy identities. Exact base PASS/candidate FAIL is `CANDIDATE_REGRESSION`. Exact equivalent base FAIL/candidate FAIL is `PRE_EXISTING_BASELINE`. Candidate-only, skipped, unstable, incomplete, non-comparable or fingerprint-divergent observations are `UNRESOLVED`. Matching nodeid alone proves nothing. Candidate regressions and unresolved observations remain blocking.

Raw command output and nonzero exit codes are preserved. A distinct task-level outcome is recomputed from bound observations: exit 1 can be nonblocking only when a nonempty complete failure population is entirely `PRE_EXISTING_BASELINE`. Collection/internal errors, worker crashes, interrupts, missing failures and failed conformance remain blocking. An authored full-suite guard remains required for PRIMARY, REMEDIATION and REPAIR, even after an affected probe fails. Only still-valid evidence can discharge that same requirement without another execution.

Evidence binds subject, base, complete tracked tree, exact command, profile, toolchain, authored envelope/correction scope, committed delta and canonical failure set. Reuse additionally checks immutable raw-output digests, preserves the original observation subject and records its explicit current-subject binding. Missing raw data or changes to any relevant binding invalidate reuse. Deterministic plan records distinguish `REUSED` from `EXECUTED`; base observations are reused only with their exact conditions and raw bindings. Conflicting repeated observations fail closed.

For REMEDIATION and REPAIR, an exact failed-test probe may run first only when its canonical nodeids belong to explicitly authorized correction test files and are mechanically covered by an authored command. The admitted reviewed/failed subject and its committed correction delta determine that scope check; they do not replace the RUN's original attribution base or turn an earlier candidate regression into baseline debt. Parameterized identities are passed intact. This probe never discharges an unproven broader requirement. Proven baseline failures never add files to modification_scope or select a correction. V2 has no planning, semantic review, correction selection, publication, retry/failover or roadmap authority. RUN-316-002 remains frozen; TASK-317 authorizes no REPAIR-316-002 and changes none of its artifacts.

TASK-356 changes no frozen Kernel clause, historical KA-01 amendment or separately opted-in semantic proof design. The Brain semantic audit profile `aios-adaptive-brain-audit-v1` remains distinct from Runtime V1/V2 verification policy. Runtime still owns canonical verification and transport, Reviewer semantic judgment and Publisher exact eligible source publication. No default change authorizes execution, automatic correction, publication or roadmap mutation.

Do not instruct the Executor to push. Executor implementation ends at the permitted final local commit; synchronization and publication remain outside Brain-authored implementation instructions.
