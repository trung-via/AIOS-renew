# Lean VP L1-02: real TASK-381 mapping for independent review

Status: prospective source declaration authored by TASK-382 r1 / RUN-382-001. No Human mapping selection, semantic Reviewer PASS, canonical target proof discharge, evidence applicability, publication observation or activation is asserted. **KA-01 remains NOT_ACTIVATED.**

## Exact subjects and one-way binding

The target is TASK-381 r1 at `bf1b3fa121fc7daebaa8257aa81d6546611e06e7` (RUN-381-002). Its immutable source is `.ai/tasks/TASK-381.yaml`, Git blob `3db2f20f9a42560d0d1b55db522bafb0cca87683`, raw SHA-256 `d18a89744492a3e1d7cb31b78969a2f976788d1ca4dedf717378fc0b22465a8d`, 10698 bytes. The raw digest binds every original condition below, including whitespace and final newline. The target TASK remains inspect-only and byte-unchanged. REVIEW-381-001 is the supplied ordinary engineering review, not mapping adequacy approval.

The separate source pair is `docs/aios-proof-contracts/TASK-381-contract.json` and `docs/aios-proof-contracts/TASK-381-mapping.json`. Both exact paths are in TASK-382 r1 `scope.modify`. This TASK also permits only this handoff document and `tests/test_lean_vp_l1_real_mapping.py`. No historical TASK, production module, preexisting test, RUN, RESULT, EVIDENCE, REVIEW or roadmap is rewritten.

Contract pin: `TASK-381-semantic-contract` r1, normalized SHA-256 `4052f6bcc8aea72a214083e38b47aa0669c2533eb0fa85f8d9111dfb5ad424c8`. Mapping pin: `TASK-381-semantic-mapping` r1, normalized SHA-256 `db9c9b4e3b5baa93598843bf84c809c996738f5d0003ec3d6aa177b673ca3aec`. These values are derived with the unchanged VP-02 normalization, not raw JSON hashing. Obligation digests bind complete normalized obligations. Mapping entries retain exact obligation revision/digest and claim/condition equality.

The dependency is one-way: immutable TASK bytes -> external contract -> external mapping -> later independently authenticated selection context. TASK-381 contains no backlink or digest derived from either new source. Editing it to install this overlay would invalidate the subject and is forbidden.

| Target acceptance | Exact original condition | Obligations |
| --- | --- | --- |
| AC1 | One separately discriminated future semantic-TASK version and immutable strict decoder represent ordinary TASK WHAT/WHY/acceptance/scope plus semantic proof obligations, without executable command-policy fields or hidden Human/Executor runtime authority. | `T381-D01`, `T381-D02` |
| AC2 | The decoder rejects unknown/ambiguous versions, unsupported shape, duplicate or incomplete acceptance and obligations, shell-like semantic commands, extra authority metadata and attempts to reinterpret legacy TASK text as the future schema. | `T381-N01`, `T381-N02`, `T381-N03` |
| AC3 | The prospective contract preserves all acceptance and distinguishing proof-condition identities and requires independently derived raw TASK/source bindings with no self-referential TASK→contract digest→TASK cycle. | `T381-B01`, `T381-B02`, `T381-B03` |
| AC4 | An explicit read-only readiness interface validates the new version against actual authenticated immutable TASK, proof-contract/mapping and current source observations using the existing TASK-376/377 validators rather than caller assertions or a second coverage algorithm. | `T381-S01`, `T381-S02`, `T381-S03` |
| AC5 | The interface composes the existing TASK-378/380 authenticated Human mapping-review selection and complete Reviewer/Publisher lineage independently of semantic declaration labels; missing, stale, partial, swapped or conflicting authority cannot establish mapping review eligibility. | `T381-A01`, `T381-A02`, `T381-A03` |
| AC6 | Readiness results distinguish structure, Git/source currentness, Human selection, Reviewer/Publisher authority, Runtime evidence applicability, future version admission and KA-01 activation with truthful conservative statuses; no result grants runnable eligibility or proof PASS. | `T381-R01`, `T381-R02` |
| AC7 | The existing canonical parse_task, AUTHOR_TASK, PRIMARY, V1/V2 verification, correction, publication and historical decoding paths retain their previous accepted/rejected inputs and never invoke or silently auto-detect the future decoder. | `T381-L01`, `T381-L02` |
| AC8 | Focused tests cover independently authenticated positive synthetic proof/readiness bindings and negative conditions including circular pin substitution, wrong version, missing acceptance, untrusted intent, unrelated PASS, stale/ref races, changed semantic sources, and no read-only side effects. | `T381-T01`, `T381-T02` |
| AC9 | Document the exact future format, cycle-free independent source association, explicit invocation and conservative readiness values, retained V1/V2 compatibility, outstanding live semantic mapping, G3 engineering and KA-01 G6 opt-in boundaries. | `T381-DOC01` |
| AC10 | The implementation modifies only the eight authorized paths and does not add a second Runtime/Reviewer/Publisher, new lifecycle state database, scheduler, worker/profile selector or roadmap mutation. | `T381-SC01`, `T381-SC02` |

