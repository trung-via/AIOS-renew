# CAA v1: finite advisory roadmap

The newer explicit Human decision of 2026-10-23 selects **CAA-02 / TASK-410 r1**
as the sole prospective NEXT in `.ai/roadmap-state.yaml`: implement CAA-02 deep
research now as one finite package. It supersedes only the earlier CAA-01
selection bookmark. That earlier approval is preserved under `previous_selection`.
The four sequential packages below remain finite, separately authorized work;
CAA-03 and CAA-04 are unselected, with no TASK IDs assigned. CAA-05 remains a
conditional unselected option. A RUN, RESULT, REVIEW or publication cannot
advance this plan. Only an explicit Human/Brain decision can select a package.

The existing CAA-01 canonical reader reauthenticated TASK-409 r1 / RUN-409-002:
RESULT artifacts `33b5be48bc465558155caf84a9e72f43be51914d`, independent PRIMARY
REVIEW-409-001 PASS at decision `3b22fbccd05120959aa5f1d646203bcf131de40f`, on exact
reviewed source `4c5a961e17315aa6bdbea810e0fed803ef460e05`. That reviewed source is
included on observed main `cb16cdc191c123e62730ea7be4d2d25c792b16e9`. These are
engineering observations, not an automatic planning transition or a verdict on
TASK-410. TASK-409 and every existing lifecycle record remain unchanged.

The selected implementation contract is
[CAA-02 intelligent reasoner](AIOS-CAA-02-INTELLIGENT-REASONER.md). It supplies
transient consultation only; CAA-03 hooks and CAA-04 shadow/rollout are not part
of TASK-410.

The objective is a portable, evidence-grounded intelligent advisor for
Architecture, TASK, REPAIR and REMEDIATION. The foundation alone supplies data,
not intelligence, trained weights, a semantic verdict or a live authoring
provider. No production benefit or CAA recommendation quality is established by
CAA-01. Runtime, independent Reviewer and Publisher retain their own authority.

## Four sequential packages

| Package | Objective and finite scope | Non-goals | Dependency | Exit gate | Stop gate |
| --- | --- | --- | --- | --- | --- |
| CAA-01 — canonical experience | One maintained, bounded, read-only Engineering Episode reconstruction interface; exact TASK revision, RUN, terminal, evidence, correction, review and inclusion provenance; historical source cuts; positive/adversarial examples. | Retrieval intelligence, model calls, workflow hooks, activation and training. | Authenticated canonical readers and published TASK-408 policy source. | Source identity, temporal isolation, privacy, finite resource use and existing caller regressions demonstrated by the exact focused TASK-409 verification, followed separately by independent review and lawful publication. | Missing/forged/stale source or an unbounded/private projection fails contained; absent facts stay UNKNOWN. No continuation without Human/Brain selection. |
| CAA-02 — evidence retrieval and advice | A bounded retrieval/advice capability that reasons from selected episodes, cites exact evidence, exposes uncertainty and handles conflicting/insufficient evidence. | Checklist/prompt-only claims of intelligence, training, lifecycle verdicts, integration or defaults. | Accepted CAA-01 foundation and a separately authored contract with measurable advice criteria. | Independent evaluation of evidence grounding, useful reasoning, abstention, counterexamples and leakage resistance against a fixed baseline. | Stop if evidence quality or measured advice quality is inadequate, costs exceed the agreed budget, or advice invents facts; return the gap to Human/Brain. |
| CAA-03 — four-flow advisory integration and portability | Finite optional advisory contracts for Architecture, TASK, REPAIR and REMEDIATION; portable inputs/outputs for Claude, Gemini and GPT; equivalent evidence/citation/abstention behavior across adapters. | New Planner, Reviewer, Publisher, generic router, authority, database, dispatch or automatic audit gate. | Accepted CAA-02 plus explicit Human-approved flow/adaptor scope. | Independent contract and portability checks for all four flows, direct-authoring compatibility, failure containment and a working opt-out. | Stop on authority drift, provider dependence, changed direct-authoring semantics or unavailable canonical evidence. No default activation. |
| CAA-04 — independent evaluation, shadow and controlled rollout | A finite independent holdout evaluation and bounded shadow window; compare quality, safety, cost and latency; propose a controlled rollout and rollback decision to Human. | Self-scoring as acceptance, always-on agent, autonomous rollout, training or production uplift inferred from shadow. | Accepted CAA-03; fixed metrics, holdout, shadow duration/budget and rollback gates approved before measurement. | Independent results satisfy predeclared thresholds; only a separate exact Human authorization may enable a controlled rollout, with monitored exit/rollback criteria. | Stop or roll back on failed thresholds, leakage, authority drift, uncertain attribution, budget exhaustion or Human withdrawal. A negative result ends this plan unless Human replans. |

## Conditional fifth option

