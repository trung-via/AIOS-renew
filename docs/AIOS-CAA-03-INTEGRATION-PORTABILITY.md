# CAA-03: four-flow advisory integration and portability

TASK-419 r2 records Human's narrow 2026-10-25 replacement of TASK-417's future
access/default rules. CAA now starts by default in the supported first-party
Python `brain_provider.attempt()` for ARCHITECTURE, TASK_AUTHORING,
REPAIR_AUTHORING and REMEDIATION_AUTHORING, after canonical policy/flow and
subject/draft composition and before Brain's semantic decision calls. This does
not make CAA an audit, authoring admission requirement, or lifecycle authority.
TASK-417, TASK-419 r1 and all original engineering lineage remain immutable.

Human on 2026-10-24 selected option B: one minimal optional advisor for the
existing Brain-owned ARCHITECTURE, TASK_AUTHORING, REPAIR_AUTHORING and
REMEDIATION_AUTHORING flows. TASK-412 r2 implements this bounded contract.
The selection records no completion, review, publication, default activation,
live provider equivalence or production benefit. Option C, a generic agent or
orchestrator, remains outside scope. CAA-04 evaluation/shadow/rollout and
conditional CAA-05 training remain unselected.

The accepted predecessor is TASK-410 r1 / RUN-410-002: RESULT artifacts
`149ca05de386bd929d2e432e9a24561c55cba9f4`, independent DELTA REVIEW-410-002 PASS
at `bf7b3baf65fa45e056caac5e634543d387f5087e`, on exact reviewed source
`8327f421df0c3e477e9a405b69bf7e5910cddca5`, included on main. CAA-01's accepted
TASK-409 / RUN-409-002 lineage remains in the [CAA roadmap](AIOS-CAA-V1-ROADMAP.md).
The authoring handoff observes main `749cd197e55eadb36e8876db104c333a021f4915`;
Runtime reauthenticates source currentness. TASK-411 r2 Publisher work remains
separate, with no completion claim here. TASK-412 r2's additional permission is
only the source-bound historical CAA-02 fixture correction; prior RUN-412-001
through RUN-412-007 FAILURE history is immutable and the native Codex exit-1
technical root cause remains UNKNOWN.

## One callable boundary

```python
consult_flow(
    repo, *, flow, packet, subject, candidate, expected_candidate_fingerprint,
    fresh_packet_supplier, cut=None, human_opt_out=False,
    provider=None,
)
```

`aios_renew.caa_integration.consult_flow` returns a strict JSON-compatible
`AIOS_CAA_FLOW_ADVISORY` v1 dictionary. There are exactly four accepted flow
tokens, supplied by the caller after the existing Flow Resolver and Flow Card
have established its reasoning contract. The wrapper never invokes a resolver,
guesses a flow, creates a Flow Card, selects a provider or calls an authoring
ingress. Ordinary first-party Brain attempts call this boundary automatically.
`brain_provider.consult_advisory` also exposes it directly, returning the existing
consultation container without manufacturing a historical pilot observation.
Legacy `enabled`, `profile_supplier` and `reserve_call` arguments are accepted
for source compatibility only; they no longer gate normal consultation.

The caller can supply the exact existing six-field `advisory_candidate` to
`attempt()`. Otherwise the boundary composes that draft projection from the
already authenticated TASK contract/source context or Architecture objective and
Human request. It adds no advice, causal declaration or missing requirement.
An absent or over-budget optional draft cannot become an authoring gate.
For an old host packet lacking a CAA correction projection, the attempt reports
unavailable advice; supplied incorrect/stale subjects still fail closed.

The existing selected Brain transport handles the unchanged CAA-03 exchange
before the unchanged Brain semantic calls. No request field, response grammar,
model prompt, audit selection or stage lineage is added or rewritten.
`AttemptResult.advisory` exposes the bound advisory separately from the family
candidate for Brain's final accept/adapt/reject disposition. A transport may use
its established conversation resources; this implementation makes no claim that
a stateless remote model or unrelated host automatically retains that exchange.
Empty/unavailable advice permits the existing authoring path to continue.