## Prospective proof meaning and finite populations

Every claim in these JSON documents states what must be proved. A proof descriptor is a required observation specification, not an executed test, valid EVIDENCE, passed proof or semantic approval. All provenance labels mean authoring-only or review-required. They must never be interpreted as authenticated authorities. Each obligation has its own used proof and exactly one mapping entry; no proof reuse is requested. Semantic adequacy remains for independent Reviewer judgment, including whether the listed existing tests actually establish each property.

Each population below is the complete required set for that obligation; size is the number of semicolon-separated cases, not a guessed pytest collection count. Each case requires a matching source-bound observation with the relevant positive assertion or explicit rejection/conservative result. A parameterized test may cover several cases and a case may need several assertions. A missing case prevents discharge. These declarations do not claim the historical verification command exercised them. Coverage gaps must be surfaced by review, never repaired by inventing PASS or weakening a population.

| Obligation / target AC | Population reference suffix / size | Complete required cases | Profile family |
| --- | --- | --- | --- |
| T381-D01 / AC1 | `decoder-normalization-r1` / 3 | valid complete future envelope; reordered acceptance and obligations; reordered fixture identities | decoder |
| T381-D02 / AC1 | `decoder-immutability-r1` / 6 | mutating decoded TASK; mutating nested condition; mutating original input after decoding; file access trap; process access trap; network access trap | decoder |
| T381-N01 / AC2 | `version-shape-rejections-r1` / 15 | missing schema; boolean schema; integer schema; null schema; unknown version; list schema; legacy TASK bytes; duplicate key; multiple documents; YAML anchor; explicit tag; BOM; invalid UTF-8; non-object root; oversize envelope | decoder |
| T381-N02 / AC2 | `command-authority-rejections-r1` / 20 | verification field; executor field; return affinity field; Human selection field; contract digest field; source locator field; envelope digest field; policy field; budget field; activation field; provenance on requirement; leading interpreter invocation; shell invocation; script path; generic command flag; substitution; pipe; chaining; redirection; function-call expression | decoder |
| T381-N03 / AC2 | `requirement-rejections-r1` / 10 | duplicate acceptance; partial acceptance; unknown acceptance; duplicate obligation; missing obligation; missing condition; conflicting claim text; mixed candidate subjects; boolean revision; extra requirement field | decoder |
| T381-B01 / AC3 | `condition-retention-r1` / 19 | candidate_sha; base_sha; population_ref; population_size; test_ref; fixture_refs; shared_state_ref; profile_ref; worker_mode; workers; concurrency_ref; ordering_ref; integration_ref; repetition_ref; toolchain_ref; environment_ref; evidence_kind; evidence_schema; collection_ref | decoder |
| T381-B02 / AC3 | `cycle-and-pin-rejections-r1` / 6 | TASK-side contract pin; TASK-side mapping pin; TASK-side envelope digest; contract TASK digest replaced by mapping digest; changed raw TASK whitespace; normalized YAML digest replacing raw digest | source |
| T381-B03 / AC3 | `profile-separation-r1` / 2 | isolated serial requirement; shared integration requirement declaring two parallel workers | decoder |
| T381-S01 / AC4 | `authenticated-source-positive-r1` / 1 | complete future-format local Git source observation | source |
| T381-S02 / AC4 | `source-substitution-rejections-r1` / 9 | wrong TASK identity; wrong TASK revision; partial acceptance; wrong target candidate; swapped contract; swapped mapping; changed semantic condition; copied source labels; stale ancestor blob | source |
| T381-S03 / AC4 | `source-currentness-rejections-r1` / 4 | main moves between observations; configured remote changes; changed current TASK blob; changed current contract or mapping blob | source-race |
| T381-A01 / AC5 | `independent-lineage-positive-r1` / 1 | complete independently authenticated synthetic Human and mapping-review lineage | authority |
| T381-A02 / AC5 | `authority-rejections-r1` / 11 | missing Human selection; untrusted Human; copied actor provenance; wrong selected scope; competing selection refs; missing review decision; partial acceptance review; swapped RUN; unrelated ordinary PASS; changed decision source; stale selection policy | authority |
| T381-A03 / AC5 | `authority-currentness-rejections-r1` / 2 | selection ref moves after initial read; review decision ref moves after initial read | authority-race |
| T381-R01 / AC6 | `status-separation-r1` / 2 | complete synthetic source and authority context; source-consistent context without Human selection | authority |
| T381-R02 / AC6 | `nonoperative-results-r1` / 2 | source-consistent unselected result; fully matching synthetic lineage result | authority |
| T381-L01 / AC7 | `legacy-decoder-compatibility-r1` / 4 | valid historical V1 command TASK; valid historical V2 command TASK; future TASK passed to legacy parser; legacy TASK passed to future parser | legacy |
| T381-L02 / AC7 | `operative-ingress-rejections-r1` / 4 | AUTHOR_TASK future envelope rejection; PRIMARY loader future envelope rejection; legacy source reader future envelope rejection; legacy Human-selection producer future envelope rejection | legacy |
| T381-T01 / AC8 | `existing-test-adequacy-audit-r1` / 4 | tests/test_lean_vp_l1_versioned_task.py; tests/test_task.py; tests/test_proof_coverage_contract.py; tests/test_lean_vp_l1_human_selection.py | audit |
| T381-T02 / AC8 | `read-only-effects-r1` / 2 | pure decoder effect traps; authenticated readiness Git allowlist and state snapshot | source-race |
| T381-DOC01 / AC9 | `documentation-boundaries-r1` / 8 | exact future shape and decoder; cycle-free source association; explicit reader invocation; separate conservative statuses; V1 and V2 compatibility; unobserved live adequate mapping; outstanding G3 engineering; separate Human G6 opt-in | audit |
| T381-SC01 / AC10 | `authorized-target-delta-r1` / 8 | src/aios_renew/task.py; src/aios_renew/proof_coverage_contract.py; src/aios_renew/review_transport.py; tests/test_task.py; tests/test_proof_coverage_contract.py; tests/test_lean_vp_l1_human_selection.py; tests/test_lean_vp_l1_versioned_task.py; docs/AIOS-LEAN-VP-L1-VERSIONED-SEMANTIC-TASK.md | audit |
| T381-SC02 / AC10 | `single-owner-boundaries-r1` / 7 | canonical Runtime; independent Reviewer; Publisher; lifecycle state storage; verification scheduler; worker and profile selection; roadmap state | audit |

