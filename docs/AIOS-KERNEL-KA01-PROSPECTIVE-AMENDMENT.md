# AIOS Kernel Amendment KA-01 — Prospective Verification and Bounded Intra-RUN Continuation

| Identity / status | Value |
| --- | --- |
| Amendment identity | **KA-01** |
| Document version | **1.3 — historical typed modes retained; TASK-425 prospective default across three coding operations** |
| Normative status | **HUMAN_RATIFIED_PROSPECTIVE_SPEC** |
| Runtime activation policy | **PRODUCTION_DEFAULT_THREE_FLOW** for newly admitted coding RUNs under D.1.2; historical v1/v2/v3 keep their exact semantics. Native support requires actual capability; no review/publication outcome asserted. |
| Original Human ratification date | **2026-10-09** — KA-01 v1.0, sections A–D. |
| Human activation-sequence decision | **2026-10-18** — v1.1 delta described in A.1 and D.1; no production activation. |
| Human three-operation scope decision | **2026-10-19** — v1.2 delta described in A.1, C.1 and D.1; no production activation or per-RUN consent. |
| Human resource decision | **2026-10-21** — v1.3 defaults and per-field operational overrides in C.8; no production activation or per-RUN consent. |
| Human closeout decision | **2026-10-25** — one TASK-423 for the contract and minimum supported production enablement; retire future TASK-418, L3-06, separate G3/G6 and L4 packages. |
| Ratified scope | Prospective normative design and supported v1.3 activation under D.1; historical operation boundaries and resource defaults preserved; no constitutional change. |
| Canonical repository | `trung-via/AIOS-renew` |
| Historical v1.0 publication | `TASK-329 r1` / `REVIEW-329-001`; immutable reviewed/published version. |
| Historical v1.1 amendment lineage | `TASK-374`, revision `1`; activation-sequence-only version, retaining its own exact Runtime/Reviewer/Publisher history and PRIMARY-only eligibility. |
| Historical v1.2 publication | `TASK-386`, revision `1`; immutable published three-operation specification, with no default resource profile and no production activation. |
| Historical v1.3 resource publication | `TASK-396`, revision `1`; exact reviewed/published specification retained in Git history. It did not authorize this activation delta. |
| v1.3 activation amendment | `TASK-423`, revision `1`; ordinary Runtime verification, independent exact-source review and Publisher publication govern this same implementation delta, without a later activation TASK. |
| Human native interaction priority | **2026-10-25, TASK-424 r1** — one integrated original-RUN verify/fix/verify delta, new explicit consent and same original live Codex session across distinct turns; no extra activation stage. |
| Human default policy decision | **2026-10-25, TASK-425 r1** — bounded same-RUN Runtime verification/correction is the prospective default after independent PRIMARY, REPAIR or REMEDIATION admission, without another KA-01 selection/consent Issue or selector. |

## A. Identity, authority and legal effect

### A.1 Separate prospective amendment

**Prospective precedence (TASK-425):** D.1.2 and the current-policy paragraph in
D.4 supersede this document's earlier optional enrollment, legacy-default and
per-RUN KA-01 selection/consent requirements only for future newly admitted coding
episodes. They include existing TASK IDs. Earlier A–C and D.1/D.1.1 opt-in text
continues to describe the exact historical typed modes, not a requirement to
create automatic per-RUN consent for the new mode. Independent Human TASK,
REPAIR and REMEDIATION authority, selected Executor/profile and live lease remain
mandatory. No previously admitted or terminal RUN is retargeted.

KA-01 v1.3 records the separately Human-ratified prospective Kernel design for:

1. A future version of TASK authoring that expresses reviewed semantic proof
   obligations with a reviewed deterministic coverage mapping, in place of
   mandatory Brain-authored executable verification commands.
2. Optional, bounded Runtime-owned preterminal proof feedback and continuation
   by the same currently authorized live leased Executor inside one already
   admitted `ACTIVE` `PRIMARY`, `REPAIR` or `REMEDIATION` RUN, with a distinct
   immutable operation-specific authorization and exact Human per-RUN opt-in.
3. A versioned default resource profile with separately authenticated per-field
   operational overrides, resolved and frozen for each eligible future RUN as
   specified in C.8. Default selection supplies limits, never per-RUN permission.

Human ratification establishes authority for this narrowly defined future
normative design. It is not an execution admission, a Runtime verification
result, a Reviewer verdict, Publisher publication, a live applicability
witness, or per-RUN consent to activate an implementation. This exact amended
version must itself pass the independent normative publication gate in D.1.
Its operative extension applies **only to future versioned Human-opted-in work
after eligible L1–L3 core engineering is actually implemented, independently
Runtime-verified, semantically reviewed and published, and exact finite Human
opt-in is authenticated at admission**. The 2026-10-25 prospective activation
policy in D.1 replaces the former separate G3/G6 and mandatory L4 package
sequence for new supported v1.3 admissions. This text is normative authority,
not a claim that its implementation has passed Runtime, review or publication.

**Historical prospective change authorized by Human on 2026-10-18 (v1.1):**
remove the requirement to complete L4 / VP-06 / VP-07 real production-episode
conformance before activation. Proceed L1 → L2 → L3, permit bounded production opt-in only
after the eligible reviewed/published core exists, then use subsequent lawful
production TASK episodes for L4 measurement and full conformance. This is a
policy/risk decision about sequencing, not evidence of feature readiness,
permission to run an unimplemented loop, or approval of an unreviewed change.

KA-01 **v1.0** remains the immutable version ratified on 2026-10-09 and separately
reviewed/published through `TASK-329 r1` / `REVIEW-329-001`. Its original C.3(1)
required activation for production continuation; D.1 G3 included VP-03..VP-07,
G4 required VP-01/VP-06 measurement reconciliation, G5 required real VP-07
AC-01..AC-36 conformance, and G6 followed G1..G5. **Version 1.1 changed that
activation sequencing only**, through its own publication and future versioned
admissions; its C.1 remained PRIMARY-only and explicitly excluded REPAIR and
REMEDIATION by analogy. Both v1.0 and v1.1 retain their exact published sources,
decoding, authority and TASK/RUN/review/publication lineage in immutable Git
history. This successor does not change either version's meaning or gates.

**Exact prospective change authorized by Human on 2026-10-19 (v1.2):** extend
the one existing Runtime-owned preterminal proof/feedback contract to future,
individually opted-in ACTIVE PRIMARY, REPAIR and REMEDIATION RUNs. C.1 binds
each operation's existing lawful authorization; the extension adds no repair
action, remediation finding, strategy choice or cross-RUN mutation permission.
The same Executor means the selected live leased Executor of the **current
admitted RUN**, not an assumed continuation of a predecessor PRIMARY's worker.
The v1.1 preactivation-core/postactivation-measurement sequence is retained.

**Historical v1.2 planning/publication boundary:** the controlling source was
`.ai/roadmap-state.yaml` record
`human_lean_vp_intra_run_expansion_20261019`, reconciled after `TASK-385 r1` /
`RUN-385-001` / `REVIEW-385-001 PRIMARY PASS`, exact reviewed/published candidate
`aaab7a87c63fd40b592dee817fdbfada759a2085`. That predecessor establishes only
L2-02 applicability observation, not L2-03 reuse, G3 eligibility or G6 activation.
Issues #2168/#2208 supported planning context only. The v1.2 normative
`TASK-386` was separately approved **outside the original 16 L1–L4 delivery
slots**. Its publication gate preceded separately admitted L2-03/L3 engineering;
the published v1.2 version and that dependency retain their original meaning.

**Exact prospective change authorized by Human on 2026-10-21 (v1.3):** set
`max_corrections=2`, `max_checkpoints=3`, `max_run_wall_minutes=240` and
`max_runtime_verification_minutes=180`, with verification INCLUDED in the RUN
wall limit, and optional `max_tokens=null` (no KA-01-specific token ceiling).
Human may override each field independently for an exact TASK id/revision and
explicit applicable operation(s) through separately authenticated operational
delegation. C.8 is the exhaustive resource delta: it replaces v1.2's requirement
for separately supplied limits without defaults **only for selected v1.3 work**.
It does not add budget fields to the semantic TASK or inherit any earlier
RUN's consent, lease, operation authority or resource balance. External
provider/account limits remain applicable, including when `max_tokens=null`.

The historical October 21 planning source is `.ai/roadmap-state.yaml` record
`human_lean_vp_intra_v13_budget_defaults_successor_20261021` and the
[v1.3 successor roadmap](AIOS-LEAN-VP-INTRA-V13-ROADMAP.md). By explicit Human
decision it supersedes the old live Lean VP planning pointer, preserves all
16 original slots and published `TASK-391 r2` L3-04 plus separate `TASK-393/394`
side tracks, and orders this distinct normative `TASK-396` outside those slots
before L3-05 → L3-06/G3 → separate G6 → L4. This is approved planning
transcription, not automatic advancement, L3-05 engineering or activation.
The old [Lean VP source](AIOS-LEAN-VP-INTELLIGENT-INTRA-PRIMARY.md) and dated
roadmap records remain historical planning, with no independent resume path.

KA-01 **v1.2** remains its exact published three-operation specification under
`TASK-386 r1`, just as v1.0/v1.1 retain their own sources and gates. The
October 21 v1.3 publication inherited v1.2's operation-specific safeguards and
v1.1's activation ordering; the October 25 policy below prospectively replaces
that ordering for supported new v1.3 admissions. Its defaults never
retroactively supply missing v1.0/v1.1/v1.2 limits or consent.
Neither that historical planning decision nor normative publication supplied
engineering, future Executor delegation or a live lease.

**Prospective activation-policy change authorized by Human on 2026-10-25:**
the controlling record is
`.ai/roadmap-state.yaml:human_lean_vp_single_activation_closeout_20261025`.
Exactly one TASK-423 changes this normative activation policy and enables the
already implemented, technically supported subset at the ordinary production
caller in the same delta. No separate L3-06, G3/G6 qualification package,
L4 measurement/conformance package, pilot or discretionary later cutover is
required. The previous 16-slot plan and TASK-418 r2 / RUN-418-001..007 FAILURE
lineage remain exact unfinished history, never retrospective PASS. Native
Codex same-live-recipient feedback is unavailable and its future implementation
is retired. D.1 defines the supported operation and explicit STOP boundaries;
C.1-C.8 continue to constrain every mechanism actually used.

This document version is not a TASK schema version or an admitted verification
policy name. `HUMAN_RATIFIED_PROSPECTIVE_SPEC` and the activation-policy label
create no new RUN status. Source-bound per-RUN admission and actual Runtime
effects, rather than a document label or README pointer, establish enablement.