Only this Python invocation path is integrated. Repository policy text cannot
make an unrelated ChatGPT, remote chat or external host session execute Python;
automatic operation at those host boundaries remains an evidence gap.

`packet` is the existing compiled `DecisionPacket` for the exact caller-owned
subject. Its integrity is checked by the existing Brain packet validator.
`fresh_packet_supplier` is trusted, bounded, read-only application code using
the caller's existing canonical source guards and packet composition for
`repo`, as in the existing Brain provider freshness contract. It must include
current authenticated repository/main, Human request/priority/choice, original
TASK obligations and the exact canonical correction lineage where applicable.
For corrections, reuse the existing canonical correction snapshot/compiler
and their FAILURE, CHANGES_REQUIRED REVIEW and outstanding-finding guards.
An echo of untrusted packet text, a model callback or a supplied digest cannot
replace those guards. No portable object authenticates itself.

The supplier runs before consultation and again before return, including
opt-out, unavailable transport, timeout and a cut-reader exception. It must
produce the same complete packet, without resolving a new flow or refreshing
the operation onto a different subject. A changed main, source, Human choice,
policy/currentness basis, finding, original obligations or packet fails closed.
These are the caller's original source guards, not an audit or an extra gate
on ordinary authoring. The helper implements no Git authenticator or lifecycle
reducer of its own. Trusted supplier cost/deadlines remain those of the existing
bounded source composition; arbitrary executable callbacks are not sandboxed.

`candidate` is the unchanged six-field CAA-02 transient advisory projection:
goal, invariants, acceptance, scope (`inspect`/`modify`), consumers and
verification. `caa_reasoner.candidate_fingerprint(candidate)` binds every field,
including excluded prose. It grants no source or authoring authority. Changed
drafts require a new consultation. Brain independently constructs and
revalidates the final TASK/REPAIR/REMEDIATION family candidate using its existing
validators and direct ingress; no advisory field is such a candidate or token.

## Exact subject and source bindings

The caller supplies `subject` with exactly the corresponding fields below.
Values are compared with the canonical packet projection before any provider
call, and checked again before return. SHA fields are full 40-character Git
SHAs; fingerprints are full 64-character SHA-256 digests.

| Flow | Exact subject fields | Source of binding and preserved authority |
| --- | --- | --- |
| ARCHITECTURE | `kind`, `decision_fingerprint` | Kind is ARCHITECTURE; fingerprint is this packet's fingerprint, including the exact Human request. Human owns priority and risk acceptance; optional soft AOA policy and explicit opt-out remain intact. Brain owns the final WHAT/WHY. |
| TASK_AUTHORING | `kind`, `task`, `decision_fingerprint` | Task is the canonical selected `{id, revision}`. It can be null for a new proposal with no selected canonical TASK; its exact packet plus candidate still binds one draft. It cannot be relabeled as an existing revision. The entire original context and exact draft scope, acceptance and verification projection remain bound. Direct final-family admission is unchanged. |
| REPAIR_AUTHORING | `kind`, `task`, `artifacts_sha`, `executor`, `base_sha`, `failed_run_id`, `failed_head_sha`, `requested_action` | Exact `EXACT_CORRECTION_SUBJECT_LINEAGE_V1` / AUTHOR_REPAIR packet and failed subject. Executor is the failed RUN's Executor. Action is a caller-owned optional proposal, null or one existing action token; it is only an immutable request binding, never an authorization. CAA cannot choose CODE_FIX, NO_CHANGE, CONTINUE_IMPLEMENTATION or FINALIZE_CANDIDATE, select an Executor or borrow another FAILURE. |
| REMEDIATION_AUTHORING | `kind`, `task`, `artifacts_sha`, `executor`, `base_sha`, `source_run_id`, `review_id`, `reviewed_sha`, `review_decision_sha`, `finding_id`, `required_action` | Exact AUTHOR_REMEDIATION correction lineage, originating reviewed RUN and selected outstanding finding. The finding's action must match the existing compiler's unique finding or exact member of its correction frontier. The canonical source guard proves CHANGES_REQUIRED and outstanding membership. A similar historical case cannot switch the review, SHA, finding/action, original scope or verification. |