All population references have prefix `t381-`. Their immutable r1 meaning is the exact row above plus the corresponding JSON claim. These are bounded semantic requirements rather than inferred filename or command coverage.

## Complete distinguishing condition definitions

Every condition field is explicit in both source documents. References below are definitions for independent review, not a new registry, evidence schema implementation or execution plan. Changed content requires a new identity and contract/mapping revision; retaining a label for changed content is forbidden.

| Dimension | Required meaning |
| --- | --- |
| `candidate_sha`, `base_sha`, `kind` | Exact target `bf1b3fa121fc7daebaa8257aa81d6546611e06e7`; null base and candidate kind for every obligation. No base replay, comparison proof or equivalence to another candidate is claimed. Historical source audit reads the original RUN base only as immutable provenance, never executing it. |
| `population_ref`, `population_size` | Complete enumerated population above with its exact count. No discovered file list substitutes. |
| `test_ref` | `t381-published-test-code-bf1-r1`: exact test sources in the code table below, read at the target candidate. A later test edit is a different identity. New TASK-382 tests check this overlay and cannot retrospectively become target-candidate test code. |
| `fixture_refs`, `shared_state_ref` | Exact profile-family fixture and state definitions below, including setup/teardown, source builder, mock HTTP and effect guards at the target. Fresh case state is required; test-code and fixture changes invalidate equality. |
| `profile_ref`, `worker_mode`, `workers` | The declared family profile, serial with exactly one proof observer. These are observation conditions, not Executor selection or worker launch authority. B03 separately checks encoded serial versus parallel-two-worker requirements; it does not assert tests executed with two workers. |
| `concurrency_ref`, `ordering_ref` | Exact family topology and order below. Controlled inter-observation mutations are serial race simulations, not simultaneous-thread conformance. Stable and moving-ref cases are never equivalent. |
| `integration_ref` | Isolated decoder, Git/intake integration, authenticated authority integration, legacy ingress or source/document audit as separately declared below. Isolated decoder results cannot cover integrated source/authority behavior. |
| `repetition_ref` | `t381-one-complete-population-no-retry-r1`: one complete population per obligation; no retry or flaky-result selection. A new run or repetition purpose needs explicit distinct conditions. |
| `toolchain_ref` | `t381-locked-python-git-toolchain-r1`: prospective CPython 3.14.7, pytest 8.4.2, PyYAML 6.0.3, Git 2.55.0.windows.4; disable automatic third-party pytest plugin loading. Observer must additionally capture all imported dependency versions and dependency-lock bytes from the exact target source. Different interpreters, tools, plugins, lock content or observer code are different conditions and require a revised identity. These are required future conditions, not a claim about historical verification. |
| `environment_ref` | `t381-isolated-local-offline-authority-r1`: prospective Windows 11 build 10.0.26300 AMD64, UTF-8 input/output, Asia/Saigon timezone, fresh temporary repositories/filesystem, isolated Git configuration, no replacement refs/grafts/shallow state, no live GitHub/credentials. Record locale, filesystem case/path behavior and effective Git configuration before use; only identical captured values may be considered equivalent. Other OS/build/architecture, locale or path semantics require a distinct identity. No environment observation establishing proof applicability is supplied here. |
| `evidence_kind`, `evidence_schema` | `prospective-conformance-requirement` / `runtime-owned-observation-required-v1` are required observation descriptions only. They install no Runtime EVIDENCE schema, writer or approval. Existing canonical Runtime EVIDENCE must independently record subject, assertion, completeness and actual conditions before any discharge. |
| `collection_ref` | `t381-exact-source-per-case-observation-r1`: preserve immutable target source, test/fixture/toolchain/environment identities, each case input and expected/observed assertions or rejection, full population completion, no effect violations and original Runtime artifact identity. Source audits require exact Git delta/call-site reasoning, not a command exit or path label alone. No such observations are supplied here. |

