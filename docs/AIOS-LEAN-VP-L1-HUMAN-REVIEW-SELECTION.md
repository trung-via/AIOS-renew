# Lean VP L1 authenticated Human mapping-review selection

TASK-380 supplies the previously closed Human-selection boundary in
[TASK-378's read-only reader](AIOS-LEAN-VP-L1-REVIEW-AUTHORITY.md). Its historical
description of a private reader returning `None` describes TASK-378 at publication;
this bounded extension replaces that implementation. No live selection, adequate
semantic mapping, canonical verification, publication of this change, or production
activation is asserted by this document. **KA-01 remains NOT_ACTIVATED.**

## One explicit intent contract

The existing allowlisted GitHub Issue ingress accepts one additional operation,
`SELECT_MAPPING_REVIEW`, in `AIOS_INGRESS_ENVELOPE` version 1. This operation accepts
strict bounded UTF-8 JSON only. Existing lifecycle operations retain their existing
YAML/JSON and historical TASK V1/V2 decoding and Issue authentication behavior.

The exact top-level keys are `format`, `version`, `operation`, `identity`,
`expected_state`, and `payload`. There are no optional authority fields:

| Location | Exact contents |
| --- | --- |
| `identity` | `target_task_id` |
| `expected_state` | `expected_main_sha`, `expected_remote_url` |
| `payload` | `scope`, `sources`, `review_task_source`, `run` |
| `scope` | `version: 1`, `review_task`, `target_task`, `candidate_sha`, `contract_pin`, `mapping_pin`, `acceptance` |
| Each TASK pin | `task_id`, `revision`, `envelope_digest`, `acceptance_ids` |
| Each contract/mapping pin | `id`, `revision`, `digest` |
| Each `acceptance` entry | `review_acceptance_id`, `target_acceptance_ids`, `obligation_ids` |
| `sources` | Exactly `task`, `contract`, `mapping`, each an exact source locator |
| Each locator, including `review_task_source` | `source_sha`, `tree_sha`, `path`, `blob_sha` |
| `run` | `id`, `sha256`, `base_sha`, `candidate_sha` |

`scope` serializes the existing frozen `MappingReviewScope` fields: lists encode
its tuples. Every review acceptance, target acceptance and semantic obligation must
participate in its complete correspondence. TASK envelope digests cover complete
raw TASK bytes, including all acceptance conditions, rather than just AC labels.
The review TASK must differ from the target TASK. Its exact selected source bytes
must agree at source, RUN base, reviewed candidate and current main. Sources carry
their exact raw SHA-256/byte count observations through TASK-377; contract/mapping
pins retain VP-02 normalized digest semantics.

`scope.candidate_sha` identifies the target candidate. `run.candidate_sha` identifies
the separately scoped mapping review's candidate; these roles cannot be substituted.
The mapping locator must identify that reviewed candidate and a real mapping delta
from `run.base_sha`. Mapping modification requires the selected TASK's explicit
`scope.modify`. A changed contract also requires explicit `scope.modify`; an unchanged
contract may use `scope.inspect`. The reader additionally checks truthful
`RESULT.changed_files` against the actual semantic Git delta.

There is no caller-controlled destination, actor, review verdict, reviewer label,
authority callback, approval flag, publication SHA, lifecycle command, origin proof,
or audited handoff in this contract. Generic carrier-neutral execution refuses
selection. It must enter through the authenticated Issue path; copied records or
construction of an in-memory selection type grant no authority.

## Independent Human authentication and the existing writer

The supplied event must describe `opened`, the existing exact title marker, and an
allowlisted Human author who is also the event sender. The selection path reads the
allowlist from `.ai/brain-ingress-carriers.yaml` in exact canonical Git main, rather
than accepting a caller's policy as selection authority. The existing main upstream
must have a unique matching fetch/push URL and match the requested URL and SHA.

Using `GH_TOKEN` or `GITHUB_TOKEN`, the ingress independently reads the repository
and Issue from the fixed authenticated GitHub REST host. The existing ingress
workflow supplies its GitHub token; no new authority service or secret is added.
Redirects are refused. Repository ID, Issue number/ID/node ID, author login/numeric
ID/type, title, creation timestamp and exact body must match independent GitHub
observations and the event sender. The Issue must be a Human-authored Issue rather
than a PR or bot record. A fixed read-only GitHub GraphQL query independently
cross-checks the same repository/Issue/body and requires its
[`lastEditedAt`](https://docs.github.com/en/graphql/reference/issues) to be null.
Body edits fail closed, including edits that restore the same text. Receipt comments
and closing the carrier Issue can change REST `updated_at` without editing the
original intent, and do not revoke that intent. The supplied opened event still
must have creation-time timestamps. Authentication is unavailable without
credentials or GitHub availability.
HTTP reads are bounded to 1 MiB and 15 seconds each; selection JSON/records are
bounded to 128 KiB, with TASK-377's strict duplicate/depth/node limits.

The existing Runtime ingress writer adds only
`.ai/transport/l1-human-selection.json` in a metadata commit whose sole parent is
the exact requested main. It reuses `_publish_ingress_ref` and its exact missing-ref
Git lease/CAS and rollback behavior at the captured canonical URL. The ref is derived
solely from target TASK and target candidate:

```text
refs/heads/aios/l1-human-selection/<target-task-id>/<target-candidate-sha>
```

The immutable record's format is `AIOS_L1_HUMAN_REVIEW_SELECTION`, version 1, with
exact `request`, independently observed `provenance`, and canonical `policy` source
pins. Provenance includes repository ID, Issue number/ID/node ID, actor login/numeric
ID, opened action, creation timestamp, and SHA-256 of the exact Human body. The
policy pin binds its main commit, blob and raw digest. Unknown fields are rejected.

Before writing and again before pushing, Runtime reobserves exact main, transport,
selection refs, policy, Human event and source association. Only an identical record
under the same currentness predicates is idempotent; a second event, competing ref,
divergent choice, or stale replay is rejected. Concurrent changes away and back
between observations are outside the observation guarantee, as for TASK-378.
This ref records one choice. It is never a mutable general approval registry.
No main, TASK, RUN, RESULT, EVIDENCE, REVIEW or publication result is written, and
selection emits no publication or repair wakeup output.

## Read-only consumption and distinct outcomes

`bind_canonical_semantic_review_authority` keeps its existing public signature.
There is no public review TASK selector or selectable authority provider. After
TASK-377 source authentication, it derives exactly one selection ref from the target
and candidate. It checks raw commit ancestry and an exact metadata-only addition,
reauthenticates the original Issue independently, checks the current canonical
allowlist/policy bytes and exact semantic associations, and observes the selection
again before returning. A later main descendant is permitted only while the pinned
TASK/semantic sources and selection policy remain exact; stale original-main replay
at the writer is still rejected. Missing selection never triggers TASK discovery.

The selected RUN ID and its raw digest/base/candidate are Human-bound. Existing
canonical review/artifacts/decision refs supply the Reviewer/Publisher subjects;
Human input cannot supply a verdict or manufacture those subjects. TASK-378 still
requires the authentic immutable RUN/RESULT/EVIDENCE, complete claim coverage,
truthful semantic delta, a single exact independently judged PRIMARY REVIEW PASS
with all acceptance PASS and no findings, decision parent/source consistency,
and Publisher candidate containment in current main. Unsupported correction or
partial lineages fail closed. An ordinary engineering PASS cannot substitute for
this distinct selected review TASK.

| Typed output | Meaning |
| --- | --- |
| `human_selection`, `human_intent` | Frozen authenticated selection commit and event identity, or `NOT_ESTABLISHED`; Human choice alone grants no review authority |
| `source_status` | TASK-377 Git source consistency only |
| `reviewer_authority` | `REVIEW_AUTHORITY_ESTABLISHED` only after the complete existing independent Reviewer/Publisher lineage; otherwise `NOT_ESTABLISHED` |
| `lineage.human_selection_sha`, `human_event_identity` | The exact selection association used for a complete lineage |
| `semantic_adequacy` | `REQUIRES_INDEPENDENT_REVIEWER_JUDGMENT`; this code does not judge semantic adequacy |
| `canonical_verification`, `evidence_applicability` | Target Runtime proof and L2 applicability remain `UNESTABLISHED` |
| `future_version_admission` | `NOT_ADMITTED` |
| `ka01_g3`, `ka01_g6`, `production_opt_in` | `NOT_ESTABLISHED` |
| `production_activation` | `NOT_ACTIVATED`; production eligibility and execution authorization remain false |

An authentic choice with no complete review subjects can report authenticated
Human intent while reviewer authority remains `NOT_ESTABLISHED`. Missing, ambiguous,
forged, stale or incomplete subjects return a bounded conservative observation;
existing strict invalid-source/current-main errors remain errors. Observation adds
only immutable Git objects via fetch with no ref, FETCH_HEAD, index, worktree or
remote mutation. No verification command is executed by this reader.

## Fixture scope and remaining lifecycle gates

`test_lean_vp_l1_human_selection.py` replaces only the GitHub HTTP adapter with
independent synthetic repository/Issue observations. It exercises the real ingress
writer and private selection reader with separately materialized canonical
Reviewer/Publisher-shaped positive lineage. Counterexamples include copied actors
and body, event/sender substitutions, selector/acceptance swaps, stale main and moved
selection/decision refs, divergent replay, missing/incomplete review, changed contract
inspection-only scope, and read-only Git side-effect traps. Existing ingress fixtures
retain lifecycle authentication and historical decoding coverage. Synthetic fixtures
are not real Human approval, independent semantic judgment or canonical EVIDENCE.

Runtime owns canonical verification and evidence; Reviewer owns actual independent
judgment; Publisher owns publication. Future TASK/schema/policy admission, L2/L3,
KA-01 G3/G6, exact per-RUN Human opt-in and production activation remain separately
gated. This change does not select an Executor/model/effort, launch an execution,
advance ROADMAP, or establish observed measurement or savings.