For example, ARCHITECTURE binds
`{"kind":"ARCHITECTURE","decision_fingerprint":"<this packet fingerprint>"}`.
A REPAIR subject naming another failed RUN or Executor, or a REMEDIATION subject
naming another finding, reviewed SHA or required action is rejected before a
provider call. Changing TASK revision is also rejected. An extra action field
or a boolean substituted for a revision cannot pass the exact JSON comparison.
The same substitutions in a provider reply invalidate that reply.

The portable `binding` contains the exact flow, a digest of the canonical
repository identity and current main SHA, complete packet/work-context
fingerprints, exact subject, candidate digest and the original cut's digest,
repository ID, main SHA, as-of SHA and mode. Repository URLs/local roots and raw
packet/Human prose are withheld. `cut` must be an in-process sealed CAA-01
`ExperienceCut`; its complete pins remain inside the existing CAA-02 request.
An exported dictionary or another repository's cut is never imported as trust.

Current authoring source and historical evidence origin stay separate. The
source guard pins current repository/main and policy, while the CAA-01 cut pins
exact historical evidence. A TASK-only/as-of cut cannot acquire later RESULT,
FAILURE or REVIEW, even when a later terminal names the same head. Full cuts
can use only their captured refs; later refs never enrich them. CAA-02 rechecks
that same cut after transport invocation, including transport failure. Moved
pins, forged cuts, source substitution and rewritten main fail through the
existing reader. The final caller guard also rejects a main descendant or
changed policy that invalidates the current authoring packet; it never silently
borrows the descendant's authority or later episode evidence. Historical policy
is data, with `normative_policy="UNKNOWN"`, never current authorization.

## Portable transport and evidence validation

The one explicitly supplied existing `MappingBrainProvider` or
`JsonBrainProvider` receives semantically identical native JSON values:

```text
AIOS_CAA_FLOW_REQUEST v1:
  format, version, authority=NONE, binding, trusted_protocol,
  consultation=<existing AIOS_CAA_CONSULTATION_REQUEST v2>,
  response_contract, request_fingerprint

AIOS_CAA_FLOW_RESPONSE v1:
  format, version, request_fingerprint, binding,
  consultation=<existing CAA-02 response>
```

The response must echo the entire binding and exact outer request fingerprint.
The inner response independently binds its CAA-02 request fingerprint, candidate
digest and cut digest. Closed fields, strict JSON, finite bounds, exact version
types and provider/model attribution are checked locally on both transports.
Duplicate JSON keys, nonfinite values, extra tool/action fields, a swapped
subject/source/candidate/cut or malformed/oversized native response are contained.
There is one selected transport call, no retry, rerouting, provider failover,
automatic comparator fallback or tool/action channel.

The bridge unwraps only the inner response into the existing `caa_reasoner.consult`
B1 path. It does not render its own advice, create a second reasoner, supply new
citations or replace CAA-02's validator. Every 0-3 recommendation comes from
CAA-02's independent source/candidate citation checks, dependency alignment,
counterexample and falsifier checks. OBSERVED means declarations/outcomes only;
transfer stays INFERRED and LIMITED, with technical cause, actual transfer,
efficacy and measured effect UNKNOWN. The wrapped `consultation` retains those
citations, decisive differences, experiments, causal limits and falsifiers.
Arbitrary retrieved text is never instruction: the existing closed-vocabulary
projection excludes suspicious fields whole. Secrets, private paths/logs,
provider traces, raw Human input and assistant chain-of-thought are not advisory
context. Supplied digests are bindings, not proof that model assertions are facts.

## Finite outcomes and opt-out