| Profile family | Fixture / shared state | Required order, topology and integration |
| --- | --- | --- |
| decoder | `t381-decoder-fixtures-r1` / `t381-isolated-values-r1` | Fresh future_document/task_bytes values and immutable nested requirements per case; inspect normalization and lexical/shape failures with file/process/socket traps. No Git or authority context. Input then decode then assertion/mutation probe; no concurrent observer. Profile `t381-pure-decoder-serial-r1`, concurrency `t381-no-concurrent-observer-r1`, ordering `t381-fresh-input-per-case-r1`, integration `t381-decoder-isolation-r1`. |
| source | `t381-canonical-git-fixtures-r1` / `t381-fresh-local-repositories-r1` | Fresh complete local Git repository and canonical remote fixture; commit exact TASK, contract and mapping, derive locators, read source then validate intake then reobserve remote/main. Actual source bytes are independent of the incoming declaration; no changed-ref interleaving. Profile `t381-source-integration-serial-r1`, concurrency `t381-stable-ref-observers-r1`, ordering `t381-base-candidate-main-read-r1`, integration `t381-source-intake-integration-r1`. |
| source-race | `t381-canonical-git-fixtures-r1` / `t381-interobservation-ref-state-r1` | Same Git source fixture with controlled changes or effect guards between initial read and final recheck; snapshot local/remote non-object state. Preserve read-change-recheck order. A process hook simulates the specific inter-observation change; no simultaneous thread claim. Profile `t381-source-race-serial-r1`, concurrency `t381-controlled-observation-change-r1`, ordering `t381-read-change-recheck-r1`, integration `t381-source-intake-integration-r1`. |
| authority | `t381-human-review-fixtures-r1` / `t381-independent-local-authorities-r1` | Existing future versioned_package, independent local review/artifact-shaped fixtures and mocked GitHub HTTP observations. Only the HTTP adapter is synthetic; private source, Human selection and authority readers remain real. Read sources, authenticate intent, inspect complete review lineage, recheck. Synthetic positive authority is never live approval. Profile `t381-authority-integration-serial-r1`, concurrency `t381-stable-authority-observers-r1`, ordering `t381-sources-human-review-recheck-r1`, integration `t381-authority-integration-r1`. |
| authority-race | `t381-human-review-fixtures-r1` / `t381-interobservation-ref-state-r1` | Existing authority fixture with exact selection or decision ref changed after its initial observation and before its final check. The controlled mutation is a serial interleaving in an isolated fixture; no live ref is created or changed. Profile `t381-authority-race-serial-r1`, concurrency `t381-controlled-authority-change-r1`, ordering `t381-authority-read-change-recheck-r1`, integration `t381-authority-integration-r1`. |
| legacy | `t381-legacy-ingress-fixtures-r1` / `t381-fresh-local-repositories-r1` | Existing command-bearing V1/V2 fixtures and future raw envelope; fresh local repositories for rejection boundaries. Observe historical accepted/rejected baseline before future-envelope rejection; no dispatch or lifecycle writer is authorized by the proof. Profile `t381-legacy-integration-serial-r1`, concurrency `t381-no-concurrent-observer-r1`, ordering `t381-baseline-input-then-future-rejection-r1`, integration `t381-legacy-ingress-integration-r1`. |
| audit | `t381-immutable-source-fixture-r1` / `t381-read-only-source-state-r1` | Read exact target Git objects and source/call sites or published documentation in the table below. Compare the historical RUN base and target delta where needed; no base execution or live authority simulation. Enumerated source/code owners must be inspected individually. Profile `t381-semantic-source-audit-serial-r1`, concurrency `t381-no-concurrent-observer-r1`, ordering `t381-read-source-then-callsite-audit-r1`, integration `t381-source-and-document-audit-r1`. |

