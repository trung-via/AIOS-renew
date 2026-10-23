# CAA-03: optional four-flow integration and portability

TASK-412 r1 implements the Human's explicit 2026-10-24 selection of option B:
one minimal, transient advisor. Option A, current Brain reads and direct
authoring, remains lawful. Option C, a generic agent/orchestrator, is unselected.
There is no default production activation, CAA-04 evaluation/shadow/rollout,
CAA-05 training, or claim of useful production uplift or provider superiority.

## Callable and trust boundary

`aios_renew.caa_integration.consult_flow` is one read-only helper accepting
exactly `ARCHITECTURE`, `TASK_AUTHORING`, `REPAIR_AUTHORING`, and
`REMEDIATION_AUTHORING`. The caller supplies its existing `BrainWorkContext`,
already selected `FlowResolution`, and canonical `compile_decision_packet`
material. CAA calls that unchanged compiler to validate the supplied resolution
and subject. It does not resolve a new flow, infer one from prose, select a
provider, create a Flow Card, or invoke an audit or lifecycle launcher.

```python
from aios_renew.caa_integration import consult_flow
from aios_renew.caa_reasoner import candidate_fingerprint
from aios_renew.decision_packet import compile_decision_packet

# Already supplied by the caller's existing canonical readers and Flow Resolver:
# repo, context, resolution, material, selected_subject, draft, cut,
# selected_provider, fresh_canonical_inputs.
packet = compile_decision_packet(context, resolution, material)
advice = consult_flow(
    repo, context=context, resolution=resolution, material=material,
    subject=selected_subject, candidate=draft,
    expected_packet_fingerprint=packet.packet_fingerprint,
    expected_candidate_fingerprint=candidate_fingerprint(draft),
    cut=cut, enabled=True, provider=selected_provider,
    fresh_inputs=fresh_canonical_inputs,
)
```

The default `enabled=False`, or explicit `human_opt_out=True`, returns
`NO_MATERIAL_ADVICE` without history reads, a provider invocation or a freshness
callback. Callers can also omit this helper entirely. Original compiler/source
checks still apply when the helper is called; opt-out cannot authenticate a
missing mandatory FAILURE or an unrelated subject. This is not an added gate
on ordinary authoring.

The candidate is the exact six-field CAA-02 transient projection: `goal`,
`invariants`, `acceptance`, `scope` (`inspect`, `modify`), `consumers`, and
`verification`. Its original 32 KiB and field/count bounds apply. Every byte of
that projection, including text withheld from the provider, participates in
`candidate_fingerprint`. It is advice context, not a final TASK, REPAIR or
REMEDIATION artifact. Brain independently constructs and revalidates that final
family through the existing direct return/admission contracts.

## One exact subject per call

The explicit `subject` has the following exact fields. No default subject,
frontier priority, action, or Executor is selected by CAA.

| Caller-resolved flow | Required caller-selected subject | Additional binding derived from existing validated material |
| --- | --- | --- |
| ARCHITECTURE | `{"kind":"ARCHITECTURE"}` | Packet/work-context fingerprints bind current Human input, roadmap priority, canonical source and existing flow governance. |
| TASK_AUTHORING | `{"kind":"TASK_AUTHORING"}` | Packet binds exact selection; candidate fingerprint binds the draft's entire scope, acceptance and verification projection. |
| REPAIR_AUTHORING | `failed_run_id`, `failed_head_sha`, `executor` | Exact TASK id/revision, full FAILURE digest and canonical material digest. Existing compiler validates FAILURE against failed RUN/head/base/Executor/scope. |
| REMEDIATION_AUTHORING | `source_run_id`, `review_id`, `finding_id`, `reviewed_sha`, `action` | Exact TASK id/revision, finding basis/full contract digest, original material digest and required CHANGES_REQUIRED review boundary. The identity/action must match one outstanding finding in the compiler's unique subject or complete frontier. |

