# AIOS-renew

AIOS-renew provides a thin Human-facing operator above the frozen v0.1 kernel.

## Governance

AIOS operates under canonical governance:
- [AIOS Manifesto](docs/AIOS-MANIFESTO.md) defines core purpose, optimization philosophy, and the North Star metric (`Verified Useful Work / (Time + Tokens + Human Effort)`).
- [AIOS Constitution](docs/AIOS-CONSTITUTION.md) defines the non-negotiable constitutional principles and authority hierarchy.

Operational surfaces, transport mechanisms, Git refs, and handoffs are subordinate, replaceable mechanisms that serve canonical state rather than sources of constitutional authority.


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

## Remote Wakeup (GitHub Actions Self-Hosted)

A1 provides a thin repository-native GitHub Actions wakeup (`.github/workflows/aios-self-hosted-wakeup.yml`) so an authorized remote Human or Brain can start one canonical PRIMARY execution on the designated Windows self-hosted runner without being physically present at the execution machine. A2 adds one stable outer `dispatch_id` so re-delivery cannot start that PRIMARY execution again.

GitHub Actions calls the Human-facing outer surface (`aios wakeup <DISPATCH_ID> <TASK_ID> --executor <EXECUTOR> --repo <AIOS_REPO_ROOT>`), which durably journals the delivery and delegates a new request to the same existing PRIMARY implementation used by `aios run`. It does not replace AIOS Operator authority. Admission, pre-admission synchronization (TASK-062), RUN allocation, mutation authority, verification, and completion truth remain owned by the invoked AIOS process. Direct local `aios run` behavior is unchanged.

### One-Time Host Prerequisites

Self-hosted runner registration and host configuration are operational setup, not Executor implementation work or canonical AIOS authority:
1. **Register Runner**: Register one Windows x64 self-hosted runner for this repository with custom label `aios-renew` (targeting `[self-hosted, windows, x64, aios-renew]`).
2. **Environment & Toolchain**: Run the runner under an account and environment that has the working AIOS/Codex/Antigravity toolchain:
   - `aios` CLI available on `PATH`;
   - Native coding Executors (`codex`, `antigravity`) installed and authenticated;
   - Python environment with repository dependencies installed (`pip install -e .`).
3. **Repository Variable `AIOS_REPO_ROOT`**: Configure the non-secret repository variable `AIOS_REPO_ROOT` in GitHub repository settings (Settings > Secrets and variables > Actions > Variables) pointing to the persistent canonical checkout (e.g. `C:\TOOL\Projects\AIOS-renew`). The workflow does not checkout code or create fresh worktrees; it executes against this persistent checkout.
4. **Existing Git Credentials**: Ensure the persistent repository already has the non-interactive Git credentials required for existing AIOS transport (e.g. Git credential manager, SSH key, or stored credentials for `origin`). GitHub Actions runs with minimum read-only permissions and injects no write token, PAT, deploy key, or secret into the AIOS or Executor process.

### Triggering Remote Wakeup

An authorized remote Human or Brain must generate one stable `dispatch_id` for the intended delivery before triggering the workflow. Use 1–128 ASCII letters, digits, `_`, or `-`, starting with a letter or digit (a UUID without braces is suitable). Reuse that exact value with the same `task_id` and `executor` for every API retry, workflow re-delivery, or manual resubmission of the same request. Use a new value only for a genuinely new requested execution.

Trigger through GitHub's workflow-dispatch surfaces:
- **Web UI**: Navigate to Actions > "AIOS self-hosted primary wakeup" > "Run workflow", enter the stable `dispatch_id`, enter the `task_id` (e.g. `TASK-068`), and select the `executor` (`codex` or `antigravity`).
- **GitHub CLI (`gh`)**:
  ```powershell
  gh workflow run aios-self-hosted-wakeup.yml -f dispatch_id=4f53b1a8-8491-42c7-baba-62f9c3816f5c -f task_id=TASK-068 -f executor=antigravity
  ```
- **GitHub REST API**: `POST /repos/{owner}/{repo}/actions/workflows/aios-self-hosted-wakeup.yml/dispatches` with `ref` and `inputs` containing exactly `dispatch_id`, `task_id`, and `executor`.

