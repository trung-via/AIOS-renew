# Lean VP L3-03: bounded factual checkpoint feedback preparation

This prospective, explicit API belongs to the **same existing RuntimeCompletion**
that owns L3-02 checkpoint and terminal effects:

```python
request = FactualFeedbackRequest(1, checkpoint_request, checkpoint.digest)
feedback = completion.prepare_feedback(request)
```

It prepares inert factual F_i for the latest immutable K_i of one already
admitted ACTIVE RUN. It executes no proof, selects no command, changes no
candidate and grants no continuation permission. No default completion,
minimum-sufficient-v1/V2 policy, worker, launcher, transport or workflow calls
this seam. KA-01 v1.2 remains **NOT_ACTIVATED**.

## Native authority before preparation

The producer accepts only the exact typed version-1 request. The closed request
contains an existing `ProofCheckpointRequest` locator and its expected digest;
there is no caller outcome, evidence witness, prepared feedback, destination,
command addition, patch, strategy, consent or permission input. Parsing either
request or result authenticates nothing.

`RuntimeCompletion._authenticate_checkpoint` independently reopens the existing
native RUN, exact original TASK/revision/base and PRIMARY admission, committed
clean HEAD/tree, current remote admission/main/selection/review roots, complete
L3-01 reviewed semantic mapping and original L3-02 evidence/raw/native observer.
The exact latest ordinal, predecessor chain, original mandatory and execution
populations and resource history must still match. Authentication rechecks
those roots and observations after its reads, and is repeated at the feedback
effect boundary and after the immutable write. A parsed checkpoint, copied
digest, narrow failing nodeid, raw nonzero or caller PASS/FAIL supplies none of
this authority. Missing, stale, substituted, unreadable, contradictory or moving
roots yield a fixed bounded BLOCKED result before a new feedback effect.

Only PRIMARY has a positive path. REPAIR and REMEDIATION return
`BLOCKED / CORRECTION_NATIVE_AUTHORITY_NOT_ESTABLISHED` with no asserted binding,
proof or durable write, even with exact-looking correction locators. Future
positive correction support must independently authenticate the operation's
native predecessor FAILURE or CHANGES_REQUIRED REVIEW, unique current
AUTHOR_REPAIR/AUTHOR_REMEDIATION action/authorization/scope, selected outstanding
finding(s), original and affected verification, review continuity and sticky
own-RUN source. PRIMARY-shaped evidence cannot supply those facts. Unsupported
operations also return a fixed BLOCKED result.

## Factual outcomes and the full original population

The version-1 `AIOS_L3_FACTUAL_FEEDBACK` result has these outcomes:

| Status | Authenticated basis | Durable effect |
| --- | --- | --- |
| PREPARED | All original mapped candidate proofs complete and stable; every proof is decisive PASS/FAIL, with at least one native call FAIL | One immutable subordinate F_i |
| NO_FAILED_PROOF | Complete stable native PASS for every original mapped proof | None |
| BLOCKED, NATIVE_PROOF_UNRESOLVED | Authenticated checkpoint with ERROR/UNKNOWN in the full population | None |
| BLOCKED, other fixed reason | Native authority/currentness or supported operation not established | None; no invented factual binding |

L3-02's original `checkpoint_outcome` remains visible for each proof. L3-03
adds a conservative factual `outcome`: decisive FAIL requires actual call-phase
failure with passing setup/teardown and no skipped phases, as well as complete
stable native collection/process/worker observations authenticated by L3-02.
Fixture/teardown failures and nonstandard process exits are ERROR; skipped or
otherwise nondecisive observations are UNKNOWN. Exit 1 alone cannot establish a
candidate failure. A FAIL alongside ERROR/UNKNOWN yields BLOCKED, retaining
each distinct fact. Incomplete, unstable/flaky or inconsistent original
observations cannot acquire an authenticated L3-02 seal and therefore cannot
be promoted to actionable feedback. No base/comparison obligation can enter
this PRIMARY candidate-only seam; the original BR gates remain intact.

Every original obligation remains visible, including unaffected PASS and
outstanding FAIL/ERROR/UNKNOWN. Shared proof retains each separate obligation
and claim identity. Integration, worker, ordering, repetition and all other
conditions remain pinned through their original obligation, proof, condition,
provenance, mapping and checkpoint digests. Exact acceptance associations and
separate affected/outstanding obligation and acceptance sets are retained.
An acceptance may have a passing obligation and still appear outstanding due
to another required obligation; no per-acceptance PASS is inferred.

## Closed bounded privacy-safe result

The binding includes TASK/revision/RUN/PRIMARY/base, candidate commit/tree,
admitted RUN digest and original Executor, TASK envelope digest, contract and
mapping pins, reviewed source-authorization digest, plan/population/execution
digests, and exact checkpoint ordinal/predecessor/current digest. The feedback
identity is SHA-256 of that canonical binding; its content digest covers all
result bytes. The authorization digest retains the exact original native
admission and reviewed source authority; it creates no authorization.

