# CAA-04: finite independent evaluation and Human rollout report

## Prospective policy boundary (TASK-421, 2026-10-25)

CAA-04 is an optional effectiveness study under the controlling
[Personal-First, Direct-Activation policy](CHATGPT_PROJECT_CONTRACT.md#end-to-end-improvement-delivery).
Its preregistration, source/plan authentication, independent labels, thresholds,
holdout/shadow, provider and budget rules below remain exact conditions for
choosing to run that evaluation. They are not prerequisites for normal capability
activation, default four-flow CAA access or Runtime ingress. Future authorized
upgrades include normal-caller enablement in the same TASK and become active after
exact Runtime functional verification/EVIDENCE, independent Reviewer PASS,
Publisher inclusion and any already-authorized operational enablement. No default
trial cohort, shadow window, quality-uplift result or second activation decision is
required. Human may explicitly choose a bounded study or phased rollout exception.
The evaluator implementation and CAA-02/03 advisory/no-advice behavior stay unchanged;
this policy grants no provider billing/training permission and selects no CAA-05.

## Retained study selection

Human selected option B on 2026-10-24 for TASK-414 r2. The accepted input is
CAA-03 TASK-412 r2 / RUN-412-009, independent PRIMARY REVIEW-412-009 PASS on
`7995c3048e2ca2c4606bf7bd352b03a0f8b5ffc9`, with decision commit
`6c6490fe41333842b5030e6f011cf07f11bf82b3`, included on main. Runtime
independently reauthenticates this lineage. Selection authorizes implementation
only. It does not approve a decisive evaluation population, spend, shadow start,
provider, quality threshold or production rollout.

## Caller boundary

`aios_renew.caa_evaluation.AdvisorCase` holds exactly a CAA-03 four-flow packet,
caller-selected subject, transient candidate and fingerprint, sealed CAA-01
`ExperienceCut`, and trusted fresh-packet supplier. It has no rubric, label,
baseline, result or provider instruction field. `EvaluationSession(repo,
plan=None, plan_supplier=None, provider=None, reserve_call=None,
enabled=False)` is disabled by default. The caller must separately invoke
`evaluate(case, label=..., label_supplier=..., baseline=...,
advisor_episode=..., observations_supplier=..., opt_in=True)`.
The only model call is the existing `consult_flow` call, with at most one selected
advisor consultation per subject. That call retains CAA-02 source/citation,
causal, output, time and provider validation and CAA-03 exact flow, subject,
candidate and currentness guards. This layer has no source reader, flow
resolver, provider router, authoring ingress or lifecycle writer.

`evaluate` returns a finite observation or a local `SKIPPED`/`STOPPED` result.
For a shadow subject labelled after consultation,
`complete_shadow_observation(case_id, label=..., label_supplier=...,
baseline=..., advisor_episode=..., observations_supplier=...)` attaches the
independent result to the saved in-process advice. It makes no advisor call.
Pending advice is kept only in the bounded in-memory session and cleared on
grading or `disable()`; reports never export it.
Disabled evaluation, Human opt-out, absent provider, missing plan, failed
reservation, exhaustion and evaluation-only invalid data do not run or block
ordinary direct Brain work. A caller must not catch a genuine existing
canonical packet/source authentication error as permission to author; the
normal Brain guards still decide that work. `disable()` changes only the local
observer switch. A report or rollout packet is data, never an action token.

## Exact preregistration

Before decisive sealed holdout or live shadow, the caller must provide an exact
`AIOS_CAA_EVALUATION_PLAN` v1 record and a trusted `plan_supplier` that
independently reauthenticates the registered Human decision, its source/current
main, and Human withdrawal state on every call. The evaluator compares the
entire supplied plan to that result. An echo callback, plan hash, exported cut,
JSON report, model output or CAA self-score does not authenticate the record.
No plan or an invalid/changed plan stops evaluation before advice. The plan
contains the Human decision ID and HOLDOUT/SHADOW permissions, registration
time, repository/main and allowed sealed cuts; separate exact DEVELOPMENT,
HOLDOUT and SHADOW case cohorts; flow, subject key, packet/work-context,
candidate, as-of and cut bindings; independent Human rubric/grader; fixed
quality and latency thresholds; privacy exclusions; exact provider/model/
effort; cost, call, subject and duration ceilings; stop conditions; and a
disable/rollback statement that carries no production authority.

Development examples may run without a plan for diagnostics only, at most
eight selected cases. They never enter decisive aggregate scores. The plan
rejects repeated subjects across cohorts, source/citation swaps and changed
candidate or packet bindings. The sealed cut and CAA-03 guard reject future
episode enrichment, later RUN/REVIEW evidence, moved main, stale/forged cuts
and wrong repository. Labels are supplied on a separate evaluator channel and
used only for grading after the CAA-03 call; the advisor sees no reference
answer. Decisive labels must match a trusted independent `label_supplier`
before the call. The label's exact source,
flow, subject, candidate and as-of binding must match its case. A label with
CAA/model provenance cannot issue an accepted quality score.

The plan cannot exceed **50 subjects, 7 days or 50 selected calls**, and each
subject may be selected once. The window runs from the registered timestamp,
so recreating a session cannot restart it. A trusted caller-owned
`reserve_call(plan_id, subject_key, provider, max_charge_usd_per_call)` gate is
mandatory for decisive calls and must atomically enforce these ceilings across
sessions/processes and the provider's billable per-call cap. The evaluator
also reserves the worst-case approved charge locally before each consultation.
If the trusted reservation gate is unavailable or refuses a call, no provider
call occurs. These are ceilings, not permission to run or spend. The existing
CAA-03 caps still apply within each selected call. No retry or failover exists.

## Independent grading and report

The deterministic grader uses only separately supplied independent Human
labels. It requires actual labelled material source citations, a labelled
applicable causal transfer, expected counterexample citations, appropriate
abstention and safe confidence. A syntactically valid citation alone cannot
earn material support. A wrong analogy, unsupported confidence, missed
abstention or unsafe recommendation is marked harmful. Missing decisive labels
or unavailable facts remain `UNKNOWN`. No CAA output or model judge can issue
its own accepted grade; a model judge, if later used, would be secondary and
need separate calibration and Human provenance.

`report()` separates DEVELOPMENT diagnostics from HOLDOUT/SHADOW observations
and baseline from advisor. It labels measured, estimated and UNKNOWN cost,
quality, safety, p50/p95 local consultation latency, Human minutes and full
episode outcomes. Baseline and episode measurements require explicit external
provenance and a matching trusted `observations_supplier` for decisive cases;
absent observations remain UNKNOWN. Reserved cost is an estimate,
not an invoice. The report includes sample counts, missingness, finite
sampling limitations and confounders, and always leaves production causal
uplift UNKNOWN. No raw logs, credentials, provider traces, chain of thought,
candidate prose or private source text are reported.

`rollout_packet()` presents threshold observations, independent evaluator
provenance, unknowns, alternatives, stop/disable gates and one of
`PROPOSE_ROLLOUT`, `REEVALUATE`, `DEFER` or `STOP`. Its authority is `NONE`.
The original TASK-414 study selection imposed this rollout restriction:

> Only a separate exact Human authorization, followed by the ordinary lawful
> process, can expose CAA advice in controlled production use.

TASK-421 prospectively supersedes that restriction as a default gate for production
enablement already covered by Human authorization. The report's thresholds and
recommendations cannot block such activation or supply mutation authority. A newly
requested study rollout beyond existing permission still needs exact Human scope,
spend and exposure authorization. Stopping or disabling this evaluator cannot
change RUN, REVIEW, publication or Git state or disable ordinary CAA advice.

The four-flow fixtures simulate mapping and JSON providers and adversarial
source, citation, label, future-data, injection, false analogy, grader,
budget, timeout, provider-unavailable and opt-out cases. Simulation is not
live provider equivalence or evidence of production benefit. Runtime owns
canonical verification and evidence; independent Reviewer and Publisher keep
their separate contracts. TASK-414 r2's authored three-file verification
excludes only the inherited CAA-03 test that asserts its original six-path
TASK-412 source cut and former live NEXT. That historical fixture remains
unchanged. The CAA-04 regression compares retained CAA-03 Human records and
accepted lineage to the published CAA-03 source while asserting CAA-04 is now
sole NEXT. RUN-414-001 through RUN-414-006 retain their failure history; no
earlier candidate, review or publication is reclassified. CAA-05 remains
conditional and unselected.
