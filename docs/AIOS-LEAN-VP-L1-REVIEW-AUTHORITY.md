# Lean VP L1 review-authority eligibility

TASK-378 adds one explicitly invoked, read-only eligibility boundary after
[TASK-377 source binding](AIOS-LEAN-VP-L1-CANONICAL-SOURCE-BINDING.md) and
[TASK-376 structural intake](AIOS-LEAN-VP-L1-SEMANTIC-PROOF-INTAKE.md).
The existing VP-02 contract and complete mapping validators remain the owners
of structural coverage. **KA-01 remains NOT_ACTIVATED; Lean VP remains NEXT.**

## Public interface and closed Human authority boundary

```python
bind_canonical_semantic_review_authority(
    repo, declaration_bytes,
    expected_remote_url=independently_selected_canonical_url,
    expected_main_sha=exact_main,
    expected_candidate_sha=exact_target_candidate,
    sources={"task": task_locator, "contract": contract_locator,
             "mapping": mapping_locator},
)
```

The source inputs have exactly TASK-377's meaning and bounds. The wrapper calls
that existing binder directly, reusing its strict decoders, raw Git ancestry,
current-main source checks, canonical TASK parser and TASK-376/VP-02 validation.
Invalid source input raises `ReviewTransportError`. No active admission,
Runtime, Reviewer, Publisher or authoring path calls this new API.

Source hashing does not authenticate Human selection or independent semantic
review. The public signature therefore accepts no review TASK selector,
resolver, trust-source callback, reviewer label, authority flag or approval ref.
The private `_l1_authenticated_mapping_review_selection` boundary is **closed
and returns None in this implementation**. Existing TASK/RUN/REVIEW formats
do not independently authenticate a Human choice of mapping-specific scope.
This slice supplies no new producer, issuer, approval registry, review carrier,
writable decision ref, trust-root selector or operational integration.

Consequently real calls currently return `NOT_ESTABLISHED`, including calls
whose semantic files exist on main and whose repository has ordinary engineering
REVIEW PASS records. Neither TASK discovery nor acceptance-prose classification
can fill this gap. Future governance and a lawful integration authenticating
the separately Human-selected mapping-review TASK remain independently gated.
Changing or monkeypatching Python code is not an admission mechanism.

## Required exact binding

The private reader's prospective in-memory expected facts must come from
independently authenticated Human selection and existing canonical
Reviewer/Publisher authority, independently of the caller's declaration.
Constructing the private value or its frozen `MappingReviewScope` type
authenticates nothing. The scope is a consistency contract, not a serializable
approval protocol or a new production TASK version.

An established observation requires every one of these associations:

1. One exact **target TASK**, revision, SHA-256 of its complete raw envelope and
   full acceptance set; target candidate SHA; normalized contract and mapping
   IDs/revisions/digests; and all three TASK-377 source locators, content hashes
   and byte counts. Target candidate and mapping-review candidate are distinct
   roles and are never silently substituted.
2. One separately Human-selected **mapping-review TASK**, different from the
   target, with immutable source commit/tree/path/blob, raw envelope digest,
   revision and full acceptance. Its exact bytes must agree at its selected
   source, the review RUN base, the reviewed candidate and current main.
   Its scope must include the contract source and authorize mapping modification.
3. Explicit `MappingReviewAcceptance` correspondence between every review TASK
   acceptance and the target obligations/acceptance. Every obligation appears
   exactly once; all target acceptance and all review acceptance are covered.
   Each entry's target acceptance equals its obligations' acceptance union.
   `validate_mapping_review_scope` checks version 1, exact pins and complete
   correspondence; it never interprets natural-language AC text or decides
   whether that correspondence is semantically adequate.
4. That TASK's **own immutable RUN**, pinned by ID and SHA-256 of actual RUN
   bytes, exact base and reviewed candidate. The canonical candidate must descend
   from the base. The mapping locator must name this reviewed candidate and its
   blob must actually be added or changed in the delta. The exact contract may
   come from an immutable ancestor; both semantic blobs must match the reviewed
   candidate and remain in current main.
   Reviewing an unrelated later source with unchanged mapping files is insufficient.
5. Existing canonical `aios/review/<RUN>`, `aios/artifacts/<RUN>` and
   `aios/review-decision/<RUN>` refs with independently pinned exact SHAs.
   The root artifact commit must supply the RUN and ResultPackage. Existing
   canonical RESULT/EVIDENCE validators check the package; complete claim coverage,
   matching source/candidate and no unresolved items are additionally required.
   No verification commands are executed by this reader.
6. The existing independent REVIEW decision must be a single exact canonical
   review document, in a decision commit parented by the reviewed candidate.
   The decision tree must preserve the selected TASK/semantic source bytes.
   Its actual ID and reviewed SHA must match; PRIMARY mode, full acceptance PASS,
   no findings and no prior finding are required. Duplicate or unknown fields,
   forged reviewer labels, partial/contradictory outcomes and unsupported
   correction/recovery lineages fail closed. This bounded slice supports only
   an ordinary complete PRIMARY lineage; it does not reinterpret historical
   DELTA, REPAIR, REMEDIATION or publication-recovery decisions.
