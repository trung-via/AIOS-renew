# VP-02B intra-RUN proof and frozen Kernel gate v1

Contract family: `intra-run-proof-v1`, `intra-run-lifecycle-v1`.

Compatibility analysis: `frozen-intra-run-kernel-gate-v1`.

Status: offline declarative foundation for TASK-328. **NOT_ACTIVATED**.
Runtime-orchestrated correction: **KERNEL_AMENDMENT_REQUIRED** under the currently
frozen authority. This document records a conservative governance decision gate;
it adds no production admission check, verification stage or latency cost.

## Authority and containment

The Human-approved [VP architecture v1.2](AIOS-VERIFICATION-ARCHITECTURE-REPLACEMENT-v1.md)
describes prospective, explicitly opted-in PRIMARY correction. Planning/refinement
approval does not amend the Kernel, authenticate a lease, authorize retry or
admit execution. [Constitution](AIOS-CONSTITUTION.md) sections 2 and 3 reserve
constitutional change to Human authority and prohibit reinterpretation of
frozen engineering lineage. Manifesto intent is not canonical execution truth.

Human owns risk, delegation and bounds; Brain owns WHAT, scope and semantic
proof obligations; Executor owns HOW; Runtime owns canonical admission,
verification, evidence and terminal state; Reviewer independently judges the
exact final candidate; Publisher owns exact reviewed-source publication. No
feedback transport acquires any of these authorities.

The new module only validates decoded in-memory dictionaries/lists and returns
immutable **design descriptions**. It imports the unchanged
[TASK-326 proof foundation](AIOS-VP02-PROOF-COVERAGE-CONTRACT-v1.md) for complete
reviewed mapping validation and distinguishing-condition comparison. It neither
retests nor modifies that foundation. There is no production caller, command
runner, scheduler, retry controller, checkpoint file, second evidence store,
RUN mutation, callback, loader, clock, environment read, credential access,
Git/process operation, pytest invocation, network call or filesystem mutation.
No V1/V2 parser, policy, verification profile or Runtime integration is added.
The currently admitted `minimum-sufficient-v2` policy remains in force.

Neither an opaque reference nor a content digest authenticates canonical
provenance. Independently supplied pins can expose inconsistent descriptions;
they cannot prove the pin supplier trustworthy. Even a complete synthetic
declaration returns `status=BLOCK`, `activation=NOT_ACTIVATED`,
`kernel_gate=KERNEL_AMENDMENT_REQUIRED`, and these false flags:
`provenance_authenticated`, `runtime_continuation_authorized`,
`verification_execution_authorized`, `evidence_reuse_authorized`,
`verified_evidence`, `canonical_checkpoint_created`. Supplying a putative Human
amendment reference changes none of those flags. Actual authority authentication
and source-to-current applicability witnesses remain later, separately admitted
work. These outputs must never be fed into an active Runtime as permission.

## Lifecycle distinctions

| Situation | Declarative route and boundary |
| --- | --- |
| Admitted ACTIVE PRIMARY, Executor chooses a targeted local test/fix iteration | Kernel section 5 permits normal Executor implementation iteration within the existing scope/lease; no canonical Runtime feedback controller is implied. |
| Prospective committed C1, Runtime proof observation K1 while RUN is ACTIVE | A `PRETERMINAL_OBSERVATION` description, explicitly different from RESULT, FAILURE, REVIEW, REPAIR or REMEDIATION. Currently blocked for production by the frozen authority gate. |
| Prospective factual failed-proof feedback K1 -> same Executor -> distinct C2 | Described only when identity, complete stable failure, opt-in, scope, sticky lease, risk, progress and remaining resources are declared; never an instruction to correct, retry or re-enter. |
| Admitted ACTIVE RUN reaches a terminal boundary | Exactly one canonical terminal RESULT or FAILURE through the existing Runtime; this module cannot emit either. A final checkpoint's equivalent proof must not be rerun merely for terminalization in a future authorized design. |
| Canonical terminal FAILURE | Immutable failed RUN stays terminal. Existing `AUTHOR_REPAIR` binds that FAILURE and requires separately authored correction plus a distinct, newly admitted REPAIR RUN. It cannot revive the failed RUN. |
| Canonical RESULT followed by REVIEW CHANGES_REQUIRED | Existing narrow REMEDIATION and delta-review route; outside the prospective PRIMARY contract. |
| Operational failure before AIOS admission | No canonical RUN, FAILURE or REPAIR is fabricated. Transport/runner signals remain subordinate operational facts. |

