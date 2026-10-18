# Lean VP L1 canonical Git source binding

TASK-377 adds an explicitly invoked, read-only Git source-consistency bridge
to the non-operative [TASK-376 intake](AIOS-LEAN-VP-L1-SEMANTIC-PROOF-INTAKE.md).
TASK-376 checks caller-supplied pins; this bridge derives those pins from
actual committed TASK, contract and mapping bytes anchored in configured
canonical main. It establishes bounded provenance and currentness for one
observation. It does not establish independent approval or proof validity.
**KA-01 remains NOT_ACTIVATED; the roadmap NEXT remains Lean VP.**

## Exact explicit interface

```python
bind_canonical_semantic_proof_sources(
    repo, declaration_bytes,
    expected_remote_url=canonical_url,
    expected_main_sha=exact_main_sha,
    expected_candidate_sha=exact_candidate_sha,
    sources={
        "task": GitSemanticSource(task_commit, task_tree, task_path, task_blob),
        "contract": GitSemanticSource(contract_commit, contract_tree, contract_path, contract_blob),
        "mapping": GitSemanticSource(mapping_commit, mapping_tree, mapping_path, mapping_blob),
    },
)
```

This API lives only in `review_transport.py`; it has no CLI, automatic caller,
admission hook, scheduler, registry, persistent observation store or publication
side effect. All inputs are mandatory. `declaration_bytes` is strict UTF-8 JSON
for exactly `task-semantic-proof-intake-v1`. `sources` is a plain dictionary
with exactly the three named frozen locators. Each locator carries the exact
40-character lowercase SHA-1 commit, root tree and blob identities, plus an
explicit normalized repository-relative path. SHA-256 Git repositories are
not silently converted to the existing SHA-1-only intake schema.

The trusted caller must independently select the legitimate expected remote
URL, expected main and candidate. Copying that trust root from an untrusted
declaration would defeat the caller boundary. This API authenticates content
relative to the configured remote and its existing Git transport credentials;
it adds no cryptographic commit-signature requirement, trust-root registry or
independent assertion that a configured repository is rightfully governed.
The candidate is an exact external consistency pin, not an asserted verified,
published or main-anchored candidate.

## Source authentication and declaration correspondence

1. Resolve the existing configured branch upstream remote. Detached observation
   uses the existing unanimous-upstream resolver. Missing, local `.` upstream,
   duplicate/conflicting active upstream bindings, absent/ambiguous URLs and a
   different remote URL fail closed. Both raw and effective URLs must exactly
   equal the supplied URL; URL rewriting cannot redirect the root. A remote
   named `origin`, a local branch or a cached commit is insufficient.
2. Use the existing exact remote-ref observer to require exactly the expected
   `refs/heads/main`. Fetch that exact immutable commit with no tags, refmap,
   FETCH_HEAD, automatic maintenance or submodule recursion. Only immutable
   object acquisition is permitted; no refs, index, worktree or Runtime state
   are written. Shallow readers are rejected to avoid shallow-metadata writes.
3. Traverse bounded raw commit parent identities from observed main to all
   declared source commits. Hash the exact commit bytes, reject wrong object
   types and verify each declared root tree. Replacement refs are disabled;
   local grafts cannot manufacture ancestry. A known local SHA outside this
   authenticated history is rejected, even if its files look correct.
4. Authenticate each visited tree's content/object identity, then walk literal
   path components to a regular file blob. Validate exact mode, type, path and
   blob identity; symlinks, gitlinks and traversal through a blob are rejected.
   The selected blob must also remain at that exact path on observed main.
   Thus an unchanged file may use an immutable ancestor source commit, while
   a changed/deleted current-main path cannot reuse stale ancestor content.
   Hash exact blob bytes and reject size/type/UTF-8/content-identity conflicts.
5. Parse the authenticated TASK with the existing canonical TASK parser after
   bounding YAML tokens/nesting and excluding aliases, anchors, explicit tags,
   directives and BOM. Its duplicate-field rejection remains authoritative.
   Derive TASK ID, revision and **every** acceptance ID from those bytes.
   `envelope_digest` is SHA-256 of the exact committed TASK bytes, including
   whitespace and the final newline. Require `.ai/tasks/<derived-task-id>.yaml`.
   TASK content changes therefore invalidate the binding even when ID and
   acceptance names remain unchanged. Existing V1/V2 TASK parsing is reused;
   this wrapper neither invents nor admits a future production TASK format.
6. Strictly decode the separately committed contract and mapping JSON. Derive
   normalized contract and mapping pins with existing VP-02 helpers. Compose
   those actual source documents through `validate_semantic_proof_intake`, then
   validate the incoming declaration with the same source-derived pins and
   independently supplied candidate. The sole mapping/coverage algorithm
   remains VP-02's `validate_proof_mapping`.