Per-proof facts retain original proof/unit, EVIDENCE ID/digest, source RUN and
subject, authored source-command digest, observation/manifest/raw/native
digests and raw/native sizes. Capture references are synthesized relative
locators under `l3-02/kNNN/`, preserving original capture identity without
exposing local absolute paths. Per-obligation rows retain stable ID/revision,
claim/condition/provenance digests, kind/blocking flag, original acceptance IDs,
proof association and typed outcome/completeness/stability.

No raw logs, failure-detail prose, nodeids, claim text, commands, private paths,
implementation instructions, patch proposals, test weakening or repair choices
are copied out. Exact original subjects and captures stay untouched. Strict
codecs reject extra/missing/duplicate fields, alternate encodings, floats,
unsupported stages/outcomes, instruction/permission fields and contradictory
projections. Counts are at most 256 proofs/obligations/IDs; IDs have bounded
ASCII identity syntax, references have exact generated shapes, and the shared
codec enforces 2 MiB, depth 24 and 200,000-node ceilings.

Only the original checkpoint's known cumulative counters are reported:
checkpoints, authored executions, captured raw/native bytes and preseal elapsed
milliseconds. Preparation increments none of these and asserts no complete
Executor resource accounting or Human budget. Currentness is explicitly
`EXACT_LATEST_AT_PREPARATION`, a bounded native observation rather than a lease
on future delivery/consumption.

Every result explicitly denies dispatch, delivery, ACK consumption, C2
admission, continuation, Executor selection, correction strategy, policy
selection and another RUN. Production activation is NOT_ACTIVATED. Live
Executor lease, Human risk acceptance and Human budget are NOT_ESTABLISHED.
The original admitted Executor is immutable destination context, never a
new model/profile choice or an assertion that the same live lease exists.

## One existing protected store and immutable replay

Prepared feedback is stored alongside its original checkpoint:

```
.git/aios/verification/<RUN>/.runtime-mutation/intent.json
.git/aios/verification/<RUN>/l3-02/kNNN.feedback.json
```

There is no new directory owner, database, scheduler, router, mutable ledger,
persistent index or lifecycle artifact. The bounded existing checkpoint history
reader recognizes at most one feedback file per original K_i and checks its
exact deterministic bytes against that original checkpoint/admitted Executor.
All original native checkpoint/proof and terminal checks remain in place.

Before a write, the existing per-RUN Runtime fence excludes checkpoint,
feedback, RESULT and FAILURE writers. Terminal RESULT/FAILURE, a terminal
intent, conflicting/unknown state or an unresolved fence blocks preparation.
Writes use the original exclusive-create/flush/fsync helper and never overwrite
an existing record. A crash before, during or after the write retains the first
intent and all original bytes; even complete feedback bytes behind an
unresolved fence remain BLOCKED. There is no automatic unlock, deletion,
recovery, attempt authorization or zero-cost assumption.

Exact replay freshly authenticates the latest K_i and original proof, then
returns the persisted original bytes/identity without proof execution, resource
debit, new ordinal or appended durable effect. Altered content at the same
identity, duplicate/foreign/orphan files, missing captures, gaps, stale K_i or
changed evidence fail closed. A later checkpoint leaves prior feedback
immutable but makes it ineligible for replay. Terminalization cannot reopen
the RUN or regenerate/replace feedback, including on duplicate preparation.

## Test definitions and remaining obligations

`tests/test_lean_vp_l3_factual_feedback.py` defines focused real-Git/native
checkpoint and subprocess-proof cases: stable PRIMARY FAIL, unchanged PASS
terminal reuse, exact replay, full shared/distinct mandatory populations,
ERROR/UNKNOWN/mixed proof, collection failure, negative partial/flaky/observer
and raw corruption, forged/cross-RUN/candidate/root changes, correction lineage
denial, stale checkpoints, immutable collisions, terminal competition and
partial/unknown write effects. Positive authority comes from existing native
validators and actual proof; only HTTP transport is replayed. Runtime owns
canonical execution and EVIDENCE for these definitions.

L3-04 still owns **actual delivery, same-current-RUN live Executor lease,
single-consumption ACK and continuation guards**. L3-05 still owns **finite
Human admission budgets/consent/withdrawal and reviewed rollout/rollback**.
L3-06 still owns **integrated focused safety proof**, and D.1 **G3** still needs
the eligible integrated engineering proof, independent review and publication.
**G6** remains a separate exact future per-RUN Human opt-in after eligible G3.
**L4**, including G4/G5, matched VP-01/VP-06 whole-episode measurements and full
real VP-07 AC-01..AC-36 conformance, remains outstanding. Prepared F_i establishes
none of these obligations or a speedup claim. Review, Publisher, roadmap,
frozen Kernel and all historical V1/V2 authorities remain separate.