The [Constitution](AIOS-CONSTITUTION.md) and
[Manifesto](AIOS-MANIFESTO.md) remain unchanged. Constitutional principles and
role boundaries continue to govern this amendment. The
[Kernel v0.1 specification](AIOS-RENEW-KERNEL-v0.1-SPEC.md) and
[freeze record](AIOS-RENEW-KERNEL-v0.1-FREEZE.md), frozen on 2026-08-26 with
freeze baseline `73747ec74e8669dbd0746ccf614558b6ee3d28aa`, are immutable
historical baseline. KA-01 is a separately versioned addendum, not a rewrite or
reinterpretation of that baseline. B.1 identifies its entire normative delta;
all unaffected clauses and boundaries remain in force.

No existing or already admitted TASK, RUN, RESULT, FAILURE, EVIDENCE, REVIEW,
REPAIR or REMEDIATION acquires KA-01 semantics. Historical identities, subjects,
schemas, verdicts and successful or failed lineage must not be relabeled,
migrated in place, reopened or overwritten. Existing V1/V2 decoding and each
admitted policy remain intact, including historical `minimum-sufficient-v2`
behavior for `TASK-329`. No existing work automatically opts into v1.3; no
v1.0/v1.1/v1.2 authority selects a newer version by analogy.

### A.2 Supporting design and authority separation

The [VP architecture v1.1/v1.2](AIOS-VERIFICATION-ARCHITECTURE-REPLACEMENT-v1.md),
[TASK-326 proof coverage foundation](AIOS-VP02-PROOF-COVERAGE-CONTRACT-v1.md) and
[TASK-328 intra-RUN foundation](AIOS-VP02-INTRA-RUN-PROOF-AND-KERNEL-GATE-v1.md)
are supporting design. The immediate foundation predecessor to v1.0 is `TASK-328 r1`,
`RUN-328-002`, exact reviewed/published source
`19037a71536f65327c541668527c099099af6efe`, with `REVIEW-328-002 PASS`.
That predecessor remains an offline **NOT_ACTIVATED** foundation. Its synthetic
declarations and checks do not authenticate real leases, consent, checkpoint
history or proof applicability. A putative amendment reference cannot turn its
existing APIs into live authorization. Their frozen-Kernel STOP descriptions
remain true for the versions and authority under which they were published.

Authority remains separated even when software runs in one process:

| Owner | Retained authority |
| --- | --- |
| Human | Intent, risk acceptance, version opt-in, Executor/model/profile delegation and finite resource consent; prospective activation and rollback policy. |
| Brain | WHAT/WHY, TASK scope, assumptions, acceptance and semantic proof adequacy; semantic revisions and roadmap decisions. |
| Executor | HOW, implementation choices and permitted local iteration; one sticky active mutation authority. |
| Runtime | Deterministic admission, identity, currentness, scope/lease/bound enforcement, canonical verification, evidence, authenticated applicability and exactly one terminal state. |
| Reviewer | Independent semantic judgment of the exact final candidate and required evidence; no implementation or interim correction selection. |
| Publisher | Publication of the exact eligible independently reviewed source under the existing publication boundary and compare-and-swap protections. |

Delivery mechanisms, checkpoint storage, resource counters, session handles and
feedback acknowledgements are subordinate operational metadata under Runtime
authority. They are not another canonical lifecycle authority, Planner,
Reviewer, Publisher, router, memory database or semantic dependency oracle.
Calling an operative transition a sidecar or metadata never exempts it from
this amendment or the separately admitted engineering gates.

## B. Frozen-clause compatibility and future version contract

### B.1 Exhaustive map of the affected frozen clauses

The following permissions are conditional on A.1 and D.1. Each row preserves
the frozen rule for legacy work. No row grants current production authority.

| Frozen Kernel clause | Preserved boundary | Narrow KA-01 extension for the opted-in future version |
| --- | --- | --- |
| §3 Canonical Pipeline | Human → Brain → TASK → Runtime → one Executor → terminal RESULT/EVIDENCE → independent REVIEW; review findings retain narrow REMEDIATION and delta review. | Inside one future opted-in ACTIVE PRIMARY, REPAIR or REMEDIATION execution, committed candidate/checkpoint/factual-feedback/continuation may precede its single terminal output, under C.1's exact operation authorization. No extra Planner, review stage, fresh PRIMARY admission or terminal re-entry is inserted. |
| §4.1 TASK — Semantic Contract | TASK describes the required outcome, WHAT/WHY, scope and acceptance, rather than an execution instance. Legacy minimum form and decoding remain unchanged. | A separately admitted future version may replace mandatory Brain-authored executable command requirements with reviewed semantic proof obligations and explicit reviewed deterministic mapping. This is a versioned semantic/schema change, not reinterpretation of an existing `verification.required` string or permission to empty that list in V1/V2. See B.2. |
| §4.2 RUN — Operational Execution Record | RUN references exact TASK identity/revision and retains one admitted instance, base, Executor and existing ACTIVE/terminal boundaries. Genuine recovery/handoff and corrections retain their own lawful admission. | Bind a monotonic sequence of subordinate immutable checkpoints, factual feedback and resource consumption to that same ACTIVE RUN and its PRIMARY, REPAIR or REMEDIATION authorization. Opt-in, delegation, native profile, lease and finite budget stay operational; checkpoints never create another RUN or status authority. |
| §4.3 RESULT — Executor Summary | RESULT remains a concise terminal summary of concrete claims, final committed `head_sha`, changed files and unresolved implementation work; it does not duplicate raw logs. | Finalization may reference already valid final-checkpoint proof after Runtime's exact binding checks, without executing equivalent proof again. A candidate or checkpoint is never an interim canonical RESULT. No existing RESULT schema is silently extended. |
| §4.4 EVIDENCE — Proof | Deterministic proof retains its actual RUN/source subject, command or observation provenance, typed outcome and immutable raw references. Claims require evidence. | Runtime-authenticated source-to-target applicability may discharge a target obligation while preserving the original observation's subject and raw digest. A separate applicability binding is required; rewriting `subject_sha` or manufacturing a synthetic PASS is prohibited. Any new format requires separately admitted versioned decoding. |
| §4.5 REVIEW — Semantic Judgment | Independent exact-source semantic review; PASS, CHANGES_REQUIRED or BLOCKED; blocking findings grounded in contract, material delta defect or evidence gap. Reviewer never implements. | Review consumes the final terminal package and authenticated applicability/coverage where relevant. Preterminal factual FAIL is neither REVIEW nor CHANGES_REQUIRED; feedback cannot replace independent final review. |
| §5 Primary Execution Rules; §4.6/§6 Narrow Remediation and Delta Review | Executor-owned inspect/implement/targeted-test/fix iteration within scope remains ordinary local execution. No upstream rediscovery, unrelated work or competing implementation; remediation addresses explicit findings and retains independent delta review. | Runtime canonical failed-proof feedback is a distinct conditional transition in C, not a widened definition of local iteration. The current RUN's same Executor chooses HOW only within its immutable operation authorization. REPAIR cannot change its AUTHOR_REPAIR action; REMEDIATION cannot add findings or replace its authorized strategy. New intent or semantic/risk choice stops that transition; independent delta review is preserved. |
| §7 Verification Policy | Progressive, minimum-sufficient proof; no repetition on unchanged relevant state without new reason; reusable evidence survives until relevant invalidation. Distinct required coverage cannot be skipped. | One Runtime proof scheduler plans checkpoints and finalization, authenticates applicability and executes only lawfully outstanding proof. Equivalent final-checkpoint execution is reused at terminalization. No automatic failure reproduction or base replay; all BR gates in C.6 remain mandatory. |
| §9 Core Kernel Laws | All fifteen laws remain constraints; no authority, evidence, lineage or scope exception. | C makes those laws explicit for preterminal continuity. The law-by-law map below bounds their application; none is waived. |
| §10 Minimal Runtime Responsibilities | Thin deterministic coordination, one mutation authority, SHA/evidence binding, package validation and integration safety. Runtime is no Planner Agent, Verifier Agent, second Reviewer or multi-agent supervisor. | The existing Runtime may enforce authenticated checkpoint/currentness/applicability, finite resources and idempotent factual feedback under one scheduler. It must not infer semantic dependencies, prescribe correction strategy or choose HOW/model/Executor. No second coordination authority is created. |
| §11 Explicit Non-Goals for Kernel v0.1 | Every exclusion remains for frozen/legacy work. Automatic reroute, model routing, generalized retry/failover, autonomous repair, extra agents, dependency orchestration and token-budget machinery inside TASK remain excluded. | **Only the automatic-retry exclusion receives a bounded exception:** future exact-version Human-opted-in, admitted ACTIVE PRIMARY, REPAIR or REMEDIATION, preterminal factual failed-proof feedback to the same current-RUN sticky Executor, within C.1's original operation authorization, live lease and finite consent. The original v1.3 resource delta is C.8; TASK-423 changes its prospective activation policy under D.1-D.4. C's applicable guards remain conjunctive. This grants no automatic reroute, Executor/model failover, transport retry authority, terminal RUN reopening or general retry loops. |
| §12 Known Risks / Future Considerations | Listed possibilities are not v0.1 requirements; added complexity requires measured need and separate promotion by proper authority. | Ratification defines a prospective contract, not measured benefit or production uptake. The 2026-10-25 decision in D.1 limits enablement to actual supported mechanisms and retires the remaining L4 package duties prospectively. Unknown measurements remain unknown; no feature or benefit is promoted by prose. |

The §9 laws apply as follows:

| Law | Binding application to KA-01 |
| --- | --- |
| 1 — One Active Execution Authority | One existing admitted RUN and sticky Executor lease; feedback creates no second mutation authority. |
| 2 — Brain Owns WHAT; Executor Owns HOW | Reviewed requirements/mapping define proof; factual feedback cannot select code edits or strategy. |
| 3 — Minimum Necessary Context | Only bounded task/checkpoint/proof facts and required evidence references go to the Executor. |
| 4 — No Redundant Discovery | Continuation uses known TASK lineage; it cannot restart problem or repository discovery. |
| 5 — Review the Delta | Independent final/delta review remains scoped to the contract, implementation delta and relevant evidence. |
| 6 — Claims Require Evidence | Neither a checkpoint label nor an Executor assertion discharges acceptance without authenticated proof. |
| 7 — Immutable State Binding | Exact committed candidates, ordinal/predecessor/content hashes, provenance and source-to-target bindings are mandatory. |
| 8 — Fix with Continuity, Not Rediscovery | Preterminal continuation preserves the admitted lineage; postterminal correction keeps the existing finding/FAILURE lineage. |
| 9 — No Unbounded Loops | Original finite Human bounds and a progress requirement prohibit self-extension. |
| 10 — Never Repeat Unchanged Work Without New Information | Replay, feedback duplication and terminalization cannot cause equivalent proof reexecution. |
| 11 — Fix the Finding, Not the Task | Continuation addresses failed proof within scope; review remediation still addresses explicit findings only. |
| 12 — Evidence Survives Until Invalidated | Preserve each source observation; invalidate only with relevant dependency facts, and never infer validity from file disjointness. |
| 13 — New Intent Is Not a Fix | Changed intent, acceptance, risk or required semantic choice stops for Human/Brain; no hidden TASK revision. |
| 14 — Deterministic Coordination Before AI Reasoning | Authenticate identity, scope, leases, applicability, costs and idempotency mechanically; no Runtime semantic planning. |
| 15 — Same Contract, Executor-Specific Native Adapter | Native continuity is operational and separately engineered; it cannot change TASK meaning or switch the selected Executor. |