### Immutable code and fixture locators

All following paths use commit `bf1b3fa121fc7daebaa8257aa81d6546611e06e7`; source commit/root tree/path/blob locators must be derived from actual Git objects at use time. Existing test helpers are dependency identities, not an invitation to copy their synthetic proof contracts into this real pair.

| Path | Exact Git blob |
| --- | --- |
| `src/aios_renew/task.py` | `3f8fd584b83f11973add600b7eaab9b26ef3d05a` |
| `src/aios_renew/proof_coverage_contract.py` | `328b726c852d8a2ef00aa0ae321b332a20566201` |
| `src/aios_renew/review_transport.py` | `d194bc5602e724236546590b35aca277189b0c8e` |
| `tests/test_lean_vp_l1_versioned_task.py` | `8398540af0eedc2ba0d32070365d483f56ce094e` |
| `tests/test_task.py` | `1c5b17b8998429427b74ed0cebc01c753ad3ada4` |
| `tests/test_proof_coverage_contract.py` | `2dda60271f4686ffc1bfb807828779c8804b6393` |
| `tests/test_lean_vp_l1_human_selection.py` | `885055877e51b0237cccf28d6f35e20cf88601ea` |
| `tests/test_lean_vp_l1_review_authority.py` | `ec2810983c88dbde55c906960b34fdb9766c074f` |
| `tests/test_lean_vp_l1_source_binding.py` | `2b2a5ac444197b7fd23cb855869d30d25aa40db3` |
| `tests/test_github_issue_ingress.py` | `9658f1b5c39d4ad04d692fb56c4458dd090ac774` |
| `tests/git_fixture_support.py` | `5c0c5419d02a380d6e34c95bf71c562612f6cbde` |
| `docs/AIOS-LEAN-VP-L1-VERSIONED-SEMANTIC-TASK.md` | `4dd9ac9dc50e60e2215450424074e672438c075c` |