`describe_lifecycle(data, expected_lineage=...)` accepts exactly `schema`,
`lineage`, `request`, `next_run_id`. The lineage has exactly `task_id`,
`task_revision`, `task_source_sha`, `admission`, `run_id`, `run_state`,
`base_sha`, `candidate_sha`, `terminal_kind`, `terminal_ref`, `terminal_digest`.
It must equal the independent supplied lineage. Non-admission requires NONE
and null RUN/source/terminal fields. Admission requires a RUN ID/base. ACTIVE
excludes every terminal field; terminal states require exactly one matching
kind/reference/digest; RESULT also requires its exact candidate. `request` is one of the five explicitly listed routes
in the table (local iteration, preterminal, terminal RESULT, terminal FAILURE,
AUTHOR_REPAIR). `next_run_id` is null except on AUTHOR_REPAIR, where a distinct
ID is needed to describe the existing separate correction route. It creates
and admits nothing. Every lifecycle authorization flag remains false.

Terminal re-entry, a second terminal, a terminal impersonating ACTIVE,
competing RESULT/FAILURE kinds, stale pins and hidden new RUNs either raise a
contract error or return BLOCK. Missing/extra fields and unknown requests,
including automatic retry, cannot create an alternative lifecycle.

## Exact in-memory declaration

`validate_intra_run_contract(data, expected_context=..., proof_contract=...,
proof_mapping=..., expected_mapping=...)` returns `IntraRunDeclaration` or
raises `IntraRunContractError`. There are no defaulted authority inputs.

The root has exactly `schema`, `operation`, `binding`, `delegation`,
`checkpoint`, `feedback`, `continuation`, `applicability`, `governance`.
`schema` is `intra-run-proof-v1`; `operation` is exactly
`PROSPECTIVE_PRIMARY_OPT_IN`. Other modes and historical artifact formats are
not decoded as this schema. All nested records require exact fields and plain
primitive types; booleans are not integers. Duplicate identities, unsupported
versions, unknown enums and oversized populations fail closed. No public call
executes an untrusted object hook, interprets raw logs or dereferences a label.