### B.2 Versioned semantic authoring and deterministic resolution

The future version MUST be explicitly defined, independently admitted,
implemented, verified, reviewed and published before it can be selected. Its
decoder MUST distinguish that version from legacy V1/V2 without auto-detection,
heuristic upgrading or fallback interpretation. Unsupported versions or
ambiguous semantics fail closed. A semantic reference MUST NOT be sent to the
V2 command runner, even if it happens to fit V2's nonempty scalar-list shape.
Existing/non-opted-in work retains its actual admitted command-driven policy
and exact V1/V2 decoding, including historical `minimum-sufficient-v2` behavior.
The published `minimum-sufficient-v1` comparison baseline is not replaced by
this amendment; historical read-only decoding stays exact.

Brain authors semantic claims, acceptance coverage, required distinguishing
conditions, scope and proof adequacy. Every required obligation has a stable
identity, version, immutable content binding, TASK identity/revision and
approval provenance. Coverage MUST retain the complete independently bound
acceptance set, including required comparison and nonblocking obligations.
Neither cost nor the word nonblocking removes a required proof.

A separately reviewed/versioned deterministic mapping MUST explicitly bind
each obligation to adequate proof descriptors, conditions and provenance. It
may consolidate equivalent obligations only with all individual acceptance
coverage retained. Missing, stale, ambiguous, conflicting, unused or
incompatible mapping cannot become coverage. Matching filenames, imports,
nodeids, history or command strings cannot supply a missing semantic mapping.
Review establishes semantic adequacy; structural validation alone does not.

The semantic contract and reviewed mapping are **not executable verification
plans**. Runtime mechanically resolves their authenticated pins into one
bounded plan under the admitted policy. Runner mechanics belong to separately
reviewed verification engineering, not mandatory Brain command authoring
relocated to another TASK field, manifest or disguised instruction string.
An opaque ID or incoming approval/review label is not authentication. Runtime
must authenticate the rightful immutable sources and complete mapping before
using them; it must not become a semantic dependency guesser or proof Planner.

TASK remains WHAT/WHY. Executor identity, model, effort/native profile, lease,
version activation consent, correction/resource/token budget and feedback
delivery metadata MUST NOT become operative TASK fields. They belong to the
Human-authorized operational admission/delegation bound to the RUN. Human's
selection is never inferred from an old TASK, roadmap entry, mapping, model
capability, repository default or transport availability. This amendment
neither selects an executable future schema/policy name nor changes return
affinity or H4 origin authority. Previously authored affinity is preserved;
`TASK-329` uses its supplied `LEGACY_REPOSITORY_DEFAULT_ROUTE` without claiming
origin-affine proof or granting a fallback to other historical TASKs.

## C. Normative preterminal state-transition contract

### C.1 Terms and immutable admission binding

Under prospective **v1.3**, retaining v1.2's three-operation boundary, this
extension is limited to operation
`PRIMARY`, `REPAIR` or `REMEDIATION` on one already admitted RUN `R` whose
canonical status is `ACTIVE` and which has no terminal outcome. Each operation
requires exact eligible version/implementation/policy/profile selection and
authenticated finite Human opt-in for **that RUN at its own admission**, after
D.1's source-bound supported-operation checks. Operation eligibility is individually checked, never inherited
from another operation or RUN. DIRECT_CANDIDATE, preadmission work, transport
events and every terminal RUN are excluded. Historical v1.0/v1.1 eligibility
is unchanged; neither acquires the three-operation extension by analogy.
V1.2 retains its published limits-without-defaults contract; only exact v1.3
selection can use C.8's resource profile.

The three-operation design is not a claim of three implemented production
adapters. D.1 enables bounded v2 Codex PRIMARY completion and separately
consented v3 native interactive PRIMARY under minimum-sufficient-v1.
REPAIR/REMEDIATION production opt-ins STOP; their ordinary nonopted legacy routes retain their
original authorization. The local preactivation codecs/callbacks retain their
historical meaning and cannot provide remote delivery authority.

`C_i` is a distinct committed clean candidate at exact Git SHA. `K_i` is the
Runtime-owned immutable preterminal proof checkpoint for `C_i`, with ordinal
`i`. `F_i` is bounded typed factual feedback bound to `K_i` and its evidence.
These are subordinate records, not new TASKs, RUNs, RESULTs, FAILUREs, REVIEWs,
REPAIRs or REMEDIATIONs. A checkpoint's proof outcome FAIL is nonterminal.

Every transition MUST authenticate and bind:

- Exact TASK id/revision, source/blob/envelope binding, complete acceptance set;
  admitted RUN identity/source/record, operation, immutable base SHA and policy
  version; unchanged authorized inspect/modify scope and non-goals, plus the
  applicable immutable operation authorization below, including its exact
  source/currentness, action/scope and original/affected verification bindings.
- Reviewed proof-contract and mapping identities/revisions/digests; actual
  candidate SHA, proof populations/conditions and evidence provenance.
- Exact Human opt-in/delegation/budget authority; selected Executor, native
  profile/model/effort **of R**; same native lease identity/generation and
  currently live lease for R, authenticated from actual authoritative state.
- Checkpoint ordinal, predecessor ordinal/digest/candidate and content digest;
  feedback identity/digest/consumption state; monotonic resource facts and the
  existing terminal-state exclusion.

Opaque pins, hashes, caller booleans and transport acknowledgements are not
proof of authority or currentness. They must resolve to authenticated canonical
and operational sources. Semantic obligations and reviewed meaning cannot
change inside the loop. Candidate-specific condition binding may change only
as mechanically authorized by the admitted version/mapping. A required new
semantic or mapping revision outside that authorization is a STOP for separate
Human/Brain action, not a Runtime remapping shortcut.

The admitted base never changes because `main` moves. No silent rebase,
reset/amend of a checkpointed candidate, lineage substitution or adoption of
a different source is allowed. Existing exact-source integration/publication
checks remain independent and may block publication later.

The following operation-specific contracts are conjunctive with all shared
guards; a shared scheduler never merges their permissions:

**PRIMARY authorization.** Preserve the original admitted TASK identity and
revision, authenticated source, immutable base, complete acceptance, original
inspect/modify scope, constraints and non-goals throughout C_i/K_i/F_i.
Runtime must discharge the full original reviewed semantic proof obligations
and complete coverage mapping, including required comparisons and distinct
integration/concurrency/ordering conditions. Feedback permits only in-scope
implementation iteration under that original PRIMARY authority. It imports
no AUTHOR_REPAIR action, REVIEW finding, REMEDIATION scope or permission to
change the TASK, acceptance, proof adequacy or required conditions.

**REPAIR authorization.** Bind the exact predecessor **admitted failed RUN and
its immutable terminal FAILURE**, its original TASK/revision/base and failure
evidence/source, and the exact immutable **currently authorized AUTHOR_REPAIR**
record/action and correction scope admitted for R. Authenticate that record's
source, currentness and predecessor relation; a merely asserted FAILURE id
or stale/superseded repair authorization is insufficient. The authorized
action kind, limits and source lineage remain fixed. Original TASK proof,
origin/failure verification and authorized affected verification retain their
required coverage under the reviewed policy/mapping, with overlap normalized
only by proven equivalence. Unaffected VALID proof may be reused under C.5;
feedback cannot waive required original/origin proof or widen affected scope.
Any preterminal correction must address a causal failed candidate obligation
inside that same AUTHOR_REPAIR action/scope. It cannot replace the action or
strategy, convert an evidence-only action into a code fix, authorize unrelated
repair, revise the predecessor FAILURE or reopen that earlier terminal RUN.
The new REPAIR RUN's own exact source/base and authorization remain immutable.

**REMEDIATION authorization.** Bind the exact authenticated **CHANGES_REQUIRED
REVIEW** decision/source, reviewed RUN/RESULT/EVIDENCE and exact reviewed source
SHA, original TASK/revision, correction predecessor, and the immutable current
remediation authorization/source/base/action/scope admitted for R. Bind the
selected **outstanding finding**, or the exact membership and explicit approval
of a causal finding group where separately authorized. Authenticate current
outstanding status and source/currentness, rather than trusting caller finding
labels. Reject unrelated, already closed, newly added or unauthorized findings;
no implicit group expansion, strategy replacement, re-planning of the original
TASK or fresh PRIMARY is permitted. Required authorized **affected verification**
and the findings' semantic proof coverage remain complete under the reviewed
mapping. Preserve unaffected root proof and review conclusions only where
their applicability remains authenticated; a changed conclusion cannot be
silently retained. A failed candidate obligation permits correction only
within those same selected findings, authorized action and minimum supporting
scope; otherwise STOP for separate rightful authorization. Independent
**DELTA review** must later determine resolution of the selected finding(s)
and material defects directly introduced by the correction, revisiting prior
conclusions only as relevantly invalidated. Feedback neither closes findings
nor substitutes for that review or exact Publisher publication.

For all three operations, the same Executor is **R's actually selected and
currently delegated native Executor**, with R's exact model/effort/profile and
same live lease identity/generation throughout its checkpoints. Do not assume
the Executor or lease of a preceding PRIMARY persists into a separately
admitted REPAIR or REMEDIATION. Another RUN's Executor, a consent record from
prior work, historical sticky-worker rule or repository default grants no v1.3
permission. Existing lawful delegation rules still apply at each new admission;
the actual current RUN binding and its explicit finite Human consent must be
independently authenticated. For v1.3, C.8 resolves the published defaults plus
valid operational overrides and freezes correction/checkpoint counts, wall and
verification time, and any applicable token cap at admission. Feedback,
recovery or operation change never renews consent or changes those limits.
Executor owns HOW within the exact
authorized action; Runtime supplies only bounded factual proof feedback.