## Mapping-review delta and source affinity

The target candidate is `bf1b3fa121fc7daebaa8257aa81d6546611e06e7`. The
mapping-review RUN is the already admitted RUN-382-001 for TASK-382 r1, with base
`e381e921bbf82dd131c7178f35c4aad9ccac22dd`. Its final candidate must come from
the independently observed Runtime ResultPackage `result.head_sha`; this document
contains no prediction of that SHA. It must descend from this base and add/change
the two semantic sources. Its final Git delta must contain only the four declared
modified paths. TASK-381's eight-path historical delta is a different subject,
audited by SC01 using the original RUN base. Neither delta proves the other;
historical pytest success and REVIEW-381-001 do not discharge this new contract.

The review TASK's immutable source locator can use:

- `source_sha`: `e381e921bbf82dd131c7178f35c4aad9ccac22dd`;
- `tree_sha`: `005b4b50867e5c1d7ee944f176004ade29070101`;
- `path`: `.ai/tasks/TASK-382.yaml`;
- `blob_sha`: `c745dc9c9eb7e3649ba0394221cc0dc895c7c995`.

Its raw envelope SHA-256 is
`ecc5269d2e69de82a49d3c5a0d7b394277dca9213431a275ca1d8cf1864f03e1`
(11,144 bytes). Its complete nine acceptance IDs and conditions must remain
byte-exact at the selected source, RUN base, mapping-review candidate and current
canonical main. The target source locator can use the older target commit,
root tree `5caf8e0ad6f39bc94d74677e5e38c7a437afb0a5`,
path `.ai/tasks/TASK-381.yaml` and the target blob specified above.

Both new contract and mapping locators should use the actual mapping-review
candidate as `source_sha`; derive its exact root `tree_sha` and the two path-specific
`blob_sha` values from Git objects after the final commit exists. The mapping
source commit must equal that mapping-review candidate, and its blob must actually
differ from the RUN base. Do not use the older target candidate, an unrelated later
commit with unchanged mapping, or a worktree hash as that locator. All three source
commits must be ancestors of the exact observed canonical main, and all selected
path/blob contents must still match current main. Later changes at any selected
path invalidate this association.

Before publication of this pair, a live canonical-source call cannot authenticate
these newly authored paths. The new tests use isolated local repositories with
actual committed production document bytes and synthetic source commits only;
they do not establish live source currentness, review, selection or publication.
The public `bind_canonical_semantic_review_authority` is the existing legacy-TASK
reader for this overlay. `read_semantic_task_readiness` requires an authenticated
`task-semantic-v1` envelope and must reject the unchanged legacy TASK-381.
Rewriting TASK-381 to make that API succeed is forbidden. The current selection
producer retains legacy TASK decoding; its future-format limitation does not
prevent selecting this unchanged legacy subject and is not bypassed here.

## Prospective Human selection handoff

This checklist identifies missing authoritative inputs. It is not an Issue body,
selection event or permission to deliver one. Generic Human continuation and
planning Issue #2168 do not select this mapping. Human may choose another lawful
mapping-review TASK/RUN; its exact association must then be authored and
independently judged. L1-02 closure remains an external Human/Brain milestone.

Only after independently observing Runtime RESULT/EVIDENCE, mapping-specific
independent semantic Reviewer PASS and exact-source publication, assemble these
facts from their authoritative sources. A model must not choose a future SHA,
actor, Issue identity, decision or approval label to fill missing values.