| Condition | Result | Effect on existing authoring |
| --- | --- | --- |
| `human_opt_out=True` | NO_MATERIAL_ADVICE; opt-out reason, no cut read or provider call | Direct authoring and optional audit policy remain unchanged. |
| `cut=None` | Search full available authenticated history; no grounded evidence yields INSUFFICIENT_GROUNDED_EVIDENCE | No replacement audit or history gate. |
| Optional history objects unavailable or existing reader budget exhausted | UNAVAILABLE, no advice | Mandatory current subject guard still runs independently; no authoring gate or history retry. |
| Valid no-advice response | NO_MATERIAL_ADVICE, empty recommendations | Brain can continue directly. |
| Absent/busy provider, transport error or bounded timeout | UNAVAILABLE | No retry, mutation or mandatory consultation. |
| Malformed/unbound/overbudget outer response | UNAVAILABLE | Invalid advice is contained without granting authority. |
| Invalid inner causal/citation response | INSUFFICIENT_GROUNDED_EVIDENCE | CAA-02's validator rejects unsupported advice. |
| Missing mandatory canonical subject, source guard failure, changed currentness, or supplied invalid cut | Existing source exception or IntegrationContextError | Genuine original source/authority errors remain blocking independently of optional advice availability. |

The result carries `authority="NONE"`, `authoring_policy="UNCHANGED"`, explicit
source validity checked by the caller guard, and false mutation/RUN/Executor/
verification flags. It is advice only, never a verdict on lawful final admission.

| Surface | Fixed ceiling |
| --- | --- |
| Candidate | Existing CAA-02 32 KiB; 4096 characters/string; 32 entries/list |
| Subject / portable binding | 4 KiB / 8 KiB |
| Outer request / response | 128 KiB / 24 KiB; strict native JSON |
| Inner response / recommendations | Existing CAA-02 16 KiB / 0-3 |
| Native adapter response | Existing 768 KiB, independently checked |
| Provider wait | Existing CAA-02 10 seconds; at most one outstanding selected call, including a timed-out call |
| Source reads | CAA-01 validators and per-read bounds; full history searched in batches; CAA-02 selected-cut read plus at most one reauthentication; no fetch |
| Wrapped output | 112 KiB, depth 20; output excess becomes finite UNAVAILABLE without advice |

Mapping and JSON fixtures are deterministic simulations, not live GPT, Claude or
Gemini product proof. They cover all four flows, correct and colliding subjects,
cross-flow/candidate/cut/citation substitutions, request/response budgets,
attribution, malformed JSON, injection, currentness changes during failed calls,
timeout containment, no advice, missing history/provider and opt-out. Direct
candidate validation and original policy/source surfaces remain unchanged.
The legacy CAA-02 planning/delta fixture reads its actual reviewed Git snapshot;
a separate CAA-03 fixture checks the new current selection and six-file delta.
No test is skipped, weakened, marked xfail or given fabricated historical state.
Runtime alone runs TASK-412's canonical minimum-sufficient-v1 verification and
constructs EVIDENCE. Independent Reviewer and Publisher retain their own roles.

## Default history reach (TASK-419 r2)

Without an explicit historical/evaluation cut, the ordinary consult pages all
available authenticated canonical main TASK/revision history and each TASK's
RUN, terminal, correction, independent review and publication sources. Deleted
TASK roots retain their original authenticated main-ancestry source. Each RUN
uses its own immutable revision; historical TASK-only views gain no later facts.
No TASK number, first-authoring date or ten-TASK cohort controls this search.
No eight-TASK/episode ceiling limits the corpus. Per-document/read resource and
privacy bounds remain effective; missing/unavailable objects do not cause a
source-inventing fallback or a successful partial-search assertion.

CAA-02's existing lexical/structured evidence ordering is reused to choose a
concise request only after every episode has been searched. The advisory reports
searched, selected and omitted counts. The entire searched snapshot is checked
again after consultation, including unselected pins. There is no independent
mutable history database. Explicit sealed past/evaluation cuts keep their exact
original semantics and are never silently enlarged.

CAA-02 source, A1/B1 algorithms, prompts, 0-3 suggestion grammar, evidence and
causal validators, uncertainty, no-material-advice and abstention behavior remain
unchanged. Its single optional B1 provider call uses the existing lawful Brain
transport; there is no separate TASK-417 profile/charge reservation prerequisite,
new provider selector, forced model call, obligatory hypothesis or promise of
advice. Independent CAA-04 source/plan/privacy/provider/spending/holdout gates are
unchanged. TASK-408 Human-on-demand TCA/ROA/MOA and soft AOA policy are unchanged.
