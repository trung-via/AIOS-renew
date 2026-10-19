# Lean VP L1-03 integrated readiness and falsification boundary

TASK-383 r2 / RUN-383-003 supplies a bounded, explicitly invoked observation
of the selected legacy TASK-381/TASK-382 mapping lineage and its unsupported
bridge to the prospective semantic TASK format. This document describes the
implementation candidate. It asserts no TASK-383 Runtime PASS, independent
Reviewer PASS, Publisher publication, completed L1 engineering or roadmap
advancement. **KA-01 remains NOT_ACTIVATED.**

## Invocation and ownership

```python
from aios_renew.review_transport import read_l1_integrated_readiness

report = read_l1_integrated_readiness(
    repo, intake_json_bytes,
    expected_remote_url=independently_selected_canonical_url,
    expected_main_sha=exact_current_main_sha,
)
```

The intake is the unchanged `task-semantic-proof-intake-v1` declaration:
`schema`, complete `task` binding, `candidate_sha`, `sources` containing each
role's `source_sha`/`blob_sha`, and the actual contract and mapping documents.
It is untrusted input, not a source of authority. The API fixes the three
immutable subject locators below; no caller can choose a review, selection
ref, source locator, resolver, PASS flag, future fixture or authority provider.
The expected main is a fresh exact observation, not the historical authoring
main or an ancestor accepted as current.

The implementation composes the unchanged TASK-377 source binder and
TASK-378/380 authority reader, including TASK-376/VP-02 structural validation.
It does not implement another coverage, selection, review or publication
algorithm. The unchanged TASK-381 decoder is invoked on the authenticated
legacy target bytes only to establish their incompatibility with the future
format. Human, Runtime, Reviewer and Publisher retain their separate owners.
All returned records and their nested conditions/associations are immutable.

Only existing bounded Git reads, immutable-object fetches and independent
GitHub reauthentication are permitted. Fetch uses `--no-tags`,
`--no-write-fetch-head`, `--refmap=`, `--no-auto-maintenance` and
`--no-recurse-submodules`; raw object hashing and bounded raw ancestry retain
the existing transport policy. No ref, main, index or worktree is written.
No observation or report executes tests or semantic proof commands, issues a
selection, creates a lifecycle object, admits a TASK, publishes code, chooses
an Executor or activates policy. There is no automatic invocation from any
operative parser, ingress, Runtime, Reviewer or Publisher path.

## Exact legacy subjects

The following pins identify the bounded observation. Constants, copied labels
and this document cannot authenticate them. Every successful invocation must
independently establish source/currentness and authority through the existing
readers, including exact current-main containment and repeated ref checks.

| Role | Commit | Tree | Exact path | Blob |
| --- | --- | --- | --- | --- |
| Target TASK-381 r1 | `bf1b3fa121fc7daebaa8257aa81d6546611e06e7` | `5caf8e0ad6f39bc94d74677e5e38c7a437afb0a5` | `.ai/tasks/TASK-381.yaml` | `3db2f20f9a42560d0d1b55db522bafb0cca87683` |
| Contract | `0ff5a56674c5f56012e0df551a069d6c461fd8fa` | `09e0b4bc63657216bfc2d73f6efd4b507fc849d6` | `docs/aios-proof-contracts/TASK-381-contract.json` | `f6d72b058e4276f0c3f0e0f6215e0369014fc70e` |
| Mapping | `0ff5a56674c5f56012e0df551a069d6c461fd8fa` | `09e0b4bc63657216bfc2d73f6efd4b507fc849d6` | `docs/aios-proof-contracts/TASK-381-mapping.json` | `00d8410d8f9d1086e286c6b07e0290442f68d0c2` |
| Mapping-review TASK-382 r1 | `e381e921bbf82dd131c7178f35c4aad9ccac22dd` | `005b4b50867e5c1d7ee944f176004ade29070101` | `.ai/tasks/TASK-382.yaml` | `c745dc9c9eb7e3649ba0394221cc0dc895c7c995` |

Target raw SHA-256 is
`d18a89744492a3e1d7cb31b78969a2f976788d1ca4dedf717378fc0b22465a8d`;
review TASK raw SHA-256 is
`ecc5269d2e69de82a49d3c5a0d7b394277dca9213431a275ca1d8cf1864f03e1`.
Contract `TASK-381-semantic-contract` r1 has normalized digest
`4052f6bcc8aea72a214083e38b47aa0669c2533eb0fa85f8d9111dfb5ad424c8`;
mapping `TASK-381-semantic-mapping` r1 has normalized digest
`db9c9b4e3b5baa93598843bf84c809c996738f5d0003ec3d6aa177b673ca3aec`.
Raw TASK hashing and existing VP-02 normalization remain distinct.