### C.2 Strictly monotonic sequence and one terminal boundary

The authorized sequence is:

```text
one admitted ACTIVE R: PRIMARY | REPAIR | REMEDIATION
  (exact v1.3 per-RUN opt-in + immutable operation authorization + C.8 limits)
  -> committed C1 -> Runtime checkpoint K1
  -> factual failed-proof feedback F1, only if C.3 is satisfied
  -> same current-RUN leased Executor implements within that unchanged authorization
  -> distinct committed C2 -> Runtime checkpoint K2
  -> ... within the original finite bounds
  -> exactly one terminal RESULT or FAILURE
```

Ordinal order is strictly `1, 2, ...`; Git SHA text is not a numerical clock.
`K_1` has no predecessor. Each newly accepted `K_i` for `i > 1` has exactly the
last accepted `K_(i-1)` as predecessor and a unique new candidate on its committed
descendant lineage. It cannot reuse the base or a previously checkpointed
candidate. A new SHA alone is insufficient: an empty commit, unrelated change
or unestablished causal/progress basis does not qualify as a correction.
Accumulating observations for one candidate before sealing its checkpoint
does not manufacture another correction or another checkpoint ordinal.

Each sealed checkpoint commits to all C.1 bindings, proof outcomes and
outstanding obligation states, original evidence references/digests, and
resource consumption. Sealed records and their predecessors are immutable.
Later checkpoints append; they do not revise prior facts. Each continuation
has a single consumed feedback identity and accounted correction reservation.
Counters cannot decrease, reset on process restart or exceed the original
Human limits. With effective limits `N=max_corrections` and
`M=max_checkpoints`, there can be at most `min(N, M - 1)` correction
transitions and `min(M, N + 1)` candidate checkpoints. K1 counts against M;
finalization reusing K_i does not create another checkpoint. C.8 specifies
v1.3's default-fill, override and immutable effective-limit authority.

| Current state/event | Permitted effect | Prohibited effect |
| --- | --- | --- |
| ACTIVE R, committed C1 | One scheduler establishes and seals K1 bound to C1. | Interim canonical RESULT/FAILURE or independent verification owner. |
| Complete stable candidate proof FAIL in K_i, every continuation guard true | Bind factual F_i; the same delegated Executor may make one scoped correction; accept distinct C_(i+1)/K_(i+1) only after rechecking all guards. R stays ACTIVE. | New RUN, model/Executor switch, runtime-selected fix strategy, scope or consent expansion. |
| Final current checkpoint discharges every mandatory obligation | Runtime validates exact coverage/claims/source and emits one canonical terminal RESULT bound to the final committed SHA, reusing valid equivalent checkpoint proof. | Ceremony rerun, synthetic subject relabel, skipped comparison/integration proof or second terminal. |
| Decisive required failure, continuation unavailable/declined, hard stop or exhausted bounds | Stop mutation and use the existing admitted-RUN terminal FAILURE boundary once, preserving cause, evidence and outstanding obligations. Attribution may remain UNKNOWN when no independent required comparison remains. | Endless retries, hidden new authorization, conversion of uncertainty into PASS or overwriting the root failure with cleanup noise. |
| Terminal RESULT or FAILURE already exists | Idempotent retrieval/reconciliation of that exact terminal fact only. | Reopen ACTIVE, append corrective checkpoints, replace terminal kind/source, or create a competing terminal artifact. |
| Terminal FAILURE requiring correction | Existing AUTHOR_REPAIR binds the exact immutable FAILURE, separately authorizes correction and admits a distinct new REPAIR RUN. That new RUN requires its own current Executor/lease and exact versioned finite opt-in to use C.1; v1.3 limits resolve under C.8. | Reuse the failed R as ACTIVE, change the authorized action or treat prior opt-in/budget/worker identity as repair-loop permission. |
| Terminal RESULT followed by REVIEW CHANGES_REQUIRED | Existing narrow REMEDIATION binds authorized outstanding finding(s), affected verification and independent delta review. A separately admitted new REMEDIATION RUN requires its own current Executor/lease and exact versioned finite opt-in to use C.1; v1.3 limits resolve under C.8. | Convert the earlier finding into feedback for the terminal RUN, add unauthorized findings or rerun the root TASK as a hidden fresh PRIMARY. |
| Pre-AIOS carrier/transport/runner failure, `run_created=false` | Record bounded operational facts under existing transport ownership. | Fabricate a canonical RUN, FAILURE, AUTHOR_REPAIR lineage or execution success. |

Exactly one mutually exclusive terminal RESULT or FAILURE MUST be durably
established through the existing Runtime terminal authority. Concurrent delivery,
crash recovery or finalization retries may return the same fact, never create
two outcomes. If terminal persistence/currentness cannot be authenticated,
mutation stops and recovery must reconcile the exact existing state before
any terminal write; absence of a transport receipt is no permission to write
another outcome. No terminal RUN is reopened. REVIEW and publication remain
subsequent independent authorities, not checkpoint transitions.

### C.3 Conjunctive continuation and hard-stop guards

For correction authorization based on `K_i`, the following guards MUST hold
at feedback delivery and before Executor continuation. When accepting
`C_(i+1)`/`K_(i+1)`, revalidate the enduring identity/scope/authority/lease/resource
guards and the integrity and single consumption of the originating `K_i`/`F_i`.
The new candidate's progress is checked at acceptance. A successful new
checkpoint need not contain a FAIL: it may finalize through C.2.

1. For **production same-RUN continuation**, the exact eligible published
   implementation/version and same-live-recipient mechanism are supported under D.1, this future work
   explicitly opted in at its own admission for its exact PRIMARY, REPAIR or
   REMEDIATION operation, and R remains the same admitted ACTIVE RUN with C.1's
   unchanged operation authorization and no terminal. **Implementing and
   independently Runtime-verifying L3 under an existing admitted engineering
   TASK does not require production continuation to be previously ACTIVE.**
   Ordinary offline tests and Runtime
   verification may exercise the implemented L3 core and its guards through the
   existing Runtime under that TASK's original policy/authority. Such proof
   neither grants its engineering RUN a new production continuation permission
   nor authorizes preimplementation RUN mutation. The presently supported
   production operation performs no in-turn continuation; an ACK, queued record
   or local callback cannot satisfy the missing native channel. The retired
   VP-06/VP-07 package sequence is not an activation prerequisite.
2. Authenticated current TASK/RUN/base/contract/mapping/candidate/feedback
   bindings and trustworthy committed clean state are available. At least one
   complete, stable, decisive, non-conflicting FAIL of an approved **candidate**
   proof is established in a complete checkpoint observation with all required
   population/phase/condition facts. Partial, stale/ref-moving, flaky,
   conflicting, ERROR or UNKNOWN observations cannot authorize continuation;
   an isolated failing item cannot hide those conditions. Raw pytest nonzero,
   a comparison failure alone or a caller FAIL label is insufficient.
3. R's original authenticated finite Human opt-in/delegation, unchanged scope,
   acceptance and operation-specific action/findings/verification authorization,
   actual selected sticky Executor/profile/model/effort and same live native
   lease identity/generation remain valid. Runtime and transport cannot select
   replacements, inherit another RUN's worker/consent or infer renewed consent.
   Lease loss, expiration, uncertainty or authority drift is STOP.
4. The correction needs no new Human risk acceptance, semantic decision by
   Brain, expanded intent, changed non-goal or scope exception. Changed or
   unknown risk/semantic-choice facts are STOP; Runtime cannot adjudicate them
   into consent or decide a strategy. Weakening acceptance/tests to obtain green
   does not satisfy proof and remains subject to independent semantic review.
5. Original immutable effective limits under C.8 on corrections, checkpoints,
   cumulative RUN wall and cumulative Runtime verification, plus token usage
   when a non-null cap applies, have trustworthy spent/reserved accounting and
   enough remaining capacity for lawful work. Verification is a subset of wall,
   never extra time. Missing/untrustworthy applicable accounting is STOP;
   `max_tokens=null` requires no mandatory token-cap input or finite KA-01 token
   ceiling. Costs include applicability/checkpoint/evidence/feedback/re-entry
   and infrastructure/cleanup work, not only test execution. Design-validator
   ceilings from TASK-328 are not live Human consent. Exhaustion/overrun cannot
   enlarge limits or waive mandatory proof.
6. The authenticated failed candidate obligation supplies the causal basis for
   continuation. At acceptance, the correction must be a new committed in-scope
   candidate with a factually established progress basis against that obligation.
   Same/previous SHA, unrelated or empty delta, duplicate feedback used to seek
   another effect, repeated consumption, no progress or unknown progress is STOP;
   exact duplicate receipt may only reconcile the prior effect under C.7.
   Runtime checks authorized identities and factual guards; it does not predict
   a successful fix, choose or semantically invent HOW.

Unknown, stale, uncommitted, dirty, malformed, incomplete, unstable, conflicting,
tampered or invalid authority-bearing observations cannot enable continuation
or PASS. Structural/infrastructure instability, unsupported worker state and
interruption with unprovable currentness also stop safely. UNKNOWN applicability
can undergo only already authorized bounded deterministic resolution as in
C.5; it grants no Executor continuation. If it remains unresolved, fail closed.

A STOP revokes permission for further mutation in this loop. For an
authenticated admitted RUN it leads through the existing terminal failure
boundary, with reason and unresolved proof preserved; where state is uncertain,
bounded reconciliation must establish that terminal boundary exactly once.
Escalation reports facts to the rightful Human/Brain authority. It does not
grant new admission, budget, semantic strategy, REPAIR or REMEDIATION authority.
For REPAIR and REMEDIATION, STOP also cannot mint or replace AUTHOR_REPAIR,
select another finding/group, widen the current action/scope or edit canonical
history. Terminal re-entry is always rejected, for R and every predecessor.

### C.4 Factual typed feedback only

The bounded feedback envelope carries the exact C.1 TASK/RUN/operation/
authorization/subject/checkpoint bindings, feedback identity/digest and factual
remaining guard/resource state. Any action/finding reference identifies only
the existing authorization; feedback cannot create or amend it.
Its proof items contain only:

| Typed fact | Required binding |
| --- | --- |
| Proof/obligation identity and revision/digest | Approved mapping and contract, with all affected acceptance IDs retained. |
| Exact observed subject and conditions | Actual candidate SHA, checkpoint ordinal/digest and full proof condition binding. |
| Proof state | Typed outcome, completeness, stability/conflict and applicability state; FAIL, ERROR and UNKNOWN stay distinct. |
| Evidence references | Authenticated immutable source/raw/digest references sufficient to inspect the observation; no invented diagnostics. |