**CAA-05 — learned reranker or fine-tune** is outside the four-package sequence
and is not selected. Its objective would be to address a measured residual error
or cost problem using a finite, provenance-bound dataset and experiment. It
depends on CAA-04 measurements showing that simpler retrieval/advice is
insufficient, lawful data suitability, and separate Human approval and TASK
authoring. Training, a new truth store, automatic deployment and unmeasured
benefit are non-goals of the current work. Exit would require independent
holdout gains over the accepted baseline within fixed cost/privacy budgets and
a separately authorized rollout. Stop if those gains are absent, data lineage is
uncertain, leakage appears, or the experiment budget is exhausted.

## Source and historical preservation

At execution, the existing canonical audit source reader authenticated
`eaa1363681bade65aaed33d9af6211728fb55992` as the reviewed/published TASK-408
source with effective `HUMAN_ON_DEMAND` policy. CAA does not change that policy:
ordinary AUTHOR_TASK / AUTHOR_REPAIR / AUTHOR_REMEDIATION stay direct;
TCA/ROA/MOA remain Human-on-demand, soft AOA retains explicit opt-out, and exact
Human V1 support keeps its existing constraints. This historical observation
did not itself assert any TASK-409 REVIEW or publication outcome. The separate
TASK-409 observation above was reauthenticated for the new Human selection.

The TASK-408 priority entry is paused prospectively, not marked executed or
completed by this planning update. Its approval records, TASK-407 r1, the
suspended prospective ROA R4 default plan and all prior RUN/FAILURE/REVIEW
lineage remain history. PTE, Lean VP, KA-01 and downstream bookmarks do not
advance. CAA adds no admission step, Flow Card, audit invocation or provider
selection authority. CAA-02 can use one explicitly caller-supplied provider
callable; it is not connected to ordinary authoring flows.

## CAA-01 read contract

`aios_renew.caa_experience.capture_experience_cut` authenticates a finite selection
against canonical transport refs and returns an opaque in-process source cut.
`reconstruct_episodes` reauthenticates that cut and returns ordinary JSON-compatible
data. Cut metadata and facts are portable; an exported dictionary is not an
authenticated cut and cannot be reimported as authority. Capture a new cut for a
new observation. This is an experience reader, never a lifecycle reducer.

For a historical authoring snapshot, use `task_only=True` and an explicit
`as_of_sha` on authenticated main ancestry. Such cuts read TASKs only and cannot
acquire any RUN, FAILURE, REPAIR or REVIEW, including a later failure whose head
equals the authoring commit. Full cuts capture only the refs available during
that current observation. Replays use exactly those pins and never enrich them
with later refs. Later main descendants are allowed; removed or moved pinned
refs, source substitution, a different repository, or rewritten main fail
contained. The cut is not a wall-clock reconstruction of historical remote ref
availability; use an actually captured cut for a historical full episode view.

The reader reuses canonical Git identity, artifact validators, review transport
and public failure projection. It reads only allowlisted canonical documents
already present in the local Git object store. Missing objects produce a bounded
reader error: no implicit fetch, local runtime log fallback or alternate store.
Free-form TASK/claim/summary/correction text, raw logs, private paths, credentials
and provider traces are not exported. Retrieved text is data, never instructions.
Facts separate OBSERVED, INFERRED and UNKNOWN; observed exit codes, completion
gate causes or test-summary shapes are not inferred technical root causes.

Fixed ceilings are 8 TASKs, 8 episodes, 128 scoped refs, 256 KiB per document,
4 MiB total source/command output, 24 input nesting levels, 32,768 input nodes,
64 KiB output at depth 16, 512 Git commands and 60 seconds per call (at most
10 seconds per command). Inputs exceeding a ceiling fail without truncating a
success or retrying. Scope ranges contain at most 8 numeric TASKs; broad/system
queries are unavailable. Equivalent reads of an unchanged cut are deterministic.

CAA-02 adds opt-in `include_semantics=True` to this same authenticated reader.
Its default output and 64 KiB bound remain unchanged. The opt-in view permits
128 KiB and adds only closed-vocabulary mentions from eligible TASK fields,
bound to exact field pointers, immutable RUN revision and original cut. Source
prose stays withheld; a mention is not an invariant proved or a cause observed.
See the CAA-02 contract for the finite advisory and comparative fixture grammar.

Runtime alone retains the unchanged authored minimum-sufficient-v1 command:

```text
python -m pytest -q tests/test_caa_experience.py tests/test_brain_sync.py tests/test_brain_context.py tests/test_brain_attention.py
```

The command above remains the historical TASK-409 verification contract.
Runtime alone executes TASK-410's authored minimum-sufficient-v1 verification;
its exact commands remain in immutable TASK-410. Executor implementation-local
syntax/read sanity is not canonical verification or EVIDENCE. This roadmap records no CAA training, evaluation result, default
activation, later package completion or production quality improvement.