The derived selection ref is
`refs/heads/aios/l1-human-selection/TASK-381/bf1b3fa121fc7daebaa8257aa81d6546611e06e7`.
Its supplied immutable commit is
`c735c45134da36cd581ebce6547d4acf991a72d9`, authenticating the original Human
choice through Issue #2178 under the exact canonical carrier policy. The
reader independently reauthenticates repository/Issue/actor identities,
original body digest and unedited content. A receipt or this copied identity
alone is insufficient. Missing or competing selection refs fail closed.

The selected RUN is `RUN-382-001`, base
`e381e921bbf82dd131c7178f35c4aad9ccac22dd`, reviewed/published candidate
`0ff5a56674c5f56012e0df551a069d6c461fd8fa`; its exact raw RUN digest is
`77afa26b9ef3dbf4f2e93d238a3a315dc84bb84f92d58d5b35956587f280f548`.
Artifact ref `aios/artifacts/RUN-382-001` must resolve to
`a760736536391ed10a232b5b1790c5d21b90718c`. The existing reader validates its
canonical RESULT/EVIDENCE against that RUN, candidate and all review TASK
acceptance. `lineage.result_sha256` binds the complete ResultPackage bytes,
including EVIDENCE, without re-executing or fabricating them.

Decision ref `aios/review-decision/RUN-382-001` must resolve to
`504a88d2ee5076ce8508c61907d00a5b46682600`, containing `REVIEW-382-001`,
PRIMARY PASS for all AC1–AC9, with no findings. The independent reader checks
the authorized mapping/contract delta, complete correspondence and exact
review subjects; the integration additionally requires these particular
published identities. Publisher authority means that exact reviewed source
is contained in current canonical main with the selected bytes surviving;
it does not identify a Git push actor. An identical tree without actual
candidate ancestry is insufficient.

The selected request binds the exact URL
`https://github.com/trung-via/AIOS-renew`. The existing transport requires raw
and effective configured URL equality; spelling it with `.git` is different.
The reader never normalizes this mismatch, rewrites config, searches another
remote or substitutes a fixture. An unavailable GitHub authentication, source
or transport observation remains unestablished/unknown. A later main may
contain the unchanged published sources, but a stale expected-main pin fails.

## Complete associations, without proof discharge

The immutable source binding retains the entire contract, mapping, 23
obligations, 23 used proofs and 23 entries, not just these display counts.
`target_acceptance` and `review_acceptance` retain all original parsed condition
text; authenticated raw blob/digest bindings preserve the complete exact
source bytes. `lineage.scope.acceptance` retains every selected association.

| Target acceptance | Complete obligation suffixes (`T381-`) |
| --- | --- |
| AC1 | D01, D02 |
| AC2 | N01, N02, N03 |
| AC3 | B01, B02, B03 |
| AC4 | S01, S02, S03 |
| AC5 | A01, A02, A03 |
| AC6 | R01, R02 |
| AC7 | L01, L02 |
| AC8 | T01, T02 |
| AC9 | DOC01 |
| AC10 | SC01, SC02 |

| Review acceptance | Target acceptance | Complete selected obligation suffixes |
| --- | --- | --- |
| AC1 | AC3 | B02 |
| AC2 | AC1, AC2, AC3 | D01, D02, N01, N02, N03, B01, B03 |
| AC3 | AC4 | S01, S02 |
| AC4 | AC5 | A01 |
| AC5 | AC5 | A02, A03 |
| AC6 | AC10 | SC01 |
| AC7 | AC9 | DOC01 |
| AC8 | AC6, AC7, AC8 | R01, R02, L01, L02, T01, T02 |
| AC9 | AC10, AC4 | S03, SC02 |

All 19 distinguishing condition fields survive: candidate/base, population
reference/size, test, fixture, shared-state, profile, worker mode/count,
concurrency, ordering, integration, repetition, toolchain, environment,
evidence kind/schema and collection. Obligation revision/digest, claim text,
acceptance associations, blocking/kind and proof identities are retained.
Matching labels or test success do not determine semantic adequacy. The
observed Reviewer verdict adjudicates TASK-382's mapping-declaration AC1–AC9;
it is not target TASK-381 proof discharge, actual execution of all prospective
proof populations, or L2 source-to-target evidence applicability.

## Independent diagnostics and the prospective lane

`observation_status=SELECTED_LEGACY_LINEAGE_ESTABLISHED` describes only the
exact selected legacy lineage. It is never a combined readiness PASS.