| Required fact | Authoritative observation and exact binding |
| --- | --- |
| Canonical transport and main | Independently selected canonical URL, unique configured upstream fetch/push identity and actual exact `refs/heads/main` SHA at selection time. All source/currentness checks must pass under that URL. |
| Target | TASK-381 r1 raw source/digest/full ten IDs and conditions above, exact target candidate, and independently derived commit/tree/path/blob locator. No target RUN is reopened. |
| Mapping-review TASK | TASK-382 r1 is a proposed eligible subject only. Independently retain its raw digest/full nine acceptance set, immutable source locator above, and exact base/candidate/main byte equality. Both semantic paths must remain in `scope.modify`. |
| Mapping-review RUN | If Human selects RUN-382-001, use its actual `.ai/transport/run.json` bytes at the canonical artifact root: exact RUN ID, SHA-256 of those bytes, TASK ID/revision, actual base and actual reviewed candidate. Do not hash a reserialized RUN or invent terminal `head_sha` in an ACTIVE RUN with null head. Candidate comes from actual RESULT. |
| Candidate and artifact refs | Observe exact SHAs of `refs/heads/aios/review/RUN-382-001`, `refs/heads/aios/artifacts/RUN-382-001` and `refs/heads/aios/review-decision/RUN-382-001`. Artifact root must supply authentic RUN and ResultPackage at `.ai/transport/run.json` and `.ai/transport/result.json`. Existing canonical RESULT/EVIDENCE validation, complete claims, no unresolved work and subject agreement are required. |
| Truthful semantic delta | RESULT must list both newly changed semantic paths and the two other changed permitted files; Git comparison of RUN base/candidate must agree. An unchanged ancestor contract is a supported different case, not this two-new-source package. A changed contract cannot be authorized by inspect-only scope. |
| Actual semantic REVIEW | Independently observe the single exact `.ai/reviews/<actual-review-id>.yaml` in the decision tree. ID and reviewed candidate must match; PRIMARY mode, full review-TASK acceptance PASS, no findings and no prior finding. Decision commit must have the candidate as its parent and preserve target/review TASK and semantic bytes. It must actually judge semantic adequacy of the obligations, population definitions, reference meanings and correspondence; successful schema checks alone are insufficient. No REVIEW ID or PASS is supplied here. |
| Publisher containment | Independently observe exact reviewed mapping candidate contained in current canonical main using existing Publisher source-containment semantics; selected sources must survive byte-exact at their paths. This is not a claim about a push actor and is not established by creating an implementation commit. |
| Exact proof pins and sources | Derive normalized contract/mapping IDs, revisions and digests from actual source blobs using VP-02. Derive all three `GitSemanticSource` locators from actual objects. Copied provenance labels authenticate neither sources nor semantic review. |
| Human and carrier policy | Read actual `.ai/brain-ingress-carriers.yaml` from exact selected main; record its commit/blob/raw SHA-256. Human must independently author a fresh, unedited allowlisted GitHub Issue with the current exact title marker and opened-event author/sender identity. Original Issue/repository/actor numeric IDs, node ID, creation timestamp and exact body must be independently reauthenticated by the existing REST/GraphQL reader; `lastEditedAt` must be null. No live Human event exists by implication of this handoff. |

The future Issue's strict JSON `AIOS_INGRESS_ENVELOPE` version 1 has exactly:

- `format`, `version`, `operation: SELECT_MAPPING_REVIEW`;
- `identity` with only `target_task_id: TASK-381`;
- `expected_state` with `expected_main_sha` and `expected_remote_url` observed then;
- `payload` with exactly `scope`, `sources`, `review_task_source`, and `run`.

`payload.scope` has exactly `version: 1`, `review_task`, `target_task`,
`candidate_sha` (the older target candidate), `contract_pin`, `mapping_pin`, and
`acceptance`. Each TASK pin has exactly `task_id`, `revision`, `envelope_digest`,
`acceptance_ids`. Each proof pin has exactly `id`, `revision`, `digest`.
`payload.sources` has exactly `task`, `contract`, `mapping`; each and
`review_task_source` has exactly `source_sha`, `tree_sha`, `path`, `blob_sha`.
`payload.run` has exactly `id`, `sha256`, `base_sha`, `candidate_sha` (the newer
mapping-review candidate). No actor, reviewer verdict, publication SHA, authority
flag or destination field is accepted in that envelope.