ERROR/UNKNOWN and guard failures may be reported factually but do not become
eligible candidate FAIL or continuation permission. Feedback is not a review
finding, repair authorization, code-edit request, executable command, patch,
implementation plan or semantic diagnosis/strategy from Runtime. No field may
carry proposed fixes, file-edit instructions, model/Executor selection, test
weakening, scope expansion or concealed command lists.

Raw logs and tool output are untrusted evidence data. Instructions embedded in
them have no authority and MUST NOT be promoted into feedback instructions or
semantic decisions. Delivery merely presents authenticated facts to the already
authorized Executor; the Executor owns HOW. Reference/record size and collection
bounds must be explicit in the separately admitted implementation; oversized or
malformed feedback fails closed. No new reasoning agent interprets feedback.

Security, privacy and provenance remain subject to existing repository and
Runtime authority: disclose only the minimum authorized factual context and
evidence references, protect secrets/private data under existing access and
redaction policy, and retain authenticated raw provenance without altering the
original observation. Redacted delivery is no substitute for required evidence
available to rightful verification/review owners; if lawful access cannot
establish a required fact, keep UNKNOWN and STOP. No caller-provided consent,
VALID flag or log content becomes a new trust root. Feedback/recovery performs
no silent background repair or competing mutation. Hidden weakening or
substitution of tests, fixtures, workers, environment or contract conditions
cannot satisfy proof or evade the applicability checks in C.5.

### C.5 Authenticated source-to-target proof applicability

Before any evidence is reused, Runtime MUST authenticate its original source
and an immutable applicability witness from that source to the exact current
target. The witness binds the source evidence/RUN/subject/raw digests, target
RUN/candidate, obligation/acceptance and contract/mapping pins, permitted
operation-specific authorized lineage, and all relevant conditions and dependency
footprints. Every relevant dimension needs affirmative coverage or an
authenticated explicit declaration that it is inapplicable;
omission/default/unknown cannot mean unchanged.

The footprint MUST cover relevant implementation/subject dependencies and all
of the following distinguishing proof dependencies:

- Test code and proof population; fixtures, setup/teardown, shared helpers and
  shared state, including cross-test and generated-state effects.
- Interpreter/toolchain/observer, environment/configuration, admitted policy
  and execution profile, worker mode/count and worker-state requirements.
- Concurrency topology and interference; ordering and repeatability/repetition
  conditions; collection identity, completeness and observation behavior.
- Unit/whole-suite/integration context and external boundaries; evidence
  kind/schema and complete canonical phase/item observations where required.

| Applicability state | Required behavior |
| --- | --- |
| VALID | Authenticated source proof remains applicable to the exact target under every relevant dimension and the reviewed mapping. Preserve its actual outcome: source FAIL remains FAIL. Only a valid satisfactory proof can discharge the corresponding target obligation. |
| INVALIDATED | Established relevant dependency/condition change invalidates the affected proof. Preserve history and execute the minimum lawful new target proof needed by the approved mapping. |
| UNKNOWN | Missing, stale, conflicting or insufficient provenance/footprint/currentness. Never infer VALID, discharge acceptance or grant PASS/continuation. Resolve only within an already approved finite deterministic resolution scope, otherwise STOP/BLOCK. |

Each source observation keeps its original `subject_sha`, source RUN, raw
record/digest and actual outcome. A separate authenticated source-to-target
binding explains applicability; it does not claim execution occurred on C2
when it occurred on C1. A synthetic subject label, self-certified VALID flag,
witness digest alone, unchanged command/nodeid, filename overlap/disjointness,
imports or elapsed time cannot authenticate reuse. Changed tests, fixtures,
helpers, policy, environment, worker or contract/mapping conditions must
invalidate the relevant proof or remain UNKNOWN; they cannot preserve proof by
keeping an old label. Unauthorized contract/mapping changes also require STOP
under C.1/C.3, rather than synthetic PASS or a new correction permission.

Distinct integration, concurrency, ordering, fixture/shared-state,
serial/parallel worker and approved repetition obligations remain distinct.
A targeted green observation cannot discharge a required broad/integration
proof. Reviewed equivalence covers full semantic claim and every distinguishing
condition, not nodeid or command equality. An approved distinct repetition
purpose requires its own obligation; renaming equivalent work is not one.

Exactly **one Runtime-owned proof scheduler** covers checkpoints and terminal
finalization. It must plan coverage/equivalence before execution, reuse VALID
satisfactory evidence and avoid equivalent duplicate execution. Newly required
or INVALIDATED proof needs lawful affected execution under C.1's unchanged
original/affected authorization, never a Runtime-created obligation or scope;
UNKNOWN needs bounded resolution or a fail-closed stop. Consolidation may
remove overlap only when soundly proven and cannot erase independent coverage. The successful final
checkpoint discharges terminal proof after exact validation without rerunning
equivalent execution. No second scheduler, verifier agent or simultaneous
V2/new-policy execution is allowed for the same admitted work.

### C.6 Candidate-first failure and all six base replay gates

Candidate-first is the default. Candidate PASS or FAIL alone authorizes neither
failure reproduction nor base materialization, base collection, base execution,
narrow attribution probe or broad fallback. A complete decisive blocking
candidate failure can terminate with attribution UNKNOWN when no independent
mandatory comparison/failure-contract proof remains to be executed. Outstanding
independent comparison obligations must remain explicit and cannot be silently
discharged. Incomplete/unstable/conflicting observation is no decisive proof.

Before **any** base replay cost, including materialization or collection, Runtime
MUST authenticate all six affirmative eligibility facts with exact reviewed
basis, bindings and bounds:

| Gate | Required affirmative fact |
| --- | --- |
| BR-1 — APPROVED_NECESSITY | An approved baseline-dependent comparison obligation actually requires the exact base, rather than ordinary candidate conformance. |
| BR-2 — DECISION_RELEVANCE | Comparison resolves a currently undecided required outcome or a distinct mandatory comparison; diagnostic curiosity or labeling every FAIL is insufficient. |
| BR-3 — NO_VALID_ALTERNATIVE | Immutable comparable history was inspected without executing base; adequate evidence is absent or proven invalid. VALID alternatives require reuse; UNKNOWN alternatives do not establish this gate. |
| BR-4 — MINIMUM_LAWFUL_SCOPE | The smallest reviewed scope that covers the mandatory comparison and a bounded declared method are established. A broad suite is no fallback from an ineligible/failed narrower attempt. |
| BR-5 — EXACT_COMPARABILITY | Exact source/base/target identities and full population, test/fixture/helper/state, toolchain/environment/profile/worker, concurrency/order, collection/integration/repetition and evidence conditions admit the comparison. Incompatible/unknown conditions permit no unsupported attribution. |
| BR-6 — AUTHORITY_COST_BOUNDS | The operation fits the original approved authority, exact minimum scope and explicit finite execution/cost bounds, with one approved attempt and no equivalent already-covered proof repeated. |

Missing, false, unknown or contradictory facts deny replay; six asserted labels
are insufficient. Denial preserves decisive failure or explicit unresolved
comparison and cannot grant PASS. Budget cannot waive a required comparison.
An accepted-preexisting-failure comparison, if part of the approved contract,
still needs these gates. Automatic same-SHA failure reproduction and the chain
candidate broad → exact reproduction → base collection → base probe → base
broad fallback are prohibited. Original verification cause and complete failure
provenance survive observation/cleanup faults; those faults cannot fabricate
successful proof or replace the root cause.

### C.7 Crash, replay, duplicate checkpoints and bounded effects

The separately admitted implementation MUST provide durable, authenticated
checkpoint/feedback/resource/terminal facts under existing Runtime ownership.
This specification grants no storage technology or independent lifecycle store.

- An exact duplicate checkpoint or feedback delivery resolves to the already
  accepted identity/acknowledgement. It cannot consume another correction,
  invoke the Executor again, repeat proof, append another ordinal or create a
  new RUN. Idempotency binds R, TASK version, operation and immutable
  authorization, candidate, ordinal/predecessor, checkpoint digest and feedback
  digest, not a transport event label alone. Duplicate receipt never renews
  consent or authorizes another effect; attempted reuse for new work is STOP.
- Same ordinal/identity with different content, missing predecessor, sequence
  gap, stale candidate, tampered history or competing writer is a hard conflict;
  reject and stop rather than repair history automatically.
- Correction dispatch/consumption and cost reservation must be reconciled
  durably so a crash cannot reset counters or grant the same action twice.
  Charged/reserved work remains attributable. Unknown execution effects cannot
  be treated as zero cost or permission to rerun.
- Recovery may reconcile existing authenticated observations/candidates and
  continue only the same still-ACTIVE admission with the same current-RUN
  Executor/live sticky lease/profile and original finite operation authority.
  It is no automatic failover, Executor reassignment, model switch or fresh
  admission. Lost lease, nonrecoverable worker state or unprovable
  effect/currentness stops; do not
  blindly reproduce an interrupted proof or re-enter an Executor.
- A terminal artifact is reconciled idempotently and remains terminal. The
  existing Runtime arbitrates exactly one terminal write. Later correction
  uses AUTHOR_REPAIR/new RUN or REVIEW-driven REMEDIATION as in C.2.
- A pre-admission transport event creates no canonical RUN/FAILURE, and its
  redelivery cannot independently allocate competing lifecycle records.
  Existing admission/delivery ownership remains intact. Operational receipts
  never assert verification, review, publication or consent by themselves.

Checkpoint, applicability, feedback, delivery and reconciliation work must all
fit the original measured finite bounds. Replay is reconciliation of facts,
not authorization for another implementation/proof attempt. There is no
generalized retry, polling, escalation-to-broader-proof or self-extension
authority hidden in crash handling.

### C.8 Version 1.3 default resource profile and operational overrides

#### C.8.1 Published defaults and exact eligibility

Human's **2026-10-21** decision defines this prospective KA-01 **v1.3** profile:

| Parameter | Published default | Meaning |
| --- | --- | --- |
| `max_corrections` | `2` | Maximum same-RUN correction transitions after K1; zero is a valid override. |
| `max_checkpoints` | `3` | Maximum candidate proof checkpoints, including K1 and any final candidate checkpoint. |
| `max_run_wall_minutes` | `240` | Cumulative elapsed wall time of the whole admitted RUN through its terminal boundary. |
| `max_runtime_verification_minutes` | `180` | Cumulative Runtime verification time across checkpoints and finalization, INCLUDED within the RUN wall time. |
| `max_tokens` | `null` | Optional cap; null means no KA-01-specific token ceiling, while external provider/account limits remain in force. |