| Dimension | Legacy selected-overlay lane | Prospective semantic-TASK lane |
| --- | --- | --- |
| Structure/coverage | `STRUCTURALLY_COMPLETE` after unchanged validation; otherwise `UNKNOWN` | `UNKNOWN` |
| Source consistency | `SOURCE_CONSISTENT` or `UNKNOWN` | `UNKNOWN` |
| Source authentication/currentness | `ESTABLISHED_FOR_OBSERVATION` or `UNKNOWN` | `UNKNOWN` |
| Human choice | Exact independently `AUTHENTICATED_SELECTION` or `NOT_ESTABLISHED` | `NOT_ESTABLISHED`, selection `None` |
| Reviewer authority | `REVIEW_AUTHORITY_ESTABLISHED` or `NOT_ESTABLISHED` | `NOT_ESTABLISHED` |
| Publisher containment | `EXACT_SOURCE_PUBLICATION_ESTABLISHED` or `NOT_ESTABLISHED` | `NOT_ESTABLISHED` |
| Mapping-review Runtime artifacts | `CANONICAL_RESULT_EVIDENCE_BOUND_TO_REVIEW_RUN` or `UNESTABLISHED` | No bound review RUN |
| Semantic judgment | `EXISTING_MAPPING_REVIEW_JUDGMENT_OBSERVED`, verdict `PRIMARY_PASS_FOR_TASK_382_AC1_AC9`, or unestablished/unknown | `NOT_ESTABLISHED` |
| Actual target canonical verification/evidence applicability | `UNESTABLISHED` / `UNESTABLISHED` | `UNESTABLISHED` / `UNESTABLISHED` |

Source failure drops the entire source/authority projection and returns
`SOURCE_OBSERVATION_NOT_ESTABLISHED`. Counts then use `None`, not a fabricated
zero or complete count. Authority failure can retain valid structural/source
facts; an independently authenticated exact Human choice can survive absent
review, while Reviewer/Publisher authority stays unestablished. Reasons are
bounded diagnostics, not raw process logs or replacement lifecycle truth.

`prospective_semantic_task` has schema `task-semantic-v1`, source `None`, status
`NO_DISTINCT_FUTURE_SUBJECT_OBSERVED`, and bridge
`UNSUPPORTED_LEGACY_TO_SEMANTIC_V1`. Once exact legacy source is read, its
`legacy_source_compatibility` is `REJECTED_BY_TASK_SEMANTIC_V1`; otherwise it is
`UNKNOWN`. This is not a claim of exhaustive source discovery.

TASK-381's command-bearing legacy bytes cannot enter the future decoder or
`read_semantic_task_readiness` by relabelling. A genuine future observation
requires a distinct exact canonical `task-semantic-v1` envelope, independent
cycle-free raw TASK/contract/mapping source association and separately
authenticated Human mapping choice/review/publication. Current selection
ingress retains legacy TASK parsing, so the future source/selection/admission
bridge still requires separately authorized engineering. This API accepts no
future fixture that could populate that lane. Explicit synthetic future
readiness tests exercise the existing seam only and convey no live consent.

Both lanes and the root retain `production_eligible=false` and
`execution_authorized=false`. The future lane/root retain
`future_version_admission=NOT_ADMITTED`; root G3/G6 and production opt-in are
`NOT_ESTABLISHED`, KA-01 is `NOT_ACTIVATED`. The existing source/authority
readers retain their original, more limited status meanings. Every observation
is transient cognitive support: later lawful action needs its own fresh
canonical checks. There is no saved token, status database or permission gate.

## Grounded L1 exit checklist and remaining gates

The Human planning allocation remains 16 slots: L1=3 (including TASK-381),
L2=3, L3=6, L4=4, with KA-01 G6 activation separate after L3. Planning carrier
#2168 does not itself canonicalize `.ai/roadmap-state.yaml`. Slots are not an
implementation completion counter or authority to mutate roadmap state.