| Record | Exact fields and binding meaning |
| --- | --- |
| `Pin` | `id`, positive `revision`, `digest`; full lowercase SHA-256 content identity. |
| `SourceDeclaration` | `ref`, exact `source_sha`, `record_digest`; immutable source/record declaration rather than just an opaque label. This is not an authentication witness. |
| `binding` | `task_id`, `task_revision`, `acceptance_ids`, `task_source_sha`, `task_blob_sha`, `task_envelope_digest`, `run_id`, `run_source_sha`, `run_record_digest`, `base_sha`, `candidate_sha`, `candidate_tree_sha`, `operational_authority_digest`, `proof_contract`, `proof_mapping`. Exact admitted base never silently moves with main. Contract/mapping are complete Pins. |
| `delegation` | `human` SourceDeclaration, `executor`, `profile`, `model`, `effort`, `lease_id`, `lease_generation`. Human/source, Executor and the entire native profile/effort/lease must equal the supplied context and continuation; no failover or inferred model change. These are operational declarations, never new TASK fields. |
| `checkpoint` | `kind`, `owner`, `ordinal`, `predecessor_digest`, `committed`, `clean`, `resource_usage`, `evidence`, `content_digest`. Kind is only PRETERMINAL_OBSERVATION; owner only RUNTIME_DECLARATION. Resource usage exactly matches the supplied spent counters. No canonical artifact is created. |
| Each `evidence` item | `proof_id`, `provenance`, `subject_sha`, `conditions_digest`, `outcome`, `validity`, `complete`, `stable`, `conflicting`, `observed_items`. Exactly one unique declared evidence record per mapped proof; exact candidate and full conditions digest. Outcome is PASS/FAIL/ERROR/UNKNOWN; validity remains VALID/INVALIDATED/UNKNOWN. |
| `feedback` | `kind`, `binding`, `checkpoint_digest`, `items`, `content_digest`. Kind is only FACTUAL_FAILED_PROOF. Its subject binding and checkpoint must match exactly. |
| Each feedback item | `obligation` Pin, complete `acceptance_ids`, `evidence` SourceDeclaration, `outcome`. It exactly mirrors one nonpassing mapped obligation, including ERROR/UNKNOWN when present. Every nonpassing obligation is retained once. No strategy, proposed edits, commands, diagnosis prose, raw logs or instruction field exists. |
| `continuation` | `binding`, `delegation`, `checkpoint_digest`, `feedback_digest`, `target_candidate_sha`, `changed_paths`, `change_digest`, `causal_obligation_ids`, `progress`, `risk`, `semantic_choice`, `requested`. Describes one prospective correction and its causal/progress/resource claims, never a correction strategy or runtime action. |
| `governance` | `kernel=FROZEN_V0_1`, `activation=NOT_ACTIVATED`, `authority_gate=KERNEL_AMENDMENT_REQUIRED`, `human_amendment` (null or SourceDeclaration). No affirmative label can override frozen authority. |

The proof foundation is validated against TASK identity/revision/envelope and
the **independently pinned complete acceptance list** in the binding. Incoming
coverage cannot redefine that acceptance list. Contract and mapping IDs,
revisions and content digests must match both the binding and reviewed mapping
pin. All source proof conditions must bind this candidate; comparison proofs
also bind this exact base. Missing semantic coverage, merged incompatible
conditions or incomplete mapping remains an error, never inferred coverage.

Evidence descriptors bind every mapped proof, not a raw pytest exit code.
Observed counts cannot exceed the approved population; a declared complete
population must equal it. Conditions include test code, fixtures/shared state,
toolchain/environment, profile/workers, concurrency/order, collection,
integration/repetition and evidence schema. Source record references/digests
must be unique across proof descriptors. Shared coverage is only possible
through TASK-326's explicit reviewed mapping; it does not merge distinct proofs.

References are at most 128 ASCII characters, with a leading letter and only
letters, digits, underscore, period, colon or hyphen thereafter. Whitespace,
shell composition, space-separated invocations, paths and recognized bare
executable tokens cannot be feedback references.
They are opaque non-executable identities, never instructions. No raw untrusted
log is a semantic input. The separate `changed_paths` field contains exact
relative ASCII file paths for scope checking, never patterns or executable text.
Slash-separated components cannot be `.` or `..`; absolute paths, drive names,
backslashes, globs and shell text are rejected. No path implies proof validity.

## Independent currentness, bounds and continuation guards

The supplied `expected_context` has exactly `binding`, `delegation`, `operation`,
`admission`, `run_state`, `terminal_ref`, `lease_state`, `authority_current`,
`opt_in`, `budget_authority`, `limits`, `spent`, `allowed_paths`, `next_ordinal`,
`predecessor`, `seen_checkpoint_digests`, `seen_feedback_digests`,
`seen_candidate_shas`, `checkpoint_digest`. It describes canonical currentness
requirements; the parser cannot authenticate it or renew the lease.

Preterminal validation requires operation PRIMARY, ADMITTED, ACTIVE and null `terminal_ref`.
`opt_in` is exactly `enabled` and `human`; `budget_authority` is exactly
`human` and `record`. Both Human sources match delegation. Resources in
`limits`, `spent`, `requested` are exactly `corrections`, `seconds`,
`test_items`, `tokens`. The expected context supplies Human budget provenance
and the exact permitted paths. Missing authority is not filled from planning.