Each `scope.acceptance` item has exactly `review_acceptance_id`,
`target_acceptance_ids`, `obligation_ids`. This proposed mechanical partition for
TASK-382 is not authenticated Human choice or a semantic review verdict. It assigns
every obligation once and covers all ten target and nine review ACs. Independent
Reviewer and Human must judge its suitability for this TASK's actual acceptance
prose; they may reject it. TASK-382 AC2 still requires adequate obligations for
every target property, not just those placed in its carrier row. Correspondence
metadata does not narrow any review criterion.

| Review AC | Exact target acceptance union | Exact obligation IDs (prefix `T381-`) |
| --- | --- | --- |
| AC1 | AC3 | B02 |
| AC2 | AC1, AC2, AC3 | D01, D02, N01, N02, N03, B01, B03 |
| AC3 | AC4 | S01, S02 |
| AC4 | AC5 | A01 |
| AC5 | AC5 | A02, A03 |
| AC6 | AC10 | SC01 |
| AC7 | AC9 | DOC01 |
| AC8 | AC6, AC7, AC8 | R01, R02, L01, L02, T01, T02 |
| AC9 | AC4, AC10 | S03, SC02 |

Existing `validate_mapping_review_scope` can check this partition's complete sets
and pins. It cannot approve its semantics. Existing authenticated ingress alone
may record subsequent Human selection in the derived immutable
`refs/heads/aios/l1-human-selection/TASK-381/bf1b3fa121fc7daebaa8257aa81d6546611e06e7`
with `.ai/transport/l1-human-selection.json`. The selection reader must independently
reauthenticate the original Human event and unchanged policy, exact RUN and source
associations, then obtain actual Reviewer/Publisher subjects from existing canonical
refs and reobserve all authority/currentness subjects. This Executor creates none
of those carriers or refs. An unselected or mismatched pair must leave Human choice,
review lineage and Publisher authority NOT_ESTABLISHED; invalid sources raise.

## Remaining facts and unchanged owners

Structural coverage is only declared coverage. Actual target verification and L2
evidence applicability remain `UNESTABLISHED`; Human selection and independent
mapping-specific Reviewer/Publisher authority remain `NOT_ESTABLISHED` until their
own real observations. Semantic adequacy remains
`REQUIRES_INDEPENDENT_REVIEWER_JUDGMENT`. Even established future selection and review
lineage cannot change `NOT_ADMITTED` future policy, `NOT_ESTABLISHED` G3/G6 or
production opt-in, `NOT_ACTIVATED` KA-01, or false production/execution eligibility.
No label, prospective toolchain, synthetic fixture or complete declaration is a
discharged obligation, live readiness, Runtime PASS or measured savings.

The focused TASK-382 tests load the committed pair and original committed target
bytes, derive independent TASK pins, use unchanged VP-02 validators and the actual
read-only canonical source/authority reader, and reject coherent substitutions,
missing obligations/coverage, wrong claims, duplicate or unused mappings, condition
conflation, false comparison/base equivalence, source-role swaps and ancestor-label
reuse. They distinguish TASK-382's four-file delta from the historical subject.
They submit no Human selection, create no canonical REVIEW and impersonate no
Publisher. Test execution and canonical EVIDENCE belong to Runtime; test source
is an implementation deliverable, not a report of executed verification.

Only the four declared files change. Existing Runtime, Reviewer, Publisher,
validators and readers retain their owners and semantics. There is no new scheduler,
writer, lifecycle store, Human approval issuer, operative policy, primary correction
mechanism or roadmap mutation. Minimum-sufficient-v1 remains this TASK's ordinary
engineering policy; legacy V1/V2 parsing and command acceptance are untouched.
Future Human selection, actual mapping adequacy approval, L1-02 closure, L1-03/L2/L3,
G3 and separate finite G6 consent, VP-01/VP-06 measurements and VP-07 conformance
remain separate milestones, with no roadmap advancement from this pair.