7. Exact **Publisher publication/currentness**: the independently pinned
   published SHA equals that reviewed candidate and is contained in current
   canonical main, using the existing Publisher's exact-source containment
   criterion. TASK-377 still requires the selected semantic bytes at current
   main. This is not a claim about the identity of a Git push actor. Canonical
   refs are consumed under the existing authority boundary, never created here.
   All selected review/artifact refs are observed again, Human context must remain
   identical, and configured remote/main are checked again before returning.

Missing, unavailable, stale, swapped, unrecognized or incomplete review authority
returns `NOT_ESTABLISHED` with no partial lineage. A moved main/configured remote
invalidates source currentness and raises. An unrelated ordinary RUN review,
including **REVIEW-377-001 / RUN-377-002**, can never substitute for this exact
mapping-review TASK and scope. A mapping's own `provenance.review_ref` remains
an opaque label, even if it names a real PASS.

## Bounds and separate statuses

All results and nested scope/source/lineage records are frozen; no mutable input
collections are retained. TASK-377 limits still apply: 4 MiB intake, 256 KiB
TASK, 1 MiB semantic sources/trees, 32 JSON/YAML nesting levels and 4,096 raw
ancestry commits. New RUN and REVIEW reads are at most 256 KiB each; ResultPackage
reads are at most 1 MiB with the existing strict JSON node/depth limits. Review
YAML rejects aliases, anchors, explicit tags, directives, duplicate/unknown
fields, BOM and excessive nesting. Scope entries and acceptance/obligation
populations retain VP-02's 256-element limits. Ref names are derived from the
exact selected TASK/RUN rather than an untrusted review_ref.

| Output dimension | Meaning |
| --- | --- |
| `source_status` | `SOURCE_CONSISTENT`; original TASK-377 currentness observation is retained separately |
| `reviewer_authority` | `REVIEW_AUTHORITY_ESTABLISHED` only with the complete binding above; otherwise `NOT_ESTABLISHED` |
| `semantic_adequacy` | `REQUIRES_INDEPENDENT_REVIEWER_JUDGMENT`; the binder cannot judge semantics |
| `canonical_verification` | Target Runtime proof remains `UNESTABLISHED` |
| `evidence_applicability` | L2 source-to-target applicability remains `UNESTABLISHED` |
| `future_version_admission` | `NOT_ADMITTED` |
| `ka01_g3`, `ka01_g6`, `production_opt_in` | Each remains `NOT_ESTABLISHED` |
| `production_activation` | `NOT_ACTIVATED`; eligibility and execution authorization remain false |

The lineage preserves exact scope, TASK source, RUN/REVIEW/ResultPackage content
digests, candidate/base/publication and decision/artifact identities and observed
refs. These describe one bounded observation, not a durable authority receipt or
remote lock. Concurrent changes away and back between observations are outside
this guarantee. Later use requires its own lawful currentness boundary.
No output substitutes for independent Reviewer judgment or canonical EVIDENCE.
The nested TASK-376/377 objects retain their original conservative statuses.

## Synthetic support and outstanding work

`tests/test_lean_vp_l1_review_authority.py` uses isolated local Git repositories
and explicitly synthetic TASK/RUN/REVIEW/ResultPackage bytes. Its private test
trust source is selected independently of incoming declarations and replaces
only the closed reader within each test. The positive fixture cross-binds a
mapping-specific TASK, full independent-review-shaped PASS and published source.
Negative fixtures cover unrelated ordinary review, missing/mismatched/stale
decisions, incomplete acceptance, forged labels/hashes, wrong versions, changed
sources/main, oversized input and Git-write/process traps. Synthetic records
are **not live Human authorization, semantic approval, measured proof or
canonical EVIDENCE**. Tests contact no live GitHub or Executor and activate no policy.

Live reviewed adequate semantic mapping and the lawful Human selection
integration remain outstanding, together with operative future schema/policy
admission. Full L1 and matched VP-01 measurement remain incomplete; measurements
and savings remain UNKNOWN. L2 authenticated applicability and L3's single
Runtime scheduler, checkpoints, same-Executor feedback, finite consent/resources,
leases and crash/replay/stop safety require separate work. KA-01 G3 requires the
actual eligible independently Runtime-verified, reviewed and published L1-L3
core; G6 then requires exact Human implementation/version/policy/profile choice
and finite per-RUN production opt-in. L4 VP-01/VP-06 real measurements and full
VP-07 conformance remain mandatory unproved postactivation commitments under
[KA-01 v1.1](AIOS-KERNEL-KA01-PROSPECTIVE-AMENDMENT.md).

Only the two existing source/coverage modules, this document and its new fixture
file change. Normal V1/V2 Runtime, Reviewer and Publisher behavior remains with
its existing owners. Runtime owns canonical verification/EVIDENCE; Reviewer
owns semantic judgment; Publisher owns publication; Human owns selection and
consent. This slice advances no lifecycle, roadmap, downstream pin or activation.