Before the first PRIMARY call, A2 stores a path-safe, hashed dispatch record under repository-local `.git/aios/dispatches`. This is operational control/telemetry state outside the product worktree and canonical artifact schemas. It provides delivery and RUN attribution only; it is not TASK truth, RESULT/EVIDENCE, review input, publication proof, or semantic completion truth. The separate A3 status surface can observe this record but cannot reconcile or mutate it.

A duplicate terminal delivery performs no re-execution. A successful dispatch returns the same dispatch/RUN attribution with success; a failed dispatch preserves its prior nonzero outcome. Reusing a `dispatch_id` with a different TASK or Executor fails closed. After a process or host interruption, re-delivery only observes the recorded pre-invocation RUN namespace and canonical `.git/aios` RUN/RESULT/FAILURE state. It can link one uniquely attributable terminal RUN, report an execution still in progress, or return reconciliation blocked. This is attribution and no re-execution, not automatic retry or recovery: it never starts a second RUN, invokes an Executor again, repairs an incomplete RUN, or synthesizes terminal artifacts.

### Public Repository Security Boundary

Because AIOS-renew is a public repository and self-hosted runners run on a host with privileged local development tooling and authenticated credentials:
- The wakeup workflow trigger is strictly `workflow_dispatch` only. It never triggers on `pull_request`, `push`, `issue_comment`, `schedule`, `repository_dispatch`, or any untrusted code-change event.
- The workflow never checks out or runs an event-controlled ref.
- `dispatch_id`, `task_id`, and `executor` enter PowerShell only through environment data bindings. The bounded dispatch id is hashed for journal filenames and never becomes a path, Git ref, command, or authority token.
- The runner should be a dedicated repository runner labeled `aios-renew` rather than shared with unrelated or untrusted public repositories.


## Bounded Remote Status and Human Approval

A3 adds two separate manual GitHub Actions surfaces on the same dedicated
self-hosted runner. They do not extend the wakeup workflow and do not provide a
generic operation selector:

- `.github/workflows/aios-remote-status.yml` accepts only an existing A2
  `dispatch_id`. It reads the hashed repository-local dispatch record and reports
  an allowlisted request binding (`dispatch`, `task`, and `executor`), its stored
  dispatch status, its attributed RUN when present, and one bounded observed RUN
  classification. The classification distinguishes no attribution, missing RUN
  state, in-progress or incomplete state, RESULT, FAILURE, and conflicting terminal
  artifacts. Status does not reconcile or rewrite the dispatch, read raw logs, or
  expose prompts, credentials, environment values, arbitrary files, or local paths.
- `.github/workflows/aios-remote-approval.yml` accepts exactly `source_run_id` and
  `finding_id`. The approver is derived from the trusted `github.actor` event
  context; TASK identity/revision, REVIEW identity, action, reviewed RESULT SHA,
  remediation ref, and remediation commit SHA are derived from canonical lineage.
  The command requires exactly one current, contract-valid `CHANGES_REQUIRED`
  REVIEW/finding/REMEDIATION lineage for that source RUN and exact current remote
  remediation ref. Same-name findings under another source RUN are not candidates.

Approval is stored only in path-safe, content-addressed `.git/aios/approvals`
operational state. It binds the Human attribution and the exact immutable
remediation commit SHA as well as the source RUN/TASK/review/finding/action/reviewed
SHA. Repeating the identical approval is idempotent. If the canonical remediation
ref later moves to a different commit, the earlier SHA-bound record is stale for
that ref and cannot authorize the changed content; a new Human approval is required.

Both workflows are `workflow_dispatch`-only, request only `contents: read`, run only
on `[self-hosted, windows, x64, aios-renew]`, use the fixed `AIOS_REPO_ROOT`
repository variable, perform no checkout, and pass workflow/event values through
environment data bindings. Neither status nor approval invokes PRIMARY, a coding
Executor, verification, reconciliation writes, remediation, repair, recovery,
transport, retry, or publication. In particular, approval records authorization
only: it does not choose an Executor, create an approve-and-run path, or execute the
correction.

### Approved Remote Correction Wakeup