`operational_authority_digest` commits to a record containing `binding` (the
TASK identity/revision/acceptance/source/blob/envelope and RUN identity/source/
record/base fields), full `delegation`, `opt_in`, `budget_authority`, `limits`,
and `allowed_paths`. It is candidate-independent so the original Human scope
and limits remain bound across checkpoints. Changed opt-in, scope, authority
source or limits cannot keep the same binding. Each checkpoint additionally
commits to its `resource_usage`, which must match current spent counters. These
hash bindings detect internal drift; they still do not authenticate Human
authority or permit the caller to enlarge its original budget.

| Bound | Limit |
| --- | --- |
| Correction count / checkpoints | At most 16 corrections / 17 checkpoint descriptions |
| Wall seconds / test items / tokens | At most 3,600 / 100,000 / 1,000,000 each |
| Proofs, obligations, feedback items, applicability entries, acceptance IDs | At most 256 per list; TASK-326's own bounds still apply |
| Exact scope paths | At most 128, at most 512 characters each |
| Revisions / lease generation | Positive integers through 2,147,483,647 |
| Immutable SHA identities | Exactly 40 lowercase hexadecimal characters |
| Content/record digests | Exactly 64 lowercase hexadecimal characters |

Limits and requested resource estimates are positive; spent counters may be
zero. Exactly one correction may be requested. For every resource,
spent + requested must fit the original finite limit. Exhaustion blocks and
cannot expand its own budget; a budget never authorizes omission of mandatory
proof. Wall/resource values are declarations, not measurements or clock reads.

Checkpoint ordinal equals `next_ordinal`; every prior checkpoint, consumed
feedback and candidate appears once in bounded history. At ordinal 1 there is
no predecessor or prior history. Later predecessors have exactly `ordinal`,
`digest`, `candidate_sha`, refer to ordinal - 1 and the last prior identities,
and cannot reuse the current candidate. Spent corrections equal ordinal - 1.
A previously observed checkpoint/feedback is rejected rather than interpreted
as a second permission, proof execution or consumed attempt. This offline
rejection is not implemented persistent deduplication or crash recovery.

Digests use SHA-256 of ASCII JSON (`sort_keys=True`, compact separators,
`ensure_ascii=True`, `allow_nan=False`). Checkpoint content commits to exactly
`schema`, `binding`, `delegation`, and its entire checkpoint except its own
`content_digest`; it must equal the independently supplied checkpoint pin.
Feedback commits to its full record except its own `content_digest`.
Incoming list order is part of these content hashes; the module does not
silently normalize a received checkpoint into another immutable identity.
Hashes detect internal alteration only; synthetic hashes are not provenance.

Well-formed declaration failures produce typed BLOCK reasons:

- No explicit Human opt-in, lost/expired/unknown lease, or changed/unknown authority.
- Uncommitted/unclean candidate, any incomplete/unstable/conflicting/unknown or
  invalidated source proof, or absence of an actual declared candidate FAIL.
  A comparison failure alone is insufficient.
- Changes outside exact allowed scope; new/unknown Human risk or Brain semantic choice.
- Same/base/previously observed target candidate, NO_PROGRESS/UNKNOWN progress,
  absent causal basis or causal IDs outside failed candidate obligations.
- Resource exhaustion or correction-count/history conflict.

`prospective_conditions_declared=True` only describes complete non-governance
prerequisites. It never means GO for Runtime. Missing Human amendment is an
explicit additional blocker. Even a supplied amendment declaration leaves
canonical provenance unauthenticated and extension activation blocked.
Malformed or stale input raises instead of being downgraded to a usable hint.

## C1 -> C2 applicability and no double execution

`applicability` has exactly `invariants`, `required_dimensions`,
`base_replay_required_gates`, `entries`. All required invariant/dimension/BR
identities must appear in the versioned order; none can be silently removed.