For example, a REPAIR subject is
`{"failed_run_id":"RUN-402-004","failed_head_sha":"<exact SHA>","executor":"codex"}`.
A REMEDIATION subject is
`{"source_run_id":"RUN-402-003","review_id":"REVIEW-402-003","finding_id":"F1","reviewed_sha":"<exact SHA>","action":"CODE_FIX"}`.
Swapping just RUN, Executor, review, finding, revision, reviewed head or action
fails before trusted advice. A multi-finding frontier is not permission to
switch from the caller's selected finding to another otherwise valid finding.
Changing selection requires a separate explicit call with its exact bindings.

`fresh_inputs()` returns a fresh `(context, resolution, material)` tuple from
the caller's **existing canonical readers**. It must actually reobserve the
canonical source and subject, not return a cached packet or model claims.
The helper recompiles and compares packet, source, subject and full material
digests before and after consultation, including failed transports and timeout.
It also rechecks original mutable inputs, candidate fingerprint and cut seal.
A packet/fingerprint is not independent Git authentication; no new Git reader,
authenticator, reducer or source authority is introduced here.

The operational repository root must match the existing Work Context, and its
canonical repository/main identity is bound into the request. CAA-01 then
authenticates the sealed cut against that same repository. A consultation cut
must have been captured against the exact packet main; CAA-01's allowance for
later main descendants never silently refreshes a current flow binding.

## Portable envelope, unchanged reasoner

The selected existing `MappingBrainProvider` or `JsonBrainProvider` receives
the same native-JSON semantics:

```text
AIOS_CAA_INTEGRATION_REQUEST, version=1, authority=NONE
  binding:
    flow, packet_fingerprint, work_context_fingerprint,
    source {repository, main_sha}, subject, material_digest,
    candidate_digest, source_cut
  binding_fingerprint: digest of that entire binding
  consultation: unchanged CAA-02 version-2 request
  response_contract: exact envelope fields and finite response ceiling
  request_fingerprint: digest of the entire request before this field
```

The binding's source cut carries CAA-01's sealed origin digest, repository
identity hash, main/as-of SHA, finite TASK/RUN selection and task-only/full mode.
Its digest binds every immutable ref pin; the complete manifest is retained in
the unchanged inner CAA-02 request/advisory rather than duplicated in the outer
binding. The output does not serialize the private transport origin or local root.

The provider returns the existing native wrapper with exact operational
attribution (`provider`, `model`, nullable `session_id`, `invocation_id`). Its
semantic response has exactly:

```text
request_fingerprint, binding_fingerprint, consultation_response
```

Both fingerprints must echo the current envelope. `consultation_response` is
exactly the existing CAA-02 response: `request_fingerprint`, `candidate_digest`,
`cut_digest`, `outcome`, `suggestions`. The envelope validator only checks
transport binding/shape/budgets. CAA-02 independently validates all advice,
source/candidate citation catalogs, opposing witnesses, causal paths and
epistemic grammar. There is no second reasoner or replacement causal validator.
Invalid outer envelopes yield bounded unavailability; invalid inner advice
yields CAA-02's insufficient-evidence result. Nothing is retried, rerouted or
repaired by another provider or the A1 comparator.

`AIOS_CAA_FLOW_ADVISORY`, version 1, carries `authority=NONE`, the exact binding
and fingerprint, `outcome`, finite `reason`, `source_validity`, and the complete
unchanged CAA-02 `advisory` (or null when consultation was skipped). Its zero to
three recommendations retain evidence citations, counterexamples, decisive
differences, LIMITED/INFERRED transfers, experiments, falsifiers, and UNKNOWN
actual cause/efficacy. Source observations remain declarations/outcomes, not
claims of successful implementation. `source_validity` is
`CAA01_REAUTHENTICATED` only after the existing consult returns; skipped advice
uses `NOT_CONSULTED`. No standalone suggestion or availability field grants
mutation, authoring, dispatch, review or publication authority.