| Slot/gate | Exact exit facts to reconcile | Residual boundary |
| --- | --- | --- |
| L1-01 / TASK-381 | Published legacy implementation of the distinct immutable decoder/readiness seam; exact source and its own Runtime/review/publication lineage | The target remains legacy; future semantic TASK admission is unsupported. Earlier TASK-376/377/378/380 readers are foundations, not extra planning slots. |
| L1-02 / TASK-382 | Exact published real contract/mapping, 10 target AC, 9 review AC, 23 complete obligations; actual RUN-382-001 artifacts, REVIEW-382-001 PRIMARY PASS and Issue #2178 selection | Authenticate these inputs independently at exact current main; mapping review is not actual target proof or evidence applicability. |
| L1-03 / TASK-383 | This bounded integrated observation, two-lane falsification and safety regressions | Its own canonical Runtime PASS, independent exact-source semantic review and Publisher publication must actually occur before claiming this engineering slot closed. This document cannot supply them. |
| Future semantic admission | Actual prospective carrier/ingress, canonical source/selection support, exact future schema and policy admission, preserved historical behavior | No live future subject or Human future selection is manufactured from the legacy overlay; operative support requires a separately authorized TASK. |
| L2 / VP-03 | Authenticated original provenance/checkpoints and complete source-to-current-target applicability witnesses | `VALID` requires all code/test/fixture/helper/shared-state/toolchain/environment/policy/profile/worker/concurrency/ordering/repetition/collection/integration conditions; changed or unknown conditions remain `INVALIDATED`/`UNKNOWN`. No filename/diff-only reuse. |
| L3 / VP-04 and VP-05 | One existing Runtime-owned proof scheduler, full authorized coverage, no duplicate checkpoint/terminal execution; authenticated factual feedback and the same live Executor/lease/model/profile/effort | Finite consent/resources, scope/risk/progress, STOP/crash/replay/idempotent accounting, terminal and reviewed rollout/rollback guards require actual core engineering and independent safety proof. No new scheduler or lifecycle controller here. |
| KA-01 G3 | Actual eligible L1–L3 core, focused Runtime safety verification, independent semantic review and exact-source publication under lawful versioned G1/G2 eligibility | Readiness, fixture PASS, document publication or a slot count cannot establish G3. |
| KA-01 G6 | After actual G3, authenticated Human selection of exact eligible published implementation/version/policy/profile and finite per-RUN admission opt-in | Planning continuation and selection of a mapping are not production consent. KA-01 stays NOT_ACTIVATED here. |
| L4 / VP-01, VP-06, VP-07; G4/G5 | Matched whole-episode V1 baseline versus lawful future production episodes, exact time/tokens/resources/Human effort/unique and duplicate tests; full real AC-01–AC-36 conformance with review/publication | VP-01 matched measurement is explicitly deferred to L4, outstanding and unproved. Savings/speedup remain UNKNOWN. These postactivation measurements are not a circular prerequisite to the first otherwise eligible G6 opt-in. |

## Falsification and verification boundary

`tests/test_lean_vp_l1_integrated_readiness.py` embeds the exact published
selection/decision/artifact Git objects as a bounded snapshot. All original
object IDs and original Issue-body digest are checked. Disposable local Git
transport and explicitly synthetic replayed HTTP responses exercise the
unchanged authenticated readers; that positive snapshot is not live Human
authentication, canonical EVIDENCE or proof of future admission. The separate
opt-in live observation uses no replay provider and never substitutes fixture
success when an external observation is missing. Unrequested live observation
is explicitly skipped as UNKNOWN.

Adversarial cases cover absent/competing/replayed selections, independent
Human failure, wrong subjects, partial/unrelated review, swapped RUN/RESULT/
EVIDENCE/candidate, conflicting lifecycle refs, absent Publisher containment,
stale main, exact remote mismatch, source/contract/mapping/digest and carrier
policy fingerprint drift, every changed condition, mid-read races,
legacy/future parser separation and future PRIMARY-loader rejection, immutable
reporting, forbidden authority arguments and process/repository side effects.
The positive snapshot compares every projected obligation, proof, mapping
entry and distinguishing condition to the independently read original blobs,
and checks the bound RUN, ResultPackage and REVIEW byte digests.
Legacy V1/V2 parser behavior and the existing future reader remain separate;
no operative caller is modified. Runtime owns canonical test execution and
EVIDENCE; these test definitions assert no execution result.

Revision 2 also corrects the required existing
`test_mapping_review_delta_is_separate_from_historical_target_delta` in
`tests/test_lean_vp_l1_real_mapping.py` within its newly authorized fourth path.
Its exact original four-file delta is measured from RUN base
`e381e921bbf82dd131c7178f35c4aad9ccac22dd` to the immutable reviewed/published
TASK-382 candidate `0ff5a56674c5f56012e0df551a069d6c461fd8fa`, rather than a
later TASK-383 HEAD. The unchanged historical production-module bytes are
compared at that same candidate against TASK-381. The exact four-file set,
target TASK inspect-only/no-delta checks, isolated two-source delta and negative
mapping source-role substitution checks remain intact; nothing is skipped or
weakened. Existing production definitions are also compared to that published
candidate in the integrated regressions, preserving the legacy behavior while
allowing the separate integrated API to be added.

The three original implementation surfaces reuse the lawful failed candidate
`ae00a011d2994099983af1ee97ac700701c13ad4` as source material for this revision.
RUN-383-001/002 and their failures remain historical; neither is relabelled or
replayed as this RUN, nor used as proof of PASS. Only the four revision-2 paths
change. There is no new lifecycle store, mapping issuer, scheduler or roadmap
writer, and no measurement or performance inference.