Every distinct obligation retains exactly one entry with `obligation` Pin,
`source_evidence` SourceDeclaration, `source_candidate_sha`,
`target_candidate_sha`, full `target_conditions`, `declared_state`,
`changed_dimensions`, `witness`, `witness_dimensions`. Source proof/evidence
and target candidate match the checkpoint and continuation exactly. Target
conditions are validated using the unchanged TASK-326 full condition schema.

Required dimensions are `test_code`, `fixtures`, `helpers`, `shared_state`,
`toolchain`, `environment`, `profile`, `worker_mode`, `concurrency`, `ordering`,
`collection`, `integration`, `repetition`, `population`, `evidence_schema`.
These are **witness requirements**, not an implemented footprint engine.

| Declared state | Prospective meaning and validation |
| --- | --- |
| VALID | All distinguishing declared conditions are equal apart from the exact source-to-target candidate; no changed dimension; non-null immutable witness declaration covering every dimension. Still no authenticated witness or evidence reuse authorization. A VALID source FAIL remains a FAIL; it cannot become a C2 PASS. |
| INVALIDATED | At least one explicit changed dependency dimension. The obligation remains separately represented and needs bounded affected proof in a future authorized scheduler. No new execution is requested here. |
| UNKNOWN | Insufficient or uncertain applicability; never implicitly VALID, PASS or discharged. It remains represented for later authoritative resolution. |

The required invariants retain the original subject, require a full immutable
applicability witness, prevent UNKNOWN discharge, preserve distinct integration,
concurrency and ordering, prohibit equivalent double execution, automatic
failure reproduction, automatic base replay and broad fallback, and forbid a
budget from waiving proof. No evidence is relabeled as executed on C2.

An unchanged filename, nodeid, command, disjoint diff, caller VALID flag or
synthetic witness digest does not authenticate reuse. No such weak selector is
an accepted applicability field. Proof identity alone cannot collapse required
integration, concurrency, worker, fixture or ordering conditions. All mandatory
obligations remain in the returned contract and applicability entries, including
failed and UNKNOWN ones; there is no discharge or execution plan API.

Future terminalization must use a valid final checkpoint without equivalent
reruns; invalidated/new obligations need only the lawful affected proof while
distinct required coverage remains mandatory. No same-SHA reproduction, base
collection, narrow base probe or broad replay follows just from failure.
**Every future base replay still requires all six TASK-326 gates**:
BR-1 approved necessity, BR-2 decision relevance, BR-3 no valid alternative,
BR-4 minimum lawful scope, BR-5 exact comparability, BR-6 authority/cost bounds.
The required gate list is an invariant, not a substitute for TASK-326's factual
gate validation or future authentication. This module authorizes no replay,
evidence reuse or verification execution, even with every label present.

## Frozen Kernel compatibility matrix

The [Kernel specification](AIOS-RENEW-KERNEL-v0.1-SPEC.md) is frozen, together
with the [freeze record](AIOS-RENEW-KERNEL-v0.1-FREEZE.md)'s Frozen Kernel and
Deferred Features lists. Its exact relevant clauses are:

- §2.3 and §9 Laws 1/2/15: one active Executor, Executor owns HOW, same semantic
  contract with native thin adapters.
- §2.5 and §10: deterministic thin Runtime; no duplicate Brain/Executor/Reviewer reasoning.
- §3: forward TASK -> one Executor -> implementation/verification -> RESULT +
  EVIDENCE -> independent REVIEW; remediation does not restart the task.
- §4.1: TASK describes outcome, not instance; Executor identity, execution SHA,
  raw logs and model instructions do not normally belong in TASK.
- §4.2: RUN is operational metadata referencing TASK; genuine recovery/handoff
  may use a new RUN, without automatically restarting full review fixes.
- §4.3/§4.4/§4.5/§4.6: RESULT is a concise claim summary, EVIDENCE is proof,
  REVIEW is independent semantic judgment, REMEDIATION is a narrow follow-up.
