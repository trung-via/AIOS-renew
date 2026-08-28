# AIOS-renew v0.1.3 Architecture Amendment

**Status:** Candidate amendment to the frozen v0.1 architecture  
**Scope:** Runtime-owned deterministic canonical verification and executor-adapter isolation

This amendment records the v0.1.3 execution boundary. It does not replace or edit the frozen v0.1 specification, broaden the architecture, or alter the authority of the Brain, Executor, or Reviewer.

## Authority and responsibility

- The Brain owns **WHAT**: the canonical TASK or narrow REMEDIATION contract.
- Exactly one active Executor owns **HOW** and the final implementation commit. Codex and Antigravity implement the same semantic contract and return the same structural ResultPackage contract to Runtime.
- An Executor may perform implementation-local self-checks. It does not receive or execute canonical TASK verification commands or REMEDIATION affected-verification commands, and it does not construct canonical verification EVIDENCE.
- Executor authors semantic claims. Deterministic Runtime mechanically executes canonical verification and captures or generates the canonical EVIDENCE bound to the committed subject SHA.
- Runtime's verifier is deterministic software, not a semantic Verifier Agent. It does not judge whether a claim is semantically adequate.
- ChatGPT Reviewer alone judges the semantic adequacy of claims against the TASK, or against the authorized remediation delta, using the canonical evidence.

## Deterministic Runtime verification

For PRIMARY execution, Runtime excludes canonical TASK verification strings from executor-facing input. After structural output and repository gates pass, Runtime executes the TASK's required verification commands exactly as specified and constructs deterministic canonical EVIDENCE.

For REMEDIATION execution, Runtime likewise excludes affected-verification strings from executor-facing input. It executes affected verification only; it does not repeat unaffected verification.

Runtime preserves deterministic evidence identifiers, exact command behavior, raw-output handling, evidence-to-run and evidence-to-subject binding, and canonical ResultPackage persistence. Successful verification is followed by HEAD and clean-worktree revalidation. Existing v0.1.2 canonical persisted ResultPackages remain valid compatibility inputs.

## Executor adapter isolation

Executor-specific operational differences remain confined to thin executor adapters. In particular, Antigravity's structural ResultPackage normalization—including compatibility normalization of a singleton `satisfies` string—belongs only in the Antigravity adapter. The shared operator remains executor-neutral deterministic coordination.

Antigravity structural output remains staged outside the canonical `.git/aios/results` store. Empty root evidence and empty claim-evidence references are valid only at this pre-verification structural stage. Canonical persisted ResultPackage validation is unchanged.

## Preserved laws and semantics

v0.1.3 preserves Minimum Necessary Context, One Active Executor, Immutable State Binding, No Redundant Work, Deterministic Coordination Before AI Reasoning, and every other core AIOS-renew law.

It also preserves PRIMARY upstream synchronization; one-active-executor locking; scope and changed-files enforcement; REMEDIATION no-sync `reviewed_sha` lineage; post-verification repository checks; and all Git binding, staging, remediation, retry, and publication semantics. There is no retry, reroute, fallback, automatic recovery, additional planner, semantic verifier agent, reviewer, router, or execution layer.
