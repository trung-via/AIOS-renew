# AIOS Publication End-to-End Flow v1

**Navigation identity:** `PUBLICATION_END_TO_END_FLOW_V1`
**Activation:** current only from the Runtime-verified, independently reviewed and
Publisher-published TASK-327 source on canonical main. A candidate checkout,
planning document, workflow acceptance or Executor claim does not activate recovery.

This is the one publication-specific normative navigation leaf beneath
[SELF_HOST_END_TO_END_FLOW_V1](AIOS-SELF-HOST-END-TO-END-FLOW-v1.md#7-publish).
It owns publication ordering and locators. Constitution, frozen Kernel, exact TASK,
Reviewer, Publisher, verification policy and H4 retain their independent authority.

## 1. Ordinary exact publication

`PASS -> SUBMIT_REVIEW ingress -> canonical decision -> Publisher -> outcome`

Reviewer authors a verdict for one exact RUN candidate. Submit that REVIEW through
the existing [Brain ingress](../.github/workflows/aios-brain-ingress.yml), selecting
the RUN and exact expected candidate SHA. Ingress validates the frozen REVIEW and
canonical RUN/RESULT/EVIDENCE, writes a metadata decision commit, and reserves the
exact main/source/decision/artifact boundary for PASS. The decision commit is not
product source. Identical ingress is idempotent; a conflicting decision is rejected.

The existing [auto-publish workflow](../.github/workflows/aios-auto-publish.yml)
receives the exact decision event or an existing RUN replay. Current canonical main
supplies control code. [Publisher](../src/aios_renew/publication.py) binds canonical
PASS, source, artifacts, current main ancestry, reservation and expected-old-main
CAS. A fast-forward needs no recovery RUN, proof or semantic review. An included
source needs no new work. A successful push is followed by exact main inclusion;
failed operational receipts never establish publication.

The library report exposes exactly these bounded classifications. Its separate
`outcome` records the actual terminal/progress state; `PUBLISHABLE` classification
alone never means a push succeeded.

| Classification | Authorized continuation |
| --- | --- |
| `PUBLISHABLE` | Exact existing PASS source can advance main through Publisher's reservation/CAS; report `PUBLISHED` only after the postcondition. |
| `ALREADY_INCLUDED` | Exact candidate equals or is an ancestor of canonical main; no additional work. `ALREADY_PUBLISHED` is the legacy equality outcome. |
| `CLEAN_RECOVERY_ELIGIBLE` | Source-only proposal or admitted `AWAITING_REVIEW` continuation; no publication or inherited verdict. |
| `CONFLICT_OR_UNKNOWN` | Conflict, ambiguous ancestry, unsupported mode, scope escape, malformed/missing proof, absent activation or an explicit Kernel representability blocker; stop. |
| `STALE_BINDING` | Main, TASK/affinity, immutable lineage, decision set or reservation changed; stop without blind retry or constructing another candidate. |
| `COMPETING_SOURCE` | Another eligible PASS or reservation owner prevents unique selection; stop, never choose a winner by timestamp or RUN number. |

## 2. Clean divergent source continuation

`eligible divergence -> Runtime admission -> one source -> Runtime evidence -> new Reviewer obligation`

The standing lane automatically enters [Runtime's publication continuation](../src/aios_renew/operator.py)
only after Publisher classifies clean divergence. No per-incident implementation
TASK or Brain multi-step Git operation is needed for an eligible source.

Runtime requires the uniquely current lifecycle tip and exact original PASS,
RESULT/artifact SHA, source SHA, TASK revision/bytes and return affinity. It checks
the complete decision snapshot and canonical main under the existing publication
reservation. There must be one merge base, no conflicting or overlapping changed
paths (even a textually clean overlapping merge is disallowed), only supported
regular-file modes, and a delta inside TASK modify scope. The new tree must contain
the exact original reviewed modes/blob identities at every source delta path and
the exact current-main identities at every unrelated path. No semantic merge
resolution, scope widening, cherry-pick, rebase or force update is authorized.

The content address binds original RUN/artifacts/PASS/source, current main,
merge base, TASK/revision, tree and sorted path/mode/blob/deletion identities.
Runtime admits an ordinary **PRIMARY RUN**, with current main as base and the
original Executor label as provenance; it does not invoke an Executor, choose a
model/effort or reuse delegation for a new TASK. Original complete structural
claims can be retained only because the reviewed implementation blobs are exactly
preserved. If existing frozen PRIMARY contracts cannot represent that RESULT
(for example an original empty DELTA claim set without complete primary claims),
the pre-RUN outcome is explicit `HUMAN_KERNEL_AMENDMENT_REQUIRED`, without candidate
creation, fabricated claims/findings or frozen schema changes. Ordinary existing
REMEDIATION/REPAIR publication remains available under its own contracts.

Admission, source creation, proof, review and publication are separate durable
boundaries. One same-origin admission ref reserves the destination RUN. One
content-addressed source ref creates the candidate once, with ordered parents
`[current main, original reviewed source]`, fixed authorship/date and identity-bound
message. Resume reads that ref; it does not construct a second candidate. The source
ref has no RESULT, proof, review or publication authority by itself.

The canonical recovery RUN/RESULT/EVIDENCE remain ordinary frozen artifacts.
`publication-recovery.json` is subordinate transport provenance, not a new Kernel
artifact kind. No original RUN, RESULT, EVIDENCE or REVIEW is rewritten. A pre-RUN
failure uses the existing no-RUN admission diagnostic surface (`RECOVER_PRIMARY`
operation, exact source selector); an admitted failure uses canonical FAILURE.
Neither is a semantic remediation finding. A failed or stale admitted continuation
does not automatically retry, steal a reservation or recover itself again.

## 3. Runtime evidence validity and affected proof

[Verification's bounded publication decision](../src/aios_renew/verification.py)
records original immutable evidence IDs/artifact/PASS provenance and each exact
authored requirement as `VALID/REUSED`, `INVALIDATED/EXECUTED` or `UNKNOWN/EXECUTED`.
It has no parallel scheduler and delegates authorized executions to the existing
Runtime verification strategy. A false, missing, malformed or unknown proof cannot
be labeled PASS.

The conservative read set is the entire tracked tree. Reuse requires a unique
complete original zero-failure observation, identical candidate and base tracked
contents and source delta, command,
TASK policy/envelope, fixture/helper/config/observer identities, profile, environment
and toolchain, plus available original raw content matching its immutable digest.
An unchanged command, pytest node ID, filename, disjoint diff, old passing RUN or
successful Git merge alone is insufficient. A main SHA change with otherwise
affirmatively equivalent conditions does not require repeated proof. Reused
current-candidate EVIDENCE explicitly identifies Runtime derivation and retains
the original executed observation/raw digest in its reuse receipt; it never
describes the old command as newly executed.

Changed planning content is preserved but is not automatically assumed irrelevant
to tests. Without established equivalence, the original authored bounded proof
requirements execute on the exact new candidate. No new baseline, full suite or VP
policy cutover is selected merely because main moved. Distinct integration proof
for exact source/current-main preservation remains mandatory even if every
authored requirement is reused. A prior nonzero/base-attributed observation cannot
be transplanted to a different integration base. A canonical RESULT resume retries
transport/wake and never repeats evidence execution.

## 4. Independent recovered-source review and final publication

`new RESULT -> exact semantic review scope -> fresh PRIMARY REVIEW -> ingress -> Publisher -> inclusion`

[Unified State and semantic review scope](../src/aios_renew/unified_state.py)
select the sole recovered tip using validated admission/source provenance,
never numeric RUN order. The new SHA starts a fresh PRIMARY review obligation;
the old PASS is provenance only. Its scope names current main, original reviewed
source/PASS/artifact and the exact recovery identity, with
`prior_pass_is_verdict=false`. Reviewer independently judges the preserved source,
integration delta, evidence validity and every TASK acceptance obligation.

The normal semantic authority boundary pauses until Reviewer supplies a fresh
canonical PASS for the exact recovered SHA. Ingress rejects old-SHA substitution,
DELTA impersonation, stale main or malformed provenance. Successful new ingress
triggers the same Publisher workflow. An origin replay deterministically selects
only its admitted destination; a new decision event enters ordinary Publisher.
Publisher rechecks original provenance, candidate source ref, fresh exact
RUN/RESULT/EVIDENCE/PASS, current main and reservation before the final CAS. Main
movement after recovery cannot trigger another automatic construction. Already
included replay remains a no-op with the original lineage fully validated.

## 5. Durable locators, attention and negative branches

| Fact | Canonical locator / owner |
| --- | --- |
| Original/new candidate | `refs/heads/aios/review/<RUN>`; Runtime terminal source, never a decision commit |
| Frozen RUN/RESULT/EVIDENCE | `refs/heads/aios/artifacts/<RUN>` and `.ai/transport/run.json`, `result.json` |
| Semantic verdict | `refs/heads/aios/review-decision/<RUN>` and `.ai/reviews/REVIEW-<suffix>.yaml`; Reviewer via ingress |
| Recovery admission | `refs/heads/aios/publication-recovery/<original RUN>`; ordinary admitted `run.json` and subordinate `publication-recovery.json` |
| Source-only candidate | `refs/heads/aios/publication-source/<content address>`; no semantic eligibility |
| Recovery provenance/validity decision | New artifacts' `.ai/transport/publication-recovery.json`; original immutable selectors, admitted RUN and bounded evidence dispositions |
| Mutation ordering | `refs/heads/aios/publication-reservation/main`; exact owner/expected main, no expiry takeover |
| Admitted failure | Existing `aios/failure`, `aios/failure-artifacts` refs and canonical FAILURE |
| Pre-RUN failure | Existing `AIOS_ADMISSION_FAILURE` diagnostic; explicitly no RUN/Executor/verification truth |
| Published source | Canonical `refs/heads/main` inclusion plus validated Publisher report |

The standard Runtime terminal attention lane owns the recovered RESULT review
request, including durable idempotent wake retry. `AWAITING_REVIEW` never creates
a publication-success event. [Publication attention](../src/aios_renew/brain_attention.py)
validates canonical continuation before projecting it and proves main inclusion
before success. Dispatch/queued/runner-started/carrier-accepted/recovery-proposal
or review-wake receipt alone is transport truth. Lost wake cannot authorize a new
candidate, repeated proof or inherited PASS. Concurrent main movement, stale or
competing PASS, missing EVIDENCE, duplicate delivery, malformed integration,
changed profile/test/fixture/helper and conflict all remain fail-closed at main.

## 6. Section-scoped consolidation and activation lineage

| Claim/section | Classification |
| --- | --- |
| Project Contract §12 publication traversal; self-host §7; README publication navigation | `REPLACE_WITH_POINTER` to this specialized leaf in the same TASK-327 candidate |
| SELF_HOST_END_TO_END_FLOW_V1 generic ordering | `RETAIN_NORMATIVE`: the single generic operational navigation entrypoint |
| Constitution, frozen Kernel, TASK, independent Reviewer/Publisher checks, current minimum-sufficient-v2 policy, H4 conformance and publication-turn governance | `RETAIN_NORMATIVE` within their independent scopes; no safety gate is retired |
| TASK-068/070/072, TASK-287/288, TASK-293/295 integrations; TASK-315 one-time Human exception; dated publication examples | `HISTORICAL_ONLY` as incident procedures and immutable provenance, not standing publication bypasses |
| Current Publisher, Runtime recovery/verification, correction integration, ingress, reducer, attention and workflows | `IMPLEMENTATION_LEAF`: live bounded code and trust boundaries |

No historical evidence/artifact or specialized mixed-authority document is deleted.
TASK-308/H4/H5 publication-turn or wake/conformance policy is not activated here.
Planning/roadmap advancement is independent of publication.

TASK-326 r1 / RUN-326-001 retains original RESULT artifact
`5b5af1e276c9a1bb7aa0031952e1390fe607fc9c`, reviewed source
`9b2ec4afbedc4ef342b3ea198c59abbb48938f74` and REVIEW-326-001 decision
`b526cfdded14d74985af42fc4a14b7b54045b260`. Its planning-only main delta,
including `1ead2b69f8c3ab68bee76e37414f013c2449de78` and
`dfa72166f96c980cdf1aff3cdf5bf254621c9773`, must be preserved. The 148 historical
focused passes remain original provenance, not an automatic verdict/proof for a
fresh candidate. Any actual TASK-326 recovery and publication is a separate later
canonical operation after TASK-327 publication; it is not TASK-327 completion or
VP-01 measurement, VP-03 implementation, roadmap closure or downstream pin change.
