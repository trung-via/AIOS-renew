# Lean VP L1 prospective versioned semantic TASK

TASK-381 introduces exactly one prospective TASK format, `task-semantic-v1`,
and an explicitly invoked read-only readiness reader. It is an L1 engineering
boundary, not operative TASK admission. **KA-01 remains NOT_ACTIVATED.**
No production TASK, RUN, command runner or Human selection producer acquires
new semantics. Full L1, G3 and matched VP-01 measurement remain outstanding.

## Exact format and decoder

`aios_renew.task.parse_semantic_task(raw_utf8_bytes)` returns a frozen
`SemanticTask`, separate from the legacy `Task` type, or raises
`TaskValidationError`. It performs no I/O. This is the only new TASK version;
`task-semantic-proof-intake-v1`, `proof-coverage-v1` and `proof-mapping-v1`
remain their existing, separate declaration formats.

The root has exactly `schema`, `task_id`, `revision`, `goal`, `problem`,
`assumptions`, `scope`, `non_goals`, `constraints`, `acceptance`, and
`proof_obligations`. Every field is required. Ordinary WHAT/WHY, assumptions,
scope, non-goals, constraints and acceptance prose remain Brain-authored.
`scope` contains exactly `inspect` and `modify` string lists; modify paths
are normalized, exact repository-relative file paths. `constraints` contains
exactly the `hard` string list. Acceptance entries contain exactly `id` and
nonempty `condition`; the complete nonempty acceptance set is required.

Each obligation contains exactly `id`, `revision`, `claim`, `acceptance_ids`,
`kind`, `blocking`, and `conditions`. There is deliberately no provenance,
contract/mapping pin, source locator, envelope digest, `verification`, policy,
return affinity, Executor choice, budget, approval or activation field in TASK
bytes. Unknown fields at every level are rejected. A complete example is:

```yaml
schema: task-semantic-v1
task_id: TASK-FUTURE
revision: 1
goal: Preserve required isolation.
problem: Isolation requires an exact semantic proof subject.
assumptions: [Explicit prospective example only]
scope:
  inspect: [src/example.py]
  modify: [src/example.py]
non_goals: [Production activation]
constraints:
  hard: [Preserve the Human authority boundary]
acceptance:
  - id: AC1
    condition: The operation preserves isolation for the complete population.
proof_obligations:
  - id: isolation-proof
    revision: 1
    claim:
      id: isolation-v1
      text: The operation preserves required isolation.
    acceptance_ids: [AC1]
    kind: candidate
    blocking: true
    conditions:
      candidate_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
      base_sha: null
      population_ref: population-v1
      population_size: 8
      test_ref: test-code-v1
      fixture_refs: [fixture-v1]
      shared_state_ref: isolated-state-v1
      profile_ref: serial-profile-v1
      worker_mode: serial
      workers: 1
      concurrency_ref: no-concurrency-v1
      ordering_ref: declared-order-v1
      integration_ref: isolated-v1
      repetition_ref: single-observation-v1
      toolchain_ref: toolchain-v1
      environment_ref: environment-v1
      evidence_kind: conformance-v1
      evidence_schema: observation-v1
      collection_ref: collection-v1
```

`profile_ref`, worker mode/count, test/fixture and other references describe
distinguishing evidence conditions, not runnable commands or runtime selectors.
They cannot choose an Executor, create a process, select a worker/profile,
allocate a budget or grant consent. Comparison obligations require a distinct
non-null base SHA; candidate obligations require a null base. Every declared
condition is required, including an explicit empty fixture list when applicable.

The existing VP-02 contract validator owns complete acceptance coverage,
bounded identities/revisions, candidate/base consistency, duplicate and
conflicting claim/obligation rejection, and condition normalization. A temporary
internal contract adapter supplies fixed non-authoritative provenance solely
to reuse that validator. It is never a source pin or an approval record.
`SemanticRequirement` retains every semantic field except external provenance;
source contract/mapping provenance remains in the existing immutable intake.
There is no second coverage or proof-mapping algorithm. The source reader
compares the complete normalized TASK requirement tuple against the actual
contract obligations, without dropping IDs, revisions, claims, acceptance,
blocking flags or distinguishing conditions. Reordering sets cannot erase
integration, population, fixture, concurrency, profile, collection or other
condition identities.

