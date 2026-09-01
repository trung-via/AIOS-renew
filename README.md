# AIOS-renew

AIOS-renew provides a thin Human-facing operator above the frozen v0.1 kernel.

## Install

```powershell
pip install -e .
```

Store canonical engineering tasks in the target repository:

```text
.ai/tasks/TASK-101.yaml
```

Inspect or execute a stored task:

```powershell
aios task TASK-101
aios run TASK-101 --executor codex
aios run TASK-101 --executor antigravity
```

Use `--repo PATH` to target a repository other than the current Git repository.

## Canonical remediation

After reviewing a canonical `CHANGES_REQUIRED` finding, a Human authorizes one
normal remediation and selects its sole Executor with no local artifact courier:

```powershell
aios remediate TASK-101 --finding F1 --executor codex
```

AIOS resolves exactly one immutable, contract-valid REVIEW, REMEDIATION, source
RUN/RESULT, reviewed SHA, and any prior-review continuity from the configured Git
remote. Missing, invalid, mismatched, or ambiguous lineage fails before RUN
admission or Executor invocation. Resolution reads Git objects without checking
out review or remediation branches. The resolved artifacts then enter the same
normal REMEDIATION boundary, including canonical scope, affected verification,
Runtime-owned evidence, repository gates, and post-PASS DELTA-review transport.

Callers that deliberately materialize canonical artifacts may retain the explicit
mode (and add `--prior-review` when a DELTA REVIEW requires it):

```powershell
aios remediate TASK-101 --review .ai/reviews/REVIEW-101-001.yaml `
  --remediation .ai/remediations/REMEDIATION-101-001-F1.yaml --executor codex
```

`--finding` cannot be mixed with `--review`, `--remediation`, or
`--prior-review`. In either mode, the command is the Human execution-authorization
boundary and AIOS invokes only the selected Executor, with no retry or fallback.

### Remediation outcome boundaries

A remediation admission failure is a Runtime-owned rejection before a RUN exists.
It means the requested Executor never ran. Runtime keeps one bounded, allowlisted
diagnostic under the repository's Git runtime state and best-effort publishes the
byte-identical artifact under `refs/heads/aios/admission-failure/`. This diagnostic
records operational facts only; it is not RESULT, EVIDENCE, or proof that a finding
was fixed. Repeated byte-identical rejections resolve to the same content-addressed
artifact, and unavailable diagnostic transport never replaces the local admission
error.

A RUN failure occurs only after admission created a RUN and the selected Executor
or a later completion or verification gate failed. It remains represented by the
existing RUN-keyed FAILURE path. A post-PASS review outcome is later still: the RUN
and canonical ResultPackage passed Runtime gates and were transported for semantic
review, which may return PASS or authorize a new narrow REMEDIATION. These three
states are distinct and an admission diagnostic is never treated as a RUN failure
or a review judgment.

### RUN observations

For each newly admitted PRIMARY, REMEDIATION, or REPAIR RUN, Runtime best-effort
stores one immutable `RUN_OBSERVATION` under the repository's Git runtime state.
It binds the exact RUN id, TASK id/revision, operation, selected Executor, and base
SHA. Its terminal kind is only `RESULT` or `FAILURE`: it reports Runtime execution
truth and never predicts or records the later semantic REVIEW outcome.

The sidecar separates three monotonic elapsed durations. Admitted-run elapsed time
runs from persisted RUN admission through Runtime's terminal RESULT or FAILURE.
Native Executor time covers the already-authorized native invocation, including a
timeout, nonzero exit, or invalid output. Runtime verification time covers the
canonical verification attempt on both success and failure. These durations do not
include pre-RUN synchronization or admission work, later semantic review, Human
thinking, queueing, or total Human wait time. They are finite non-negative values
derived from a monotonic clock, not from wall-clock timestamp subtraction.

`executor_invoked` remains an exact fact even when native execution fails. Optional
token counters are recorded only as a complete, exact machine-readable group from
that same native invocation; missing, partial, malformed, or inferred usage remains
unavailable. The observation is operational state, not RESULT, EVIDENCE, acceptance
proof, or review authority. It is not yet used for automatic Executor scoring,
selection, routing, retry, or fallback.

When present, transports place the byte-exact sidecar at
`.ai/transport/observation.json` on the existing success or failure artifact ref.
Historical refs without this optional file remain valid, and `retry-transport`
preserves a locally persisted sidecar. Persistence or publication trouble is
subordinate to the original RESULT or FAILURE and never invokes another Executor.

## Direct Candidate Acceptance

For a committed candidate produced directly by one Human-selected Executor in
response to a canonical `CHANGES_REQUIRED` CODE_FIX finding, accept it with only
the TASK id, finding id, and selected Executor identity:

```powershell
aios accept-candidate TASK-101 --finding F1 --executor codex
```

The Human attests that exactly one Executor held mutation authority while making
the candidate and must leave the candidate committed at the current clean HEAD.
AIOS does not claim a pre-acceptance lease for this mode. From the acceptance
boundary onward, Runtime resolves the REVIEW, REMEDIATION, and authoritative
prior result from the configured Git remote; rejects non-descendant or
out-of-scope candidates before admission; invokes no Executor; and runs only the
canonical affected verification. Successful candidates use the existing
post-PASS review transport for ChatGPT DELTA review. See
`docs/AIOS-RENEW-v0.1.4-DIRECT-CANDIDATE-ACCEPTANCE.md`.

## Native execution capability

The Human supplies only the canonical command arguments shown above. Before the
single selected Executor process starts, AIOS deterministically derives native
capability from the canonical contract: mutation-authorizing PRIMARY,
`CODE_FIX` REMEDIATION, and `CODE_FIX` REPAIR executions receive non-interactive
mutation capability; read-only PRIMARY, `EVIDENCE_ONLY` REMEDIATION, and
`NO_CHANGE` REPAIR executions remain read-only. Permission or capability
failure does not trigger retry, reroute, fallback, or another model invocation.

Native capability is only a process prerequisite. TASK modification scope and
the applicable REMEDIATION or REPAIR scope remain canonical authority, and the
existing Runtime committed-delta, clean-worktree, and HEAD gates reject changes
outside that authority. Every `CODE_FIX` completion path also requires an
advanced HEAD with a non-empty committed delta inside its authorized correction
scope; unchanged states and empty commits fail before affected verification or
post-PASS review transport. `EVIDENCE_ONLY` and `NO_CHANGE` retain their
zero-mutation contracts.

Executors return the structural ResultPackage through their native output;
Runtime persists staging and all canonical operational state after capturing it.
If a later completion gate fails, Runtime revalidates that bounded staging
package and preserves any exact `result.unresolved` strings as structured
`error.executor_diagnostics.unresolved` facts in the canonical FAILURE artifact.
Missing or invalid staging adds no executor diagnostics and never replaces the
original failure. Failure transport publishes that Runtime-authored artifact
without parsing Executor output.