7. Re-resolve the configured remote identity and re-observe authoritative main
   after semantic validation. Missing, ambiguous, conflicting or moved main
   fails closed. Return no partial successful observation on failure.

Source SHA/blob fields, content digests and reviewer/provenance labels in an
incoming declaration cannot replace actual Git reads. Coherent cross-TASK,
revision, partial acceptance, candidate, policy, contract or mapping forgeries
must agree with the authenticated sources and all existing semantic checks.
Equivalent unordered declarations normalize through VP-02; array reordering
does not erase fixture, integration, worker/profile, concurrency, ordering,
comparison, collection, environment or evidence conditions. Raw-byte source
changes still produce different source identities and content digests.

## Bounds, result and limits

Intake JSON is at most 4 MiB; TASK bytes at most 256 KiB; each semantic source
and visited tree at most 1 MiB. JSON permits at most 32 nested containers and
65,536 containers/nodes; duplicate keys at any depth, non-finite numbers, BOM,
invalid UTF-8 and lone Unicode surrogates are rejected. TASK scanning permits
at most 65,536 tokens and 32 nested collections. Ancestry permits at most 4,096
commits, each at most 64 KiB with at most 256 parents. Paths contain at most
512 ASCII letters/digits/dot/underscore/hyphen/slash characters and 32
components, with no empty, dot, dot-dot or `.git` component. Remote identity is
at most 2,048 characters. Unsupported or out-of-bound input raises
`ReviewTransportError`, without fallback or migration. Existing VP-02 semantic
population, coverage, identity and revision bounds also apply.

The frozen `CanonicalSemanticSourceBinding` identifies the remote name/URL,
exact main on both observations, ordered TASK/contract/mapping locators,
SHA-256 and byte count of each actual source, and the immutable validated
intake. `readiness` is `SOURCE_CONSISTENT_ONLY`; source authentication and
currentness are `ESTABLISHED_FOR_OBSERVATION`. This is a two-observation
currentness check, not a lock on remote main, a durable receipt, or protection
against a remote changing away and back between observations. Later use needs
its own rightful currentness/applicability boundary.

The wrapper explicitly retains **UNESTABLISHED** independent Human approval,
independent semantic mapping review, semantic adequacy, canonical Runtime
verification, evidence applicability, future version/policy admission and
production opt-in. `production_eligible=False`, `execution_authorized=False`
and `ka01=NOT_ACTIVATED` are fixed descriptions. The nested TASK-376 intake
retains its original structural-only flags; the wrapper supplies only the
additional bounded Git observation. No source-authenticated flag is REVIEW
PASS, a verified proof, source-to-target L2 validity or KA-01 ACTIVE. Bogus but
well-formed review labels can be committed; their existence authenticates
bytes, not an independent reviewer's judgment.

## Remaining authority and engineering

- **Full L1 / VP-01:** independently reviewed adequate live semantic packages,
  lawful prospective version/policy admission and complete readiness remain
  outstanding. The baseline and matched real VP-01 measurements remain
  outstanding/UNKNOWN, never waived or measured by synthetic Git fixtures.
- **L2 / VP-03:** original-subject evidence authentication and complete
  source-to-target applicability witnesses with conservative
  VALID/INVALIDATED/UNKNOWN decisions remain unimplemented by this slice.
- **L3 / VP-04/05:** the one Runtime scheduler, immutable checkpoints, actual
  proof coverage, same-Executor feedback, finite consent/resources, leases,
  replay/crash/stop/terminal safety and no-double-execution still require
  separate admitted implementation.
- **KA-01 G3:** eligible L1-L3 core implementation, independent Runtime safety
  verification, exact-source semantic review and publication remain necessary.
  Git source consistency alone cannot establish G3.
- **KA-01 G6:** exact Human future version/policy/profile choice, per-RUN finite
  opt-in and rollout/rollback authority remain independently gated after lawful
  G1/G2 and actual G3. No roadmap continuation or source locator grants consent.
- **L4 / G4/G5:** KA-01 v1.1 retains VP-01/VP-06 real measurements and full real
  VP-07 conformance as mandatory unproved postactivation commitments.

`tests/test_lean_vp_l1_source_binding.py` defines focused real local Git
fixtures, not live reviewed packages or canonical evidence. It covers exact
immutable sources, semantic reorder stability, strict source decoding,
forged/stale/swapped identities, unexpected remotes, main movement and effect
traps. Existing TASK/proof-contract and historical read-only transport behavior
remain with their existing owners. Runtime owns canonical verification and
EVIDENCE; Reviewer owns semantic judgment; Publisher owns publication; Human
owns operational selection and consent. This slice advances none of those
roles or lifecycle/roadmap states.