Input is at most 256 KiB of strict UTF-8 YAML, without BOM, aliases, anchors,
explicit tags, directives or multiple documents. Duplicate keys, non-string
keys, unsupported/ambiguous versions and excessive nesting/token populations
are rejected. YAML nesting is at most 32, scanning at most 65,536 tokens.
Existing VP-02 bounds apply: 256 acceptance/obligation entries, 64 fixtures,
128-character ASCII identities, 2,048-character single-line claims, revisions
through 2,147,483,647, 100,000 population items and 256 workers (serial means
one). Semantic obligations reject recognizable shell invocations, command
paths, substitution, redirection and shell control syntax. This lexical guard
does not judge semantic adequacy. Legacy text is never inferred to be this
version; the exact discriminator and exact shape are mandatory.

## Cycle-free independent source association

The raw TASK contains its requirements, not a hash or pin of a contract or
mapping which itself commits to that TASK. Its own envelope digest is also
absent from TASK bytes. Authoring can therefore proceed without a fixed point:

1. Commit exact future TASK bytes at `.ai/tasks/<task_id>.yaml`.
2. Independently read those immutable raw bytes and derive SHA-256, TASK ID,
   revision and the complete acceptance set. The contract's existing `task`
   binding commits to those facts. Commit the separate contract source.
3. Construct the separate complete reviewed mapping with the existing exact
   contract and obligation digests. Its source can be the separate legacy
   mapping-review TASK's reviewed/published candidate. Neither source changes
   the original TASK bytes.
4. The external authenticated selection context binds all three immutable
   commit/tree/path/blob locators, contract/mapping pins, complete target and
   review TASKs, explicit correspondence, candidates and review RUN. Existing
   independent Human selection, Reviewer and Publisher readers authenticate
   their own subjects. A caller or declaration cannot supply those authorities.

The readiness reader repeats these derivations from actual canonical Git
objects. A caller-selected expected contract digest, mapping label, synthetic
decision or self-consistent copied pin cannot substitute for those reads.
Hashing normalized TASK data instead of exact raw TASK bytes is forbidden;
whitespace changes alter source bindings. Contract/mapping digests retain their
existing VP-02 normalization and are not weakened. An unchanged ancestor source
is valid only while its exact path/blob survives current canonical main.

## Explicit read-only readiness invocation

```python
from aios_renew.review_transport import read_semantic_task_readiness

observation = read_semantic_task_readiness(
    repo, intake_json_bytes,
    expected_remote_url=independently_selected_canonical_url,
    expected_main_sha=exact_current_main,
    expected_candidate_sha=exact_target_candidate,
    sources={"task": task_locator, "contract": contract_locator,
             "mapping": mapping_locator},
)
```

`intake_json_bytes` uses the existing TASK-376 declaration format; each locator
is the existing frozen `GitSemanticSource(source_sha, tree_sha, path, blob_sha)`.
The future TASK itself is read from that authenticated immutable source, not
accepted as a caller-decoded trust assertion. There is no decoder callback,
network authority provider, review selector, approval boolean, expected
contract/mapping pin or alternate trust-root argument.

The common TASK-377 source reader retains exact configured canonical remote
authentication, bounded raw commit ancestry, object hashing, literal tree/path
reads, regular-blob checks, current-main source equality, strict JSON decoding
and repeated main/remote observations. Only this explicit API chooses the future
TASK decoder. Actual source contract/mapping documents and the caller's intake
both go through TASK-376 and the existing VP-02 mapping validator. TASK
requirements must exactly match the independently sourced contract semantics.
Invalid structure, identity, source, currentness or semantic correspondence
raises `ReviewTransportError`; there is no partial successful source result.

The common TASK-378/380 authority reader then requires the existing immutable
mapping-specific selection carrier plus independently authenticated original,
unedited, allowlisted Human Issue intent under canonical policy. Labels such
as `provenance.review_ref` are opaque. One complete independent mapping-review
TASK/RUN/RESULT/EVIDENCE/PRIMARY REVIEW/Publisher lineage is checked through
the existing readers: full acceptance/correspondence, exact changed mapping,
authorized contract delta, exact subjects and publication on current main.
Missing, stale, partial, swapped, unrelated or conflicting authority cannot
establish review eligibility. Selection and review refs, configured remote
and main are checked again before returning. The target candidate and the
mapping-review candidate remain separate roles.

The reader adds no selection producer. Current `SELECT_MAPPING_REVIEW` ingress
retains its legacy TASK source decoding and cannot operationally select this
future format. An authenticated positive synthetic carrier can exercise the
readiness seam without enabling a new authoring or admission path. Live
approved mapping-specific Human selection and adequate semantic mapping are
not presumed to exist.