- §5: normal local inspect/code/targeted-test/fix/targeted-test iteration is
  permitted and is not a review FIX cycle.
- §6.2/§6.3/§6.4 and §9 Laws 8/11/13: narrow corrections and delta review retain
  continuity; new intent needs TASK revision or a new TASK.
- §7 and §9 Laws 7/9/10/12/14: immutable binding, progressive verification,
  bounded loops, no unchanged repetition, evidence survives until invalidated,
  deterministic identity/scope/lease/evidence coordination.
- §8: adapters bind/invoke/capture/normalize; no extra planning/review/reasoning authority.
- §11: automatic retry, automatic reroute, token-budget machinery inside TASK,
  complex artifact graphs and dependency orchestration are explicitly excluded.
- §12: continuity/automated invalidation are future considerations, not promoted
  requirements without measured need.

The frozen spec does not itself define a preterminal checkpoint schema or a
Runtime feedback re-entry transition. Existing admitted FAILURE/AUTHOR_REPAIR
lineage is additionally grounded in the current
[Project Contract](CHATGPT_PROJECT_CONTRACT.md) canonical lineage and correction
boundaries, and the existing Runtime/unified-state implementation. This document
does not misattribute that later terminology to Kernel §4.3.

| Alternative or field family | Frozen compatibility / exact blocker | STOP or conditional GO |
| --- | --- | --- |
| This offline module, synthetic declarations/tests and document | No operative artifact, policy or authority change; TASK-326 reused unchanged. | GO for this bounded design implementation under its admitted TASK only. No production GO. |
| Executor-local targeted test/fix before its immutable result | Explicit §5 path, subject to existing scope/one lease and progressive §7 verification. Executor chooses HOW. | GO through existing admitted execution rules; no Runtime preterminal loop is inferred. |
| Subordinate operational snapshot of source/task/run/candidate, native profile/effort, lease, resource estimates and opaque observation references | May remain implementation metadata under §4.2, §9 Law 14 and Constitution §4 if it creates no canonical status/authority or normative gate. | Conditional GO only for separately authorized metadata engineering; historical schemas untouched. This design does not persist it. |
| Runtime-owned canonical checkpoints with normative ordinal/hash/history/evidence validity, continuation eligibility, budget consumption and same-RUN feedback re-entry | §3/§4/§10 do not establish this new operative transition; §11 excludes automatic retry; Freeze defers autonomous retries. Names such as sidecar/metadata cannot cure a normative semantic extension. | STOP: KERNEL_AMENDMENT_REQUIRED. Separate explicit Human Kernel amendment defining these transitions and preservation obligations, then separately admitted implementation. |
| Make opt-in, token budget, model/effort, lease or checkpoint normative TASK fields, or replace required verification form | §4.1 outcome/instance separation and §11 token machinery exclusion; current exact TASK parser rejects extra operative fields. Same YAML shape does not prove same semantics. | STOP for Human Kernel amendment if normative; no silent schema extension. Subordinate operational metadata is the distinct alternative above. |
| Future external semantic contract / reviewed mapping under a form-preserving required-list envelope | TASK-326's compatibility analysis remains in force. Current task.py and authoring rules require executable commands; verification.py consumes them under V2. | STOP for policy/authoring/decoder/single-scheduler cutover in separate authorized TASKs; Human resolves any normative Kernel change before activation. Never feed semantic references to V2 as commands. |
| Exact C1 -> C2 evidence applicability, deduplication and single scheduling | §7 and Laws 10/12 support the invariants, but not a witness authority inferred from labels or a second scheduler. | STOP until VP-03/04 and real conformance establish authenticated validity, separate proofs and one scheduler. No production adoption here. |
| Reopen terminal FAILURE/RESULT, overwrite old lineage, fabricate pre-AIOS FAILURE, switch Executor implicitly, strategy-bearing feedback | Conflicts with immutable authority/lineage, role separation and current canonical terminal/correction contracts. | STOP; reject. Separate authority cannot be inferred from planning. Terminal failure keeps AUTHOR_REPAIR; REVIEW findings keep REMEDIATION. |