These are published defaults, not hard ceilings on Human's future valid
overrides. They apply only to future eligible **versioned, Human-opted-in,
admitted ACTIVE PRIMARY, REPAIR or REMEDIATION RUNs** under C.1 and D.1.
The profile is not an executable schema/policy identifier and does not make an
existing implementation eligible. Its selection grants no per-RUN consent, lease, Executor
choice, repair action, finding scope or production continuation authority.
Historical v1.0/v1.1/v1.2 work never receives these defaults, including missing
limits in an older admission. DIRECT_CANDIDATE, preadmission and terminal work
remain excluded.

#### C.8.2 Separately authenticated per-field override and admission binding

Human may independently override any of the five fields for an **exact TASK id
and revision** and explicitly named applicable operation(s) from PRIMARY,
REPAIR and REMEDIATION. This is separately authenticated **operational
delegation**, never an operative field of the semantic TASK, acceptance,
proof mapping or planning document. A multi-operation override must name its
operations explicitly; a PRIMARY record does not apply to REPAIR/REMEDIATION
by analogy. A different TASK revision requires its own authority.

At each future admission Runtime MUST:

1. Authenticate the exact eligible published KA-01 version, implementation,
   admitted policy and resource profile identity/revision/digest and their
   current Human selection for **this operation**. Authenticate the operational
   override's rightful Human source, immutable identity/digest, exact TASK id/
   revision, explicit operation applicability and currentness, including
   withdrawal/supersession. An asserted label/hash/approval boolean is not proof.
2. Resolve each field independently: take that field's valid applicable Human
   override when present, otherwise take the exact selected published default.
   No override at all means all five defaults. Explicit `max_tokens=null` is
   valid; no token input is required. Reject unknown fields, malformed records,
   wrong TASK/revision/operation/version, stale/revoked authority and ambiguous
   or conflicting overlapping selections/overrides. Never silently default
   past an invalid supplied override or use a last-writer-wins permission.
3. Validate the **complete merged effective profile** under C.8.3, including
   cross-field consistency. Independently authenticate current per-RUN Human
   **per-RUN** opt-in, exact original operation-specific source authority in
   C.1, current selected Executor/model/effort/native profile and its live lease
   identity/generation. A reusable TASK/revision resource preference alone is
   not per-RUN consent. Re-authenticate its applicability on every admission.
4. Freeze all five concrete effective limits (including the explicit null token
   value), their units/accounting boundaries, default and override provenance,
   exact version/policy/profile pins, per-RUN consent and operation/Executor/lease
   binding into **this RUN's immutable operational admission**. No inference
   from a previous RUN's effective limits, opt-in, lease or unused balance is
   permitted, even for the same TASK/revision and operation.

Once admitted, neither publication of a new default nor later overrides,
feedback, re-entry, process restart or recovery can raise, refill, reset or
replace R's frozen bounds. A changed selection applies only through a lawful
future admission with fresh per-RUN consent; it does not retarget R. Withdrawal,
staleness or inability to authenticate continuing authority stops R under C.3;
it never supplies an automatic replacement profile, Executor or RUN.

#### C.8.3 Validation, accounting and exhaustion

- `max_corrections` MUST be an integer >= 0; `max_checkpoints` MUST be an
  integer >= 1. Booleans, fractional counts and negative values are invalid.
  Correction transitions MUST NOT exceed
  `min(max_corrections, max_checkpoints - 1)`. Checkpoint/correction limits are
  independent ceilings; neither can manufacture capacity in the other.
- Both minute bounds MUST be positive finite numbers, with
  `max_runtime_verification_minutes <= max_run_wall_minutes`. Reject zero,
  negative, boolean, NaN, infinity, missing effective bounds, invalid units and
  lossy/coerced values. A non-null `max_tokens` MUST be a positive integer;
  boolean, zero, negative, fractional or non-finite token caps are invalid.
  Null is the sole uncapped KA-01 token value, not UNKNOWN or an unlimited
  correction/checkpoint/time permission. Validate the merged profile even if
  every override field was individually valid.
- RUN wall time starts at the admitted RUN's ACTIVE execution boundary and
  accumulates through terminalization: Executor work, waits, delivery,
  feedback/re-entry, verification, evidence/checkpoint/applicability work,
  infrastructure and cleanup all consume it. Recovery and interruptions never
  restart the clock. Runtime verification accumulates all canonical proof work
  across **all** checkpoints and finalization, including its preparation,
  collection, materialization, applicability and evidence/cleanup overhead.
  It is a measured subset of that same wall interval, never another allowance:
  the defaults permit at most 240 total minutes, not 240 + 180. Parallel proof
  within one verification interval does not multiply elapsed time; distinct
  verification intervals accumulate. Reusing VALID proof does not re-charge
  its original execution, but actual current validation/IO time still counts.
- Runtime MUST authenticate monotone spent and cumulative reserved totals,
  attributable reservation/settlement identities and remaining capacity for
  every applicable bound. Reserve a correction before its single consumption
  and a checkpoint before its proof work; enforce both time deadlines during
  work. Spent plus outstanding reservations cannot exceed a bound. Settlement
  converts an existing reservation into accounted use exactly once, without
  a second debit; cumulative reservation and spent counters never decrease or
  reset. Reconcile provably unused reservations through attributable records,
  never by erasing history, refunding consumed work or treating unknown effects
  as zero. No new work may depend on an unverified release.
- For a non-null token cap, usage and reservations for the complete applicable
  RUN cost population MUST be authenticated and cumulative, including
  correction/feedback/re-entry costs. If usage cannot be reliably accounted
  within that cap, STOP. Null requires no invented finite token ceiling,
  mandatory token input or token-usage estimate as a KA-01 admission guard;
  it does not waive external provider/account limits or any other accounting.
- Reject invalid/stale limits or overrides at admission; during ACTIVE work,
  exhausted correction/checkpoint capacity forbids the next transition or new
  checkpoint. Zero corrections still permits K1. A fully proved final K_i at
  the count ceiling may finalize once using its valid proof within the remaining
  time/applicable token bounds, without a new correction/checkpoint charge.
  Exhausted wall/verification time or applicable tokens, overrun, absent or
  unverifiable accounting, clock discontinuity or UNKNOWN remaining capacity
  MUST stop further substantive work safely. Preserve the exhaustion cause and
  any bounded terminal reconciliation facts; no overrun buys more work.

C.7 idempotency covers reservation, dispatch, consumption, checkpoint acceptance
and settlement atomically under the **one existing Runtime authority**. Replay
or duplicate delivery reconciles the same effect; it cannot double-debit,
repeat proof, add a checkpoint/correction, reset counters or reopen a terminal
RUN. Actual reconciliation time remains attributable. Unverifiable effects
STOP rather than authorizing a second attempt.

Resource STOP preserves the complete mandatory proof population, outcomes,
source bindings and outstanding obligations. Limits never justify shrinking
coverage, skipping required integration/comparison proof, suppressing a
mandatory verification condition, relabeling UNKNOWN as PASS or claiming
unexecuted proof. If required proof cannot finish lawfully, use C.2/C.3's
single terminal FAILURE boundary with the real cause and outstanding proof;
no second terminal authority, budget renewal or new repair/remediation
permission follows. The bounded L3-05 machinery is a published foundation;
D.1 specifies its supported ordinary production integration and the mechanisms
that remain unavailable. This document itself supplies no accounting facts.

## D. Publication, engineering, measurement, activation and rollback

### D.1 Version 1.3 source-bound activation and prospective default

The controlling new Human decision selects **TASK-425 r1** as the single NEXT
implementation. D.1.2 introduces its distinct prospective policy. The TASK-423
and TASK-424 decisions below remain exact historical activation records; they
do not select another future implementation stage.

Human prospectively supersedes the former conjunctive staged G1/G2/G3/G6
sequence and the future L4 G4/G5 duties for new supported v1.3 admissions.
TASK-423 supplied the published bounded-primary foundation. The newer explicit
Human priority selects **TASK-424 as the one integrated native interactive
PRIMARY implementation-and-activation TASK**. It contains the native transport,
contract change, ordinary caller integration, focused tests and bounded source
observation. Its
ordinary Runtime verification, independent semantic review and exact Publisher
publication remain separate authorities over the same delta. They are not new
Lean VP stages and do not trigger a second activation TASK, manual cutover,
pilot, shadow evaluation or automatic roadmap advancement. Earlier published
specifications did not already permit this change; their exact sources remain
immutable.

The retained v2 production configuration is **KA01-v1.3-bounded-primary-v1**;
the following v2 restrictions remain exact and do not acquire v3 privileges:

- Existing `operator.run_task(..., ka01_admission=AdmissionRequest(2, ...))`,
  Codex, PRIMARY, `minimum-sufficient-v1`, KA-01 version `1.3` and
  `KA01-v1.3-default-resource-profile`. This caller first performs its ordinary
  independent TASK/current-main/new-RUN admission and resolves the original
  Human-selected execution profile and live lease. The opt-in cannot create,
  reassign, reconstruct or resume a RUN.
- Authenticate the original configured upstream's exact unambiguous
  `refs/heads/aios/ka01-v13-consent/<RUN>` orphan selector containing canonical
  `.ai/transport/ka01-v13.json` bytes equal to that typed request. The selector
  is a locator, not permission. Use the existing ingress policy and Human Issue
  authenticator for separately pinned current open selection, optional field
  overrides and exact fresh RUN consent; reject editing, withdrawal, identity
  mismatch, stale source and competing refs. Fetching a selector object is only
  delivery. Main must still equal the original admission base.
- The selection pins implementation, version, resource profile, exact TASK
  id/revision, operation, verification policy, configured remote and normative
  path/digest at the exact current main SHA. Bind the running first-party
  activation, Dispatcher, Executor, Runtime, verification and Codex consumer
  source bytes to that source, allowing only Git text newline conversion.
  Missing, changed or unsupported source STOPs rather than falling back.
- Consent uses mode `PRODUCTION_BOUNDED_PRIMARY`, the same implementation and
  selection digest, exact RUN/task/operation/Executor, original admitted RUN
  bytes, resolved execution profile, operation source and effective-limit
  digests. C.8 default-fill and independently authenticated overrides apply;
  no prior RUN's balance or consent is inherited. A nonnull token cap STOPs
  while complete authenticated cumulative usage is unavailable.