Envelope ceilings are 144 KiB request, 18 KiB semantic response and 112 KiB
output, with an 8 KiB binding ceiling, strict JSON types, finite numbers, 24 levels and 32,768 traversal
nodes. Canonical compiler material retains its existing 2 MiB input ceiling;
only its digest is exported. CAA-02 retains its own smaller request, response,
candidate and output limits. Native adapter byte/depth, duplicate-key and exact
attribution checks remain unchanged. Oversize data is rejected, never truncated
into accepted advice. CAA-02's single outstanding transport slot and ten-second
wait bound cover the entire wrapper invocation, with no accumulating abandoned
calls. Arbitrary callable code cannot be cancelled by Python; transports remain
responsible for their I/O deadlines. Existing CAA-01 finite source-read budgets
and the caller's canonical-reader budgets apply separately.

## Optional failure and temporal isolation

| Situation | Advisory behavior | Existing authoring boundary |
| --- | --- | --- |
| Disabled / Human opt-out | NO_MATERIAL_ADVICE; no history/provider/callback use | Existing lawful direct path remains available. |
| Missing optional history | INSUFFICIENT_GROUNDED_EVIDENCE | No substitute audit, history scan or retry. |
| Missing provider or optional fresh-reader callable | UNAVAILABLE | No automatic provider selection or new authoring gate. |
| No material evidence / explicit no advice | Existing finite CAA-02 abstention | Brain may continue its lawful direct work. |
| Provider exception, invalid native/semantic output, attribution mismatch, timeout or busy slot | Finite UNAVAILABLE or INSUFFICIENT_GROUNDED_EVIDENCE | No retries, mutation, audit invocation or authority upgrade. |
| Missing mandatory canonical subject, changed canonical source/selection, substituted candidate or invalid cut | Original source/compiler error or binding rejection; no trusted advice | Original mandatory source guards remain independently fail-closed. Optional advice cannot waive them. |

For an as-of authoring view, supply CAA-01 `task_only=True, as_of_sha=...` on
authenticated main ancestry. Such a view contains no future RESULT, FAILURE,
REPAIR or REVIEW. Actually captured full cuts replay exactly their pins; later
episodes never enrich them. Removed/moved refs, forged/exported cuts, cross-repo
identity and rewritten main remain CAA-01 failures. CAA-02 reauthenticates its
cut even after an attempted transport fails; the wrapper also rechecks the
original flow source. Historical policy is analogy data, never current policy.

Only existing safe CAA-02 features and identity/digest fields reach the provider.
Raw canonical material, Human prose, finding instructions, FAILURE messages,
secrets, sensitive local paths, private logs, provider traces, arbitrary Git
prose and assistant chain-of-thought are not exported. Unrecognized or suspicious
text stays excluded by CAA-02's feature projection. Retrieved text is never
instruction authority; additional action/tool/policy fields fail closed.

## Authority and bounded conformance

Human retains architecture priority and risk acceptance, optional soft AOA
and its explicit opt-out. Brain retains final WHAT/WHY and advice acceptance.
TASK authoring retains its exact scope, acceptance, minimum-sufficient-v1
verification obligations and direct final-family admission. REPAIR advice
cannot choose an action or borrow another FAILURE/Executor. REMEDIATION advice
cannot switch finding, reviewed RUN/SHA or required action, or extend original
scope because another historical case looks similar. CAA does not call any
authoring ingress, audit attempt, Runtime stage, Reviewer, Publisher or launcher.
TASK-411 and all earlier lifecycle lineage remain separate.

`tests/test_caa_integration.py` supplies disposable canonical-source fixtures
and mapping/JSON **simulations** for all four flows. Positive and adversarial
cases cover exact binding, substitution, replay, malformed/overbudget output,
attribution, injection, stale sources, cut invalidation, as-of isolation,
opt-out, optional absence, no advice, failure and timeout. These are contract
fixtures, not live GPT/Claude/Gemini equivalence or measured product superiority.
Runtime alone executes the authored checks and constructs EVIDENCE; independent
Reviewer and Publisher own their later decisions. Implementing this callable
does not select CAA-04/05, publish a result or advance NEXT automatically.