A6 adds a second, separately dispatched step after A3 approval. First, a Human
records exact approval with `aios-remote-approval.yml`. Later, an authorized Human
or Brain triggers `.github/workflows/aios-approved-remediation-wakeup.yml` with
only a stable `correction_dispatch_id`, that same `source_run_id` and `finding_id`,
and an explicit `codex` or `antigravity` Executor. The workflow delegates once to:

```powershell
aios approved-remediation-wakeup DELIVERY_ID RUN-101-001 F1 --executor codex
```

The wakeup re-resolves canonical lineage and requires the already-persisted A3
approval to match the current remediation ref commit exactly. A missing,
malformed, conflicting, or older SHA-bound approval fails before a REMEDIATION RUN
or coding Executor exists. Approval remains reusable Human authority and is not
mutated into a consumed artifact; the correction dispatch journal is the separate
delivery identity.

Before calling the existing `aios remediate` implementation, A6 stores a hashed,
path-safe record under `.git/aios/correction-dispatches`. It binds the delivery,
source RUN, finding, explicit Executor, exact approved remediation ref/SHA, and the
pre-invocation REMEDIATION RUN namespace. Re-deliveries must reuse the stable id
with the identical binding. A terminal re-delivery returns its stored RUN/outcome
without another remediation call, Runtime verification, RUN, or Executor. Reusing
the id with any changed bound value is an identity collision, never an Executor
reroute.

After a host/process interruption, re-delivery never infers or claims an unbound
later REMEDIATION RUN, even when that RUN otherwise matches the correction. Until
exact dispatch-to-RUN ownership is already durably recorded, re-delivery remains
`RECONCILIATION_BLOCKED`/in-progress and does not retry, resume, repair, steal a
lock, or invoke the Executor. Once that ownership is recorded, re-delivery may
reconcile only the exact bound RUN and its existing RESULT or FAILURE. A delegated
canonical pre-RUN rejection remains an Admission Failure v2 diagnostic; failure
after RUN creation remains only ordinary RUN-keyed FAILURE. The correction journal
is operational attribution, not TASK/RUN schema, RESULT, EVIDENCE, REVIEW truth,
approval, publication proof, or authority for semantic DELTA review/publication.

The A6 workflow retains the dedicated self-hosted security boundary used by A1/A3:
manual `workflow_dispatch` only, `[self-hosted, windows, x64, aios-renew]`, the
configured persistent `AIOS_REPO_ROOT`, read-only GitHub contents permission, no
checkout, and event values passed as command data. It introduces no GitHub write
credential, scheduler, queue, retry, router, fallback, or model call.


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

### Optional Correction Preflight

Before authorizing execution, a Human or outer automation may inspect either
canonical correction family explicitly:

```powershell
aios preflight-remediation TASK-101 --finding F1
aios preflight-repair RUN-101-001
```

The optional commands return one versioned `AIOS_CORRECTION_PREFLIGHT` JSON
observation with `READY` or `BLOCKED`, the exact allowlisted lineage identities
known at that boundary, current or historical subject mode when known, and a
bounded phase/reason code. They reuse the deterministic REMEDIATION and REPAIR
lineage, contract, reusable-state, repository, historical-subject, and canonical
RUN-namespace admission checks. A `NO_CHANGE` REPAIR inspects eligible reusable
candidate state; a `CODE_FIX` REPAIR bypasses those reuse-only checks.
For REPAIR, `executor_required` is true unless that exact admission proves an
eligible verification-only reusable candidate; blocked observations report it as
unknown.

Preflight is read-only: it creates no RUN, lease, RESULT, FAILURE, EVIDENCE,
Admission Failure v2 record, repair/remediation/dispatch state, or Executor
invocation, and it runs no canonical verification. `BLOCKED` is an observation,
not an admission failure or execution attempt. `READY` is informative only; it is
not a reservation, lease, authorization, or cached proof, and the later
`aios remediate` or `aios repair` command performs normal admission again against
then-current state.

Correction Preflight does not author or choose a correction, choose or recommend
an Executor, execute, verify, review, publish, retry, recover, reroute, select a
next action, or advance roadmap state. Focused Operator and approved-remediation
wakeup regressions, together with the final whitespace check, remain:

```powershell
python -m pytest tests/test_operator.py tests/test_remediation_wakeup.py tests/test_correction_dispatch.py -q
git diff --check
```

### Unified State + Next Action

`aios state TASK-101` returns one versioned `AIOS_UNIFIED_STATE` JSON observation
for the exact TASK revision stored in the current control repository. It derives
state from repository-local admitted RUN facts and an isolated snapshot of the
canonical remote lineage. The observation does not create Runtime state, fetch
objects into the control repository, invoke an Executor or verification, retry
transport, recover a conflict, author a correction, review, integrate, or publish.

`next_action` is restricted to `EXECUTE_PRIMARY`, `WAIT`, `SEMANTIC_REVIEW`,
`AUTHOR_REMEDIATION`, `EXECUTE_REMEDIATION`, `AUTHOR_REPAIR`, `EXECUTE_REPAIR`,
`RETRY_TRANSPORT`, `RECOVER_PRIMARY`, `PUBLICATION`, `DONE`, and `NONE`.
Execution, correction, transport, and recovery actions identify a boundary that
still requires Human-authorized Runtime entry; `SEMANTIC_REVIEW` belongs to the
Reviewer; correction authoring belongs to the Brain; and `PUBLICATION` belongs to
the separate safe-publication boundary. `WAIT`, `DONE`, and `NONE` grant no
mutation authority.

The reducer follows exact TASK revision, RUN, terminal, reviewed/failed SHA, and
correction-continuation identities. It never chooses by timestamps, directory
order, or largest RUN number. Competing tips, decisions, findings or corrections,
malformed lineage, contradictory terminal facts, and divergent publication state
fail closed as a bounded `BLOCKED` observation with `next_action=NONE`.
Historical Admission Failure v2 records are allowlisted forensic context only;
their error prose is neither parsed nor allowed to override currently observable
lifecycle state. REMEDIATION and REPAIR readiness consumes the existing Correction
Preflight boundary rather than approximating its admission rules. Unified State is
a derived observation, not a planner or router, and provides no Executor/model
recommendation.

### Unified Human continuation

`aios continue <TASK_ID>` is the optional Human-facing front door over that exact
Unified State reducer. It emits one bounded JSON object with
`format=AIOS_HUMAN_SURFACE` and `version=1`. The result binds the TASK revision,
observed `next_action`, disposition and authority, exact already-observed selector
identities, Executor requirement/supply facts, and any RUN/head identity returned
by the delegated operation. It never includes prompts, Executor output, logs,
credentials, environment dumps, arbitrary paths, Git output, or artifact bodies.

For a coding action the Human must choose explicitly:

```powershell
aios continue TASK-101 --executor codex
aios continue TASK-101 --executor antigravity --repo C:\path\to\control-repo
```

There is no default, remembered, inferred, ranked, or recommended Executor.
`--executor` is required for PRIMARY, REMEDIATION, and CODE_FIX or ordinary
Executor-backed REPAIR, including a `NO_CHANGE` authorization for which canonical
REPAIR preflight finds no eligible reusable candidate. Only a deterministically
eligible TASK-064 verification-only `NO_CHANGE` REPAIR may continue without it:
the existing REPAIR admission result must explicitly prove that no Executor is
required. Supplying an Executor does not force a coding call on that path. When it
is omitted, the continuation RUN preserves the failed candidate's Executor label
as frozen lineage metadata; the Human result reports no supplied Executor and the
RUN observation records that none was invoked. An Executor argument on transport,
recovery, handoff, blocked, wait, or done state grants no additional authority.

The command delegates at most one existing canonical operation and then stops:

| Unified `next_action` | One invocation does |
| --- | --- |
| `EXECUTE_PRIMARY` | Existing PRIMARY admission, synchronization, execution, Runtime verification, and result/failure semantics |
| `EXECUTE_REMEDIATION` | Existing REMEDIATION bound to the observed source RUN, finding, and remediation SHA |
| `EXECUTE_REPAIR` | Existing REPAIR bound to the observed failed RUN and repair SHA |
| `RETRY_TRANSPORT` | Existing terminal transport retry for the observed RUN |
| `RECOVER_PRIMARY` | Existing exact PRIMARY recovery for the observed conflicting RUN |
| `SEMANTIC_REVIEW` | `EXTERNAL_AUTHORITY_REQUIRED` for the Reviewer |
| `AUTHOR_REMEDIATION`, `AUTHOR_REPAIR` | `EXTERNAL_AUTHORITY_REQUIRED` for the Brain |
| `PUBLICATION` | `EXTERNAL_AUTHORITY_REQUIRED` for the Publisher |
| `WAIT`, `DONE` | `NO_ACTION` |
| `NONE` / BLOCKED | `BLOCKED` with the reducer's bounded blocker |

The state observation is not cached admission authority. Every executable branch
re-enters its existing admission/revalidation boundary. If a remediation or repair
selector moves between observation and admission, continuation fails closed before
a RUN instead of executing changed correction content. PRIMARY keeps its safe
sync/restart behavior; a synchronized restart re-enters `aios continue` and derives
Unified State again before any operation begins.

If that one delegated operation rejects before RUN admission or fails after a RUN
was admitted, `aios continue` still emits one `AIOS_HUMAN_SURFACE` result and exits
nonzero. Its bounded `DELEGATED_OPERATION_FAILED` blocker is only a Human-surface
outcome: the existing Admission Failure v2 or RUN FAILURE remains the sole failure
artifact and authority. Continuation does not retry, reroute, re-observe, create a
second failure record, or start another operation.

One invocation does not observe again after delegation and does not automatically
review, author a correction, publish, retry, recover, reroute, poll, or execute the
newly derived next action. Pre-RUN rejection remains Admission Failure v2; an
admitted failure remains the ordinary RUN FAILURE. The Human result does not create
another failure artifact, RESULT, or EVIDENCE, and it never attempts a second run.

`aios state <TASK_ID>` remains the read-only inspection surface. `aios continue`
does not perform planning, semantic review, correction authoring, publication,
downstream migration, roadmap mutation, autonomous workflow, or any change to the
syntax and authority of the existing low-level commands.

### Admission Failure v2 and outcome boundaries

Admission Failure v2 covers every execution-capable pre-RUN boundary: PRIMARY
(including TASK-062 synchronization and A2 wakeup preflight), REMEDIATION, REPAIR,
direct-candidate acceptance, and PRIMARY collision recovery. A v2 record carries
the `AIOS_ADMISSION_FAILURE` format marker and version 2, a bounded operation,
deterministic admission phase and reason code, and only allowlisted identities that
were authoritatively known at rejection time. When canonical remote refs were
already observed, the record binds their exact commit identity or a deterministic
digest of the immutable task-scoped snapshot. Remote transport unavailability is
kept distinct from a successful query that found missing or invalid canonical
state.

An admission failure is a Runtime-owned rejection before a RUN exists. Its
`executor_invoked=false` fact means no Executor ran; recovery-primary has no
requested Executor field because that boundary selects none. Runtime keeps one
bounded diagnostic under the repository's Git runtime state and best-effort
publishes the byte-identical artifact under
`refs/heads/aios/admission-failure/`. Repeated byte-identical rejections reuse the
same content-addressed identity, while changed bounded observations create a new
immutable record. Diagnostic persistence or transport failure never replaces the
original admission error.

Admission diagnostics are operational forensic facts only. They are not RESULT,
EVIDENCE, semantic review, proof that a finding was fixed, automatic recovery
instructions, or authority to retry, reroute, or invoke an Executor.

A RUN failure occurs only after admission created a RUN and the selected Executor
or a later completion or verification gate failed. It remains represented by the
existing RUN-keyed FAILURE path. A post-PASS review outcome is later still: the RUN
and canonical ResultPackage passed Runtime gates and were transported for semantic
review, which may return PASS or authorize a new narrow REMEDIATION. These three
states are distinct and an admission diagnostic is never treated as a RUN failure
or a review judgment.

### Canonical RUN namespace and PRIMARY collision recovery