- Enable actual C.8 admission/withdrawal/resource enforcement around the
  existing single native invocation and Runtime completion. Clamp the native
  watchdog to remaining RUN wall time; reserve and settle one final proof
  checkpoint under the existing Runtime mutation fence; execute the exact
  authored verification once with a cumulative deadline inside RUN wall;
  use the existing canonical evidence validator and sole terminal writer.
  Preserve candidate-first verification, complete acceptance coverage,
  immutable source/profile/lease checks and ordinary post-PASS transport.
  Reservations, interrupted work and terminal intents are negative crash guards,
  never permits to retry, reconstruct a lease or reopen terminal work.
- **Unavailable:** Codex native same-original-live-recipient feedback delivery,
  in-turn C2/continuation, production checkpoint feedback/reuse APIs, REPAIR or
  REMEDIATION opt-ins, other Executors and other verification policies. These
  requested configurations STOP explicitly. No no-op, transport ACK, fixture,
  queued data, GitHub receipt or local callback is a native delivery receipt.
  Historical local version-1 preactivation remains offline and distinct.

This is a supported subset of the published foundations, not full L1-L3 or
three-operation conformance. All applicable identity, finite authorization,
progress/lease STOP and single-terminal guards remain in force. Complete
mandatory proof cannot be reduced to fit resources. Known unsafe behavior
requires STOP and ordinary explicitly authorized correction. Unsupported work
uses its original ordinary legacy route only when it was **not** opted in;
an opted-in failure never silently downgrades, retries or selects another
Executor. Removing a separate G3/G6 package does not remove these checks.

The admission/accounting source is the original Runtime's
`verification/<RUN>/ka01-v13/admission.json` and immutable event records; the
same caller's bounded `RunSummary.ka01` identifies exact source, selection,
consent, profile, resolved limits, invocation and proof counts. It explicitly
reports native feedback unavailable and review/publication not observed.
Those files and observations are not canonical EVIDENCE or live-session proof.
Runtime owns canonical evidence; no current caller, measured benefit, Reviewer
PASS, publication, uptake or downstream adoption is inferred by this source.

#### D.1.1 Separately authenticated original live interactive PRIMARY (TASK-424)

The distinguishable new configuration is **KA01-v1.3-interactive-primary-v1**.
Use the same ordinary `operator.run_task` caller with **AdmissionRequest(3, ...)**,
Codex PRIMARY, `minimum-sufficient-v1`, KA-01 `1.3` and the unchanged resource
profile. The only native interaction profile is one first-party `openai` Codex
app-server process over private stdio, one new logical thread, selected exact
model/effort and distinct bounded inference turns. Native installed support must
be observed; unsupported binary/model/protocol/observer is explicit BLOCKER/STOP.
This does not describe one uninterrupted model turn.

Version 3 uses the same exact orphan selector locator and independently
authenticated current open Human Issue sources described above. The selection's
`implementation` must equal the new identifier. Consent must use the new
implementation and **PRODUCTION_INTERACTIVE_PRIMARY**, exact fresh RUN, admitted
RUN digest, original TASK/revision/base, source operation, selected profile and
merged limits. Pin normative bytes and running first-party implementation to
exact current main at new-RUN admission, including native transport/observation
consumers. An old v2 selection/consent, flag or receipt cannot authorize v3;
changing the request alone fails authentication. No in-flight or historical
RUN can upgrade, resume, switch model, respawn, migrate or silently fall back.

Only the original Runtime may verify. At each clean committed scoped candidate,
reauthenticate original Human consent/currentness, source/TASK/constraints,
original lease/profile and live process/connection/thread. Execute the original
authored commands with the existing verifier and complete exact native pytest
observer, under the existing per-RUN mutation fence and cumulative budget.
Observer and execution/configuration helper blobs must remain bound to the
admitted base; changing those proof consumers is unsupported in this profile.
Seal source-bound immutable native K_i, including original admission/authority,
candidate/tree, actual process/session/request/turn/input/item correlation,
proof policy/toolchain/profile/collection, evidence and raw/native digests.
Versioned `AIOS_KA01_NATIVE_PRIMARY_CHECKPOINT` is distinct from historical
review-mapping L3 records; parsing either never confers authority. No review or
artifact ref is invented to impersonate the older preactivation mapping.

Only complete, stable, authentic, scoped call-FAIL with passing setup/teardown
and all original command obligations observed can produce factual bounded F_i.
Collection/setup/teardown errors, skips, unknown/partial/malformed/unstable proof
STOP. F_i contains source/checkpoint/recipient/correlation, acceptance and proof
identities and observed outcomes only: no raw logs, secrets, paths, diagnostics,
patches, semantic strategy or implementation advice. The original Executor
alone chooses HOW. An eligible FAIL creates no interim FAILURE terminal.

Before exposure, authenticate exact current K_i/F_i and original still-live
process/pipes/thread/profile/lease. Before accepting C_(i+1), reauthenticate
current authority and the source feedback/lineage, a completed correlated model
turn with matching received input and receipt, and a different clean committed
in-scope descendant tree. Persist consumption only as an original protected
Runtime effect. A request ACK, detached-file receipt, mock callback, stdout ACK,
reused thread on a new process or replacement inference session cannot satisfy
receipt or continuity. Verify C_(i+1) and require strict reduction of failed
native identities under the same complete population/conditions; no-progress STOPs.

C.8 defaults remain **2 corrections, 3 checkpoints, 240m RUN wall, 180m
cumulative Runtime verification, null tokens**. Count only protected source-bound
effects. Reserve before effects; interrupted/uncertain reservations cannot retry,
refund, replay or reconstruct authority. Inference uses RUN wall, verification
uses both clocks. Nonnull token caps STOP without authentic complete applicable
usage. This first native profile has no authenticated cross-candidate VP-03
applicability: a changed source invalidates previous source-specific proof, so
all original commands remain outstanding at that source. No final proof repeats:
the exact complete final K_i is reauthenticated and consumed by the existing
canonical evidence validator and sole original terminal writer. If applicability,
authority, budgets, identity or proof cannot support continuation, use one
FAILURE/STOP, retaining uncertainty and negative crash fences, never a new RUN.

One bounded original Runtime observation reports exact source, selected session
and actual turn correlations, checkpoints/corrections, consumed verification
budget, unavailable evidence and final RESULT/FAILURE/STOP status. A session is
reported only after actual initialization; startup failure remains unavailable.
Observation, model assertions and parsed records never replace canonical EVIDENCE,
Reviewer judgment or Publisher authority. Neither v3 nor one observation claims
savings, uptake, full three-operation support or downstream adoption.

#### D.1.2 Default bounded continuation across three coding operations (TASK-425)

The distinct prospective implementation is **KA01-v1.3-default-three-flow-v1**,
mode **PRODUCTION_DEFAULT_THREE_FLOW** (internal admission version 4). Human's
explicit 2026-10-25 choice makes bounded Runtime Verify → Fix → Verify the default
for every future newly admitted coding episode of an existing or new TASK, in
PRIMARY, CODE_FIX/CONTINUE_IMPLEMENTATION REPAIR and CODE_FIX REMEDIATION. This
same integrated TASK contains the policy, implementation and ordinary callers;
there is no later activation, cutover, pilot, L3-06, G3/G6 or L4 stage.

The original ordinary `run_task`, `run_repair` and `run_remediation` first perform
their independent family admission. TASK admission, exact failed RUN/head/root
and authorized REPAIR, or exact reviewed source/finding and authorized REMEDIATION
must succeed before policy enrollment. The current canonical main's exact
normative bytes and running first-party consumers authenticate the global policy.
No fresh KA-01 selection Issue, consent Issue or orphan selector is required.
Self-host/A1/H1R/remediation carriers need no new policy fields; they grant no
execution, model selection or correction authority. Missing/malformed/stale global
source, source drift, lost lease or changed family permission STOP; they never
select legacy, reopen a RUN, manufacture consent or spawn a replacement Executor.

Reuse TASK-424's original live Codex app-server process/stdio/thread and original
Runtime checkpoint/observer/factual-feedback/terminal path. Preflight the complete
original proof policy, observer/toolchain and actual native selected profile before
the first coding turn. K_i, F_i, receipt and terminal bind operation, original
admission and family source, completion policy, candidate, selected Executor/model/
effort, lease and same live session. F_i contains only bounded factual identities;
the Executor chooses HOW within the original authorization. No raw logs, paths,
secrets, provider/account state, diagnosis or patch advice enter feedback.

PRIMARY retains original TASK AC coverage. REPAIR retains exact failed candidate,
root/result base, authorized mutation scope, cumulative complete TASK claims and
original TASK/origin proof combination. REMEDIATION retains exact reviewed SHA,
Review finding, authorized narrow affected proof and Delta Review basis; it
returns empty claims. Original executorless reuse/no-change/direct-candidate
completion stays executorless and does not invoke a model to manufacture feedback.
Other coding actions/noncoding modes and batch findings without proven exact
continuity STOP under this policy before model work. Supported V1/V2 completion
obligations are not reduced: an unsupported complete observer yields STOP.

The common defaults are at most **2 corrections**, **3 checkpoints**, **240 minutes
original RUN wall**, **180 minutes cumulative Runtime verification** inside that
wall, and **null token cap**. Authenticated complete stable scoped call-FAIL only
may yield F_i. Each correction needs a new clean causal scoped descendant, an
original-session receipt and strictly fewer failed identities in the same proof
population. No progress, forged/stale/duplicate feedback, wrong operation/session,
unknown/incomplete/unstable proof, changed source/toolchain, resource exhaustion,
crash ambiguity or revoked family authority STOP. Intermediate FAIL creates no
REVIEW/FAILURE or new REPAIR RUN. Exact final K_i supplies canonical evidence once;
terminal completion does not rerun applicable final verification. There is one
source-bound terminal and the ordinary independent Review/Publisher handoff.

Codex support is conditional on the actual installed first-party live transport.
Antigravity and Antigravity-MiniMax currently have no proven same-live recipient
transport: each operation reports explicit UNSUPPORTED/STOP before coding work.
Default enrollment does not assert universal provider capability. Bounded original
RUN observations report effective policy/source, operation, session, proof and
correction counts and STOP facts, with review/publication unobserved. They assert
no Publisher PASS, measured saving, unrelated TASK admission or downstream uptake.

Human-owned prospective exceptions remain separate from automatic enrollment.
An alternative must authenticate an exact supported fresh selection/consent or a
reviewed canonical future policy amendment within existing authority; flags,
provider output and earlier Issue bodies cannot supply that exception. Existing
v1/v2/v3 decoders and originally admitted policies never gain default-mode rights.

### D.2 Historical allocation and measurement truth