Thus source identities, profile/effort, lease IDs, resource counters and
diagnostic proof references can be subordinate operational metadata. Making
checkpoint state, automatic feedback, bounded Runtime re-entry, proof-discharge
semantics, new TASK fields or new status transitions **normative** requires
separate Human Kernel governance wherever the frozen contract cannot establish
that authority. No local parser success or reviewed planning document is that
amendment. An engineering implementation must not resolve this ambiguity by
silently widening the definition of normal Executor-local iteration.

## Explicit future STOP/GO gates

1. **Authority STOP:** Human separately decides the frozen Kernel amendment,
   exact normative schema/transition changes, source authority and compatibility.
   Explicitly resolve §3/§4/§10/§11 and the freeze's retry deferral. Preserve
   Constitution, role separation and immutable historical decoding. Missing or
   uncertain amendment authority is STOP.
2. **Design-only GO:** This three-file family may be validated synthetically
   under TASK-328. It creates no RUN/checkpoint/EVIDENCE/lifecycle effect. A
   reference claiming amendment authority still cannot turn these APIs into GO.
3. **Implementation STOP:** Future VP-03 authenticates checkpoint history,
   provenance and complete source-to-target footprints/witnesses; VP-04 provides
   exactly one scheduler; VP-05 supplies separately authorized same-Executor
   transport and hard-stop handling. No durability, recovery, re-entry or
   scheduling is implemented by this artifact.
4. **Activation STOP:** Before any prospective opt-in production use, require
   resolved Kernel authority, independently admitted TASKs, explicit Human
   delegation/bounds, canonical Runtime verification, independent exact-candidate
   REVIEW and Publisher activation; required distinct integration/concurrency/
   ordering proofs and real VP-07 negative conformance stay mandatory. No V2/new
   scheduler may double-execute one work item.
5. **Measurement/adoption STOP:** VP-01 remains DEFERRED_BY_HUMAN; no savings,
   completion or waiver is claimed. VP-06 measurement and measurement-dependent
   exits remain future work. VP-03/VP-07 dependencies, H4 origin conformance,
   downstream pins/adoption, publication and roadmap advancement are unchanged.

## Synthetic conformance scope

The focused test file constructs synthetic complete PRIMARY declarations with
separate unit, integration, concurrency, ordering and optional comparison
obligations. It covers malformed/oversized records, stale TASK/RUN/candidate/
mapping/source pins, changed Human/Executor/profile/effort/lease, incomplete or
unstable proof, unknown validity/outcomes, repeated checkpoint/feedback,
predecessor tampering, lost lease, scope escape, Human risk/Brain choice,
no progress, resource exhaustion, all applicability dimensions and BR gates,
terminal re-entry, existing distinct AUTHOR_REPAIR, pre-AIOS failures, and absent
or merely asserted amendment authority. VALID/INVALIDATED/UNKNOWN remain distinct;
targeted green never erases required wider conditions.

Effect traps surround both public calls, including complete, denied, malformed,
local, terminal and claimed-amendment descriptions. They forbid process/Git and
pytest-child invocation, filesystem reads/writes/discovery, network, environment
access, dynamic import and execution. A dependency guard checks that the module
imports only the offline foundation and standard-library parsing tools. The
traps assert zero effects and no additional AIOS module imports/lifecycle authority.
Input snapshots and frozen tuple outputs
guard against hidden in-memory mutation. No production runtime is invoked.

Synthetic conformance is not canonical verification EVIDENCE, authenticated
provenance, production Kernel compliance, real-world savings, VP-01/03 completion,
VP-07 cutover or publication. Runtime constructs canonical EVIDENCE for TASK-328;
Reviewer and Publisher retain their separate later roles. Only the new module,
its new focused test file and this new document are added.