Before admitting a new PRIMARY, REMEDIATION, direct-candidate, or REPAIR RUN,
the Operator combines repository-local RUN state with the configured remote's
canonical success and failure artifact refs for that TASK. Remote terminal RUN
identities are reserved even in a fresh checkout with no local Runtime state.
Malformed or cross-TASK terminal refs fail closed. If one RUN id has both success
and failure terminal artifacts, normal admission stops before RUN persistence or
Executor invocation and reports the conflicting identity; neither historical
artifact is preferred or changed.

For an already-observed conflicting PRIMARY identity, a Human may request the one
narrow recovery boundary:

```powershell
aios recover-primary RUN-070-001
```

The command accepts only the conflicting RUN id and optional `--repo`. It resolves
the exact failure artifact, successful artifact, and successful candidate ref from
the canonical remote, validates their shared TASK/revision/base and the successful
ResultPackage, and reserves the complete remote TASK RUN namespace. It runs current
control-plane code against an isolated worktree at the immutable historical
candidate, leaving current main, its index, and worktree unchanged. Recovery
allocates a fresh RUN id, invokes no Executor or model, discards source evidence
attribution, and executes the historical TASK verification list once so Runtime
can emit fresh evidence for the new RUN. The resulting ordinary PRIMARY lineage is
reviewable through the existing review path; recovery does not review, publish,
merge, rebase, cherry-pick, or otherwise integrate the candidate.

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
token counters (`input_tokens`, `cached_input_tokens`, `output_tokens`) measure
only the native coding Executor invocation; they reflect an Executor-only token
domain and do not represent total task cost when Runtime verification commands or
application code perform separate model/provider API calls (which require separate
instrumentation rather than being merged into `RUN_OBSERVATION`). Token counters are
recorded only as a complete, exact machine-readable group from that same native
invocation; missing, partial, malformed, negative, boolean, conflicting, or inferred
usage fails soft to unavailable (`null`) without changing the authoritative native
execution outcome or canonical ResultPackage. Historical `RUN_OBSERVATION` sidecars
with `token_usage=null` remain fully valid and compatible without migration or
backfill. The observation is operational state, not RESULT, EVIDENCE, acceptance
proof, or review authority. It is not used for automatic Executor scoring,
selection, routing, retry, or fallback.

When present, transports place the byte-exact sidecar at
`.ai/transport/observation.json` on the existing success or failure artifact ref.
Historical refs without this optional file remain valid, and `retry-transport`
preserves a locally persisted sidecar. Persistence or publication trouble is
subordinate to the original RESULT or FAILURE and never invokes another Executor.

### Verification-only NO_CHANGE continuation

After a structural package has passed Runtime's structure, HEAD, clean worktree,
changed-files, scope, completion, and applicable REPAIR mutation gates, Runtime
stores a minimum pre-verification candidate snapshot. This snapshot is subordinate
operational state keyed to the exact RUN, TASK revision, and subject SHA. It is not
RESULT, EVIDENCE, an acceptance claim, a review input, or Reviewer authority. A
failure before those gates creates no reusable candidate authority.

When canonical verification then fails, the byte-exact optional snapshot travels
with RUN, FAILURE, optional REPAIR lineage, and optional RUN_OBSERVATION on the
existing failure-artifact ref as
`.ai/transport/pre-verification-candidate.json`. Historical refs without it remain
valid. A present snapshot must bind and validate exactly; malformed, conflicting,
mismatched, dirty, non-repairable, or otherwise ineligible state cannot authorize
reuse.

An explicitly Human/Brain-authorized `NO_CHANGE` REPAIR with empty modification
scope may reuse an exact clean verification-failure candidate. Runtime still
creates the normal continuation RUN and REPAIR lineage and executes the original
TASK canonical verification commands exactly once, but invokes no native coding
Executor. Its RUN_OBSERVATION truthfully records `executor_invoked=false`, no
Executor duration, and Runtime verification duration. Success retains normal
canonical RESULT/EVIDENCE and review readiness; another verification failure
creates one normal immutable FAILURE continuation without automatic retry.

A change in credentials, quota, network, provider, service, or another external
verification dependency may justify a separately authorized verification attempt.
When code is unchanged, that does not justify invoking a coding Executor merely to
restate the same candidate. Runtime does not infer that external state changed,
probe it, or authorize a retry.

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