The original **16 L1-L4 slots** remain an immutable planning baseline, not the
prospective completion criterion. The controlling roadmap retains all dated
Human choices and the retired old resume pointers. TASK-386 (v1.2) and
TASK-396 (v1.3 normative defaults) remain outside those slots. TASK-423 does
not claim those slots were completed; it prospectively retires the unimplemented
L3-06, separate G3/G6 and L4 sequence under the 2026-10-25 Human decision.
Future TASK-418 repair/PRIMARY continuation is retired. Its r2 and seven
RUN-418-001..007 FAILURE outcomes remain unfinished immutable history.
TASK-419/420/421/422 lineage is independent and untouched by this closeout.

Published TASK-373's bounded L1 readiness and TASK-385's L2-02 observation are
not full measured program exits. TASK-387/388/389/390 retain their bounded
published meanings. TASK-391 r2 / RUN-391-015 / REVIEW-391-003, exact source
`50dc508e7c2660c40319fb32b43ec5072ca59ce3`, supplies L3-04 only. TASK-397 r1 /
RUN-397-002 / REVIEW-397-001, exact source
`60d47926448c1a537f3e9fcbf077c1aec75082f1`, supplies bounded L3-05 only.
TASK-396's exact normative source is
`030c1acb582f71f0fdd0342d790aa54d0d9bdd7f`. None establishes native Codex live
feedback or full integrated production support. TASK-393/394 remain separate
diagnostic/retrieval side tracks. D.1 enables only mechanisms actually supported
by the current source, preserving the existing single Runtime and proof owner.

VP-01/VP-06 measurements, full VP-07 real production conformance and L4
measurement success remain **unestablished**, not PASS, skipped evidence or
implicit savings. Their future Lean VP package duties are retired; they are
neither prerequisites nor required follow-on TASKs for D.1's supported use.
If Human later requests a performance/conformance claim, use authenticated
matched whole-episode evidence and the existing observation mechanisms under
that separate intent. A fixture, old published foundation or one flow's result
cannot establish another operation's real behavior or benefit.

Unknown cost/benefit remains UNKNOWN. Unknown authority or safety still fails
closed. Withdrawal/rollback under D.4 cannot create a new RUN, automatic repair,
new permission, retrospective PASS or automatic roadmap advancement.

### D.3 Retained historical VP-07 reference; no future L4 package duty

The following AC-01..AC-36 ledger records the original full VP-07 design
reference. Under the 2026-10-25 prospective decision it is **not** a mandatory
future Lean VP conformance package, preactivation test suite or staged gate.
Applicable safety and proof obligations of each admitted TASK remain enforced;
no full ledger PASS or measured production conformance is asserted. The
historical v1.0/v1.1/v1.2 and pre-TASK-423 v1.3 publications retain their exact
original duties in their immutable sources.

| Criterion | Required real conformance |
| --- | --- |
| AC-01 | Every mandatory semantic obligation is validly covered or explicitly BLOCK/UNRESOLVED. |
| AC-02 | Stale, incomplete, conflicting or unbound evidence cannot produce false PASS. |
| AC-03 | Distinct integration, concurrency, fixture and ordering proof is preserved. |
| AC-04 | UNKNOWN validity/incomparability cannot become PASS. |
| AC-05 | Candidate PASS causes zero base collection/execution without approved comparison. |
| AC-06 | Decisive candidate FAIL causes zero base replay without a separate comparison obligation. |
| AC-07 | Equivalent proof is not executed twice without a distinct approved repetition requirement. |
| AC-08 | Complete existing failure observation causes no automatic candidate reproduction. |
| AC-09 | Failed/ineligible narrow attribution causes no automatic broad base fallback. |
| AC-10 | Every base operation has authenticated BR-1..BR-6, exact binding/scope and measured cost. |
| AC-11 | REPAIR preserves and reuses unaffected VALID evidence. |
| AC-12 | REMEDIATION preserves valid root evidence unless relevantly invalidated. |
| AC-13 | Unstable, conflicting or incomplete proof is never reusable. |
| AC-14 | Original failure and cleanup/observation error provenance survive without root-cause substitution. |
| AC-15 | Immutable V1/V2 history and exact read-only decoding survive. |
| AC-16 | Prospective Brain authoring uses semantic proof, with no mandatory executable command lists relocated or disguised. |
| AC-17 | Exactly one production Runtime scheduler acts for admitted work; no V2/new-policy dual execution. |
| AC-18 | Rightful Kernel compatibility/amendment authority is resolved; planning approval is insufficient. |
| AC-19 | Human, Brain, Executor, Runtime, Reviewer and Publisher authority stays separate. |
| AC-20 | Real item counts, wall-time breakdown and equivalence/reuse establish measured benefit, with no synthetic savings. |
| AC-21 | No historical TASK/RUN/RESULT/FAILURE/REPAIR reinterpretation or frozen Kernel mutation without rightful separate authority. |
| AC-22 | One exact selected current-RUN sticky Executor/profile/model/effort and live native lease identity/generation throughout checkpoints for each eligible operation; no inherited worker or implicit failover. |
| AC-23 | Intermediate candidate/checkpoint/failed observations are immutable, monotonic, SHA-bound and nonterminal. |
| AC-24 | Bounded typed factual feedback is untrusted-output-safe and cannot decide HOW/semantic strategy. |
| AC-25 | Eligible candidate C1 FAIL → authorized same-RUN C2 correction → exactly one RESULT only when all mandatory obligations hold. |
| AC-26 | No equivalent same-SHA repetition; final checkpoint PASS is not reexecuted at terminalization. |
| AC-27 | C1→C2 reuse needs authenticated applicability; test/helper/fixture/policy/profile/worker/environment changes invalidate or remain UNKNOWN. |
| AC-28 | Distinct integration, concurrency, ordering and required broad coverage survive narrow green. |
| AC-29 | All base replay passes all six BR gates; candidate failure authorizes neither reproduction nor replay. |
| AC-30 | Budget/no-progress/infra instability, scope/risk/authority conflict, interruption and lease loss stop safely. |
| AC-31 | Terminal FAILURE uses exact canonical AUTHOR_REPAIR and a new admitted REPAIR RUN under existing lawful delegation rules. V1.3 continuation needs that new RUN's actual current Executor/live lease, own finite consent and immutable C.8 limits; no prior terminal or permission is reused. |
| AC-32 | One scheduler and existing lifecycle/telemetry ownership; no second router or dual plan. |
| AC-33 | Crash/replay/duplicate feedback cannot repeat proof or consume another attempt; stale/tampered history fails closed. |
| AC-34 | Real wall time, unique/total items, reuse, tokens, interventions and correction cost are attributable; UNKNOWN stays UNKNOWN. |
| AC-35 | Kernel authority and ordinary TASK/Runtime/Reviewer/Publisher gates are met before production cutover. |
| AC-36 | Legacy/downstream remain isolated without independent opt-in/adoption; no automatic roadmap transition. |

Focused tests for D.1 exercise the ordinary caller's real admission,
source/consent/profile/lease checks, original single process, first-pass success,
live installed Codex C1→F1→C2, finite Runtime completion, unstable/unknown proof,
resource/crash/terminal contention, stale/forged/duplicate recipients and legacy
behavior. Missing native live support is a visible BLOCKER test failure, never
skip/xfail or fixture-only live PASS. Runtime runs the authored focused tests;
no full suite, fixture-only delivery claim or separate staged package follows.
Historical failures retain their original outcomes and are not relabeled to
close this ledger.

### D.4 Prospective default, historical isolation and Human rollback

**Current prospective policy:** D.1.2 is default-on after independent family
admission for every new coding RUN. Absence of KA-01 fields means this new mode,
not legacy. Global-source or capability failure is bounded STOP. An explicit
authenticated future Human policy exception may choose a supported alternative;
closing an old selection/consent Issue cannot disable the global policy or
silently downgrade future RUNs. Rollback of the global policy requires its own
Human-owned reviewed canonical amendment and applies only prospectively.

The following paragraphs retain the historical v1/v2/v3 selection, consent and
withdrawal semantics. Their legacy-default and no-automatic-enrollment rules
apply to those earlier modes and sources; D.1.2 governs new default-mode admission.

In the absence of an explicit supported prospective opt-in, the legacy
admitted policy remains the default. Historical and already admitted work keeps
its original policy, schema, identity, consent, budget and correction semantics.
The v3 interactive consent is new-RUN only; the v2 bounded-primary interface
and v1 controlled preactivation retain their own exact meanings. No prior v2
opt-in can gain interaction privileges. Withdrawing new v3 consent cannot route
an opted-in RUN through v2, a legacy process, REPAIR or REMEDIATION.
Current minimum-sufficient V1 PRIMARY remains its exact authored-command direct
path; REPAIR retains its TASK/origin-command combination and overlap rules,
and REMEDIATION retains exact authorized affected verification. Historical V2
decoding/behavior, historical v1.0/v1.1 PRIMARY-only KA-01 semantics and
v1.2's three-operation limits-without-defaults semantics remain exact.
Prospective v1.3 eligibility for one operation never enrolls either of the
other operations, an existing RUN or a future RUN automatically. Version
selection remains explicit per operation; publication/defaults do not migrate
v1.2 admissions or silently fill their missing limits.
No cross-policy dual execution, shadow proof run or fallback from an opted-in
RUN into V2 is permitted. An unsupported new-policy condition stops under the
admitted policy rather than invoking a second scheduler.

The same source-bound activation contract allows explicit Human
withdrawal of prospective v1.3 activation for an operation or the whole version,
by closing/withdrawing the authenticated selection or consent, restoring the
legacy default for affected future nonopted admissions. Each operation's
eligibility and every later per-RUN consent must remain explicit. Withdrawal
does not rewrite previous evidence or original TASK/correction lineage, or
decode history under new semantics. Already admitted opted-in work must either
finish within its still-valid original authority or stop and terminalize safely; revoked
consent or lost lease cannot be bypassed by switching policy/Executor. Rollback
cannot reopen a terminal RUN, refund consumed budget or retroactively admit
an execution. Retain exact immutable versions and the compatible decoders
needed to explain all historical outcomes.

H4 origin/provenance conformance, authored return-affinity, transport/workflow
and publication ownership remain outside this amendment's extension. No
planning marker, session, feedback route or default repository route establishes
origin-affine proof or a new lifecycle router.

Upstream availability/publication/activation does not change a downstream
dependency pin or repository authority. Downstream migration and adoption need
fresh downstream Human/Brain reconciliation, an exact reviewed dependency pin,
explicit repository bindings/permissions and separately admitted, Runtime-
verified, independently reviewed and Publisher-gated change, followed by that
repository's own prospective consent. No downstream pin, activation, roadmap
advance or production savings is implied by KA-01.