No decoder/readiness call invokes tests, shell commands from semantics, a
worker, scheduler, admission, correction, publication or lifecycle writer.
Only existing canonical Git reads and bounded immutable-object fetches are
permitted; fetch retains no refspec mapping, tags, FETCH_HEAD, maintenance or
submodule recursion. No refs, main, index or worktree are changed. Currentness
is a bounded repeated observation, not a remote lock or durable receipt;
later lawful use needs its own fresh checks.

## Conservative results and compatibility

The frozen `SemanticTaskReadiness` retains the immutable decoded `task` and
the existing `authority` observation, including authenticated source and
complete lineage details when established. Successful source observation has
these independent dimensions:

| Dimension | Value |
| --- | --- |
| `structure` | `STRUCTURALLY_COMPLETE` |
| `source_consistency` | `SOURCE_CONSISTENT` |
| `source_authentication`, `source_currentness` | `ESTABLISHED_FOR_OBSERVATION` |
| `human_selection` | `AUTHENTICATED_SELECTION` or `NOT_ESTABLISHED` |
| `reviewer_authority` | `REVIEW_AUTHORITY_ESTABLISHED` or `NOT_ESTABLISHED` |
| `publisher_authority` | `EXACT_SOURCE_PUBLICATION_ESTABLISHED` or `NOT_ESTABLISHED`, through the complete existing lineage, not a claim about a push actor |
| `semantic_adequacy` | `REQUIRES_INDEPENDENT_REVIEWER_JUDGMENT` |
| `canonical_verification`, `evidence_applicability` | `UNESTABLISHED` for target Runtime proof/applicability |
| `future_version_admission` | `NOT_ADMITTED` |
| `ka01_g3`, `ka01_g6`, `production_opt_in` | `NOT_ESTABLISHED` |
| `ka01` | `NOT_ACTIVATED` |
| `production_eligible`, `execution_authorized` | `False` |

An authenticated Human choice alone cannot establish independent review.
Complete synthetic source/review/publication correspondence still grants no
target proof PASS, evidence reuse, runnable eligibility or activation. Source
failures raise; authority failures return conservative unestablished authority,
subject to the final source-currentness check. Nested TASK-376/377/378/380
statuses keep their existing meanings rather than being upgraded implicitly.

`parse_task` and `validate_task` retain their exact legacy V1/V2 shape, error
behavior, authored command ordering and command-runner policy. They never call
or auto-detect the future decoder. The old source/review APIs continue to decode
legacy TASKs only. `AUTHOR_TASK`, PRIMARY loading/admission, verification,
correction, publication and historical readers still use their original
parsers. No new policy is registered; `minimum-sufficient-v1` remains current
and V1/V2 accepted/rejected input behavior is retained.

Focused fixtures in `tests/test_lean_vp_l1_versioned_task.py` authenticate real
local immutable Git sources, the existing synthetic HTTP-backed Human Issue
reader and complete independent review/publication-shaped records. No private
authority reader is replaced in the positive fixture. Negative cases cover
versions/shapes, shell-like obligations, omissions/conflicts, circular pin
substitution, cross-TASK/revision/acceptance/candidate bindings, changed semantic
sources, untrusted intent, unrelated/partial reviews, policy changes, ref/main
races and process/repository side effects. The synthetic selection record is
materialized as a fixture, not admitted through a newly enabled producer.
These definitions are not live Human approval, Runtime EVIDENCE or performance
measurements. Runtime owns canonical execution and verification of the tests.

## Outstanding boundaries

Live adequate semantic mapping and exact authenticated mapping-specific Human
choice remain outstanding. Future operative schema/policy admission requires
separate authorized engineering. L2 authenticated evidence applicability and
L3's single Runtime scheduler, checkpoints, same-Executor continuation, finite
resources/consent and replay/crash/stop safety remain separate work. No second
Runtime, Reviewer, Publisher, source registry, lifecycle database or scheduler
is introduced here.

Under [KA-01 v1.1](AIOS-KERNEL-KA01-PROSPECTIVE-AMENDMENT.md), G3 still requires
actual eligible L1-L3 engineering, independent Runtime safety verification,
semantic review and exact-source publication. G6 subsequently requires exact
Human implementation/version/policy/profile selection and finite per-RUN
production opt-in. Roadmap continuation grants neither. Matched VP-01 results
and savings remain UNKNOWN; L4's VP-01/VP-06 real measurements and full VP-07
conformance remain mandatory unproved postactivation commitments. This change
does not mutate roadmap state, authorize a RUN, or activate KA-01.
