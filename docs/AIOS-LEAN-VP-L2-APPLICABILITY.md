# Lean VP L2-02: exact source-to-target proof applicability

This is the single L2-02 implementation contract. It defines a bounded Runtime
observation, not evidence reuse, satisfactory acceptance, new proof authority,
or KA-01 activation. Runtime owns verification and canonical EVIDENCE. Fixture
definitions and implementation-local sanity checks do not establish production
proof, Reviewer PASS or publication.

## Invocation and immutable representation

`verification.observe_proof_applicability(repository, expected=request)` is an
explicit read-only entrypoint. Ordinary verification, TASK admission, L1,
L2-01, Reviewer and Publisher do not invoke it. `request` must be exactly a
frozen, slotted `ProofApplicabilityRequest`, integer version 1:

- `source`: the unchanged `OriginalEvidenceRequest` selecting exactly one
  original TASK/revision, RUN, candidate/tree, artifacts commit, EVIDENCE,
  original subject, command and exit status.
- `target`: `ApplicabilityTargetRequest`, selecting TASK/revision, admitted RUN,
  base, committed candidate/tree, canonical artifacts commit, exact private RUN
  byte digest, and optional `condition_evidence: OriginalEvidenceRequest`.
  That optional locator must name this same target TASK/revision/RUN/candidate/
  tree/artifacts, not an observation from another RUN or host.
- `expected_main_sha`: the exact current configured canonical main, checked
  independently; an old ancestor is not accepted as current.
- `contract` and `mapping`: immutable id/revision/normalized SHA-256 declaration
  pins, plus `obligation_id`, `obligation_revision`, `obligation_digest` and the
  exact sorted unique `acceptance_ids` for that obligation.

These are expected locators, never caller authority. There is no parsed source,
raw path, state directory, environment, condition value, reviewed flag,
selection ref, provider callback, resolver, trust root, runner, Executor, retry
or consent argument. Wrong input types raise `VerificationContractError`.
Well-typed but unavailable/conflicting inputs produce an immutable witness
with `NOT_ESTABLISHED`, `UNKNOWN`, `BLOCK` and a bounded non-secret reason.

`ProofApplicabilityWitness` retains the entire unchanged original
`OriginalProofProvenance`, exact target provenance, reviewed authority identities
and every ordered condition judgment. Nested values are canonical immutable
JSON strings or frozen typed records/tuples. `applicability_data` and
`applicability_request_data` return detached projections. Explicit pure
`format_applicability[_request]` and `parse_applicability[_request]` format or
decode `AIOS_L2_APPLICABILITY` / `AIOS_L2_APPLICABILITY_REQUEST`, version 1.
Parsing, construction, hashing or copying even a structurally valid VALID
record establishes **no authentication**. The observer accepts only locators
and independently reads the authorities on every invocation.

The schemas are closed, including nested requests, provenance, authority
bindings, facts and dimensions. Extra/missing/duplicate keys, reordered or
missing output dimensions, floats/NaN, bool-as-integer versions/revisions,
unsafe paths, noncanonical embedded JSON, malformed identities, contradictory
comparisons and outcome/authority promotions are rejected. Encoding is sorted
ASCII JSON, `ensure_ascii=True`, separators `,`/`:`, without a trailing newline.
SHA-256 covers these exact bytes; Git object identity is SHA-1 over Git's raw
type/length/NUL/content envelope. The existing L2-01 encoder's 2 MiB projection,
24-level, 200,000-value and string limits apply. Nothing is clipped into proof.
Every successfully read source also has a compact immutable `source_snapshot`
with its exact original identity, full provenance digest, EVIDENCE digest, raw
digest/size and actual outcome. If the full source/target/fact projection cannot
fit, output becomes NOT_ESTABLISHED/UNKNOWN with `APPLICABILITY_PROJECTION_BOUND`,
no full provenance or affirmative dimensions, and this snapshot remains intact.
A known original FAIL/ERROR is never lost or promoted by projection limits.

## Independent source, target and currentness

The source is obtained by calling the unchanged
`observe_original_evidence(expected=request.source)`, rather than consuming a
caller-supplied `OriginalProofProvenance`. That reader binds the exact original
canonical and local Runtime RUN/RESULT, TASK/base/candidate/tree, EVIDENCE item,
outcome, original raw location/size/digest, upstream identity and current source
refs. Public-only/redacted/copy-path sources, missing private captures, unsupported
source lineages, replayed evidence and conflicting references do not authenticate.
Its original per-dimension UNKNOWNs are preserved, including V1 omissions and
incomplete V2 planned conditions. No current host inventory fills them in.

The target is independently observed from the Runtime-owned
`<git-dir>/aios/runs/<RUN>.json`, not from request values alone. Its canonical
root artifacts commit must retain the same admitted RUN using the unchanged
`public_run` semantics or its historical unredacted form. Configured canonical
`aios/review/<RUN>` and `aios/artifacts/<RUN>` refs must exactly match the target;
competing failure/repair, remediation, supersession or publication-recovery refs
and a local FAILURE block. The admitted ACTIVE
RUN's identity, revision, base, workspace and return affinity must match the
actual TASK. The candidate must be the current committed local HEAD with the
exact tree. Raw immutable parent headers must connect it to its admitted base;
the TASK blob must survive unchanged since admission. The target base is also
anchored in current canonical main. Matching trees without actual lineage do
not establish these bindings. The output keeps the canonical RUN byte digest,
TASK blob/content digests and a digest of the sorted observed raw ancestry
records `[commit, tree, parents]`.

Optional target `condition_evidence` is independently read through L2-01, bound
to this same canonical admission and exact local RUN bytes. An unavailable or
conflicting explicitly requested condition observation blocks authentication.
When no locator is supplied, target identity can still be authenticated from
its canonical admitted RUN, but actual process conditions remain UNKNOWN.
Git facts and genuinely reviewed inapplicability do not invent process values.
No condition collection, target execution or fresh proof is requested or run.

The configured branch/upstream is derived using the existing transport
convention. Raw and effective URL must agree; detached/unconfigured readers or
URL rewrites block. Only local immutable objects may be read. No fetch,
FETCH_HEAD, ref/index/worktree/config/policy write, object installation or
maintenance occurs. Git replace refs are disabled, graph convenience commands
are not trusted for ancestry, lazy fetch and optional locks are disabled, and
Git output/time are bounded. The L2-02 reader limits ancestry to 4,096 commits,
16 parents per commit, 8,192 cached objects and 32 MiB aggregate object bytes.
Individual objects use L2-01 bounds; ref output is limited to 32 KiB.

After computing judgments the observer independently reauthenticates Human
selection, rereads the original source observation and any target condition
observation, then rechecks configured upstream, exact main/selection/review and
target refs, local target RUN/FAILURE and committed HEAD. Changes block with
UNKNOWN, even if earlier judgments found divergence or equivalence. Local
record/raw safety and changed-read checks remain L2-01's original checks.
This is a current bounded observation, not an atomic distributed transaction,
future lease or durable permission. Later consumers must reestablish currentness.

## Reviewed semantic authority and the sole condition provider

L2-02 derives the **existing** L1 selection ref from the exact target TASK and
candidate. There is no alternate review selector. It reads the existing
immutable `AIOS_L1_HUMAN_REVIEW_SELECTION` carrier, verifies that its commit adds
only that record to its declared main parent, and uses the unchanged L1 request
and scope validators. The original GitHub Issue repository/actor/original body
and unedited history are independently reauthenticated under the unchanged
canonical carrier policy, twice. An Issue body, copied receipt or caller
`reviewed=True` cannot stand in for that observation.

The actual TASK/contract/mapping/review-TASK blobs must be anchored in current
canonical main with their exact selected bytes surviving. The existing
proof-contract, mapping, semantic-declaration and mapping-review-scope validators
check the independently read documents. Actual declaration pins, target
candidate, obligation revision/digest and acceptance correspondence must match.
The mapping must be a real authorized modification in the selected review RUN.
Its admitted RUN, canonical RESULT/EVIDENCE and full PRIMARY REVIEW PASS are
validated through the existing RUN/artifact/REVIEW validators, with exact
artifacts/decision refs, raw ancestry, authorized scope, changed-file accounting
and publication containment. Decision parent and semantic bytes must agree.
Missing, competing or moved review/publication refs block.

L1's existing higher-level readers fetch objects; L2-02 therefore uses their
unchanged structural validators and existing RESULT/REVIEW contracts with a
bounded local-object read adapter. It does not call those fetching readers,
modify their behavior, select another mapping or issue a review. In particular,
TASK-381's published mapping is authority only for its actual selected subject,
never for another TASK, candidate or obligation. Missing exact authority has no
historical-pin, structural-validity or caller-digest fallback.

The bounded provider supports existing PRIMARY TASK decoding and candidate-kind
obligations. Comparison obligations without complete original base-condition
provenance, or unsupported prospective admission formats, remain UNKNOWN; they
are not adapted into candidate proof or newly admitted by this reader.

There is exactly one supported condition provider. The selected obligation's
existing `conditions.test_ref` must be `l2c:<lowercase SHA-256>`, locating:

`docs/aios-proof-contracts/l2-conditions/<SHA-256>.json`

This is an immutable semantic source document, **not** a new Runtime artifact,
EVIDENCE, attestation issuer, trust service or registry. L2-02 creates no such
document. A future lawful mapping author/reviewer must already have provided
it. Its exact bytes must be an actual authorized change in the independently
selected mapping-review TASK's modify scope and RESULT changed files, surviving
both the decision tree and current main. Merely adding it to a candidate, using
a digest-shaped label, or changing an old reviewed document is insufficient.

The document has these exact top-level keys:

`format`, `version`, `source_proof_sha256`, `target`, `obligation_id`,
`obligation_revision`, `acceptance_ids`, `claim`, `conditions`,
`review_acceptance_id`, `dimensions`.

Format is `AIOS_L2_REVIEWED_CONDITIONS`, integer version 1. It binds the entire
authenticated original projection digest, the full exact target locator, the
obligation's semantic claim/id/revision/acceptance and all existing proof
conditions except `test_ref` itself. Excluding that content-address locator
avoids a hash cycle; the outer exact obligation/contract/mapping pins bind it.
The review acceptance must be the existing scope correspondence that covers
this obligation. A bundle for a different source, raw digest, target RUN,
candidate, claim, condition or acceptance cannot authenticate this comparison.

Each ordered dimension declaration has exactly `name`, `provider`, `reason`,
`paths`. Only these providers are understood:

| Provider | Observation and authority |
| --- | --- |
| `GIT_FOOTPRINT` | Permitted only for the six static dependency dimensions below. The independently reviewed reason declares an exhaustive relevant closure, including the absence of outside process/state effects. A nonempty sorted unique list of at most 64 normalized regular Git file paths is read independently at source and target, retaining path, executable mode, blob identity and content SHA-256. Equal reviewed full footprints can be EQUIVALENT. A proved target change/deletion is CHANGED. Missing source content, symlinks, submodules, unsupported closure or unavailable objects cannot establish equivalence. A path list without its independent exact semantic review is never sufficient. |
| `L2_ORIGINAL` | Reads this exact named dimension from independently obtained source and target L2-01 records. Recorded actual values must both be present; equality is EQUIVALENT and divergence CHANGED. Missing dimensions remain UNKNOWN. No parser defaults, current machine inventory, planned incomplete profile or digest-derived content is supplied. Equivalent population, worker topology and evidence kind/schema must also agree with the exact reviewed population size, worker mode/count, non-collection execution and kind/schema requirements; contradictions block. |
| `INAPPLICABLE` | An explicit reason in this exact independently reviewed semantic document declares the named dimension irrelevant to this exact obligation for both source and target. Output includes the reviewed authority and declaration identity. This never excuses missing source/target identity, currentness or mapping authority, nor erases any original recorded value/outcome. A caller declaration, empty fixture list, serial label or omitted dimension does not establish it. |
| `UNAVAILABLE` or omitted entry | Explicit UNKNOWN. Absence is not inapplicability or preservation. An unsupported provider is NOT_ESTABLISHED/UNKNOWN, without discovery or secondary collection. |

No arbitrary expected condition values are accepted in the document. Static
file witnesses cannot represent process-generated/cross-test state, external
services or live interference. Such relevant dimensions need retained actual
Runtime observations; if the supported reader cannot establish them, UNKNOWN
is mandatory. Review of a footprint is an existing independent semantic
judgment, not a new automated dependency oracle or adequacy validator.

## All C.5 dimensions, in serialization order

Every output dimension contains `name`, `status`, source and target facts,
`reason`, and the exact authority digest. Facts retain `status`, `origin`,
`identity_sha256`, `value_json`. An EQUIVALENT/CHANGED judgment requires two
explicit OBSERVED facts; its values must actually agree/differ. INAPPLICABLE
requires both facts to reference reviewed semantics. UNKNOWN facts contain no
asserted content or evidence identity; an observed side is retained when only
the other side is missing. The full source/target provenance and authority
bindings make the fact identities inspection-readable and reproducible.

| Dimension | Actual supported witness; UNKNOWN/inapplicability boundary |
| --- | --- |
| implementation_dependencies | Reviewed exhaustive static subject/code closure and independent Git reads. An implementation SHA alone does not describe dependencies. |
| test_dependencies | Reviewed complete static test dependency closure. Command paths and nodeids alone are insufficient. |
| population | Original recorded report-node population on both sides, also checked against the reviewed size. Partial/absent population is not assumed preserved. |
| fixtures_setup_teardown | Reviewed complete static fixture/setup/teardown closure. Any relevant unrecorded dynamic fixture effects stay UNKNOWN. |
| helpers | Reviewed complete static helper closure, including shared helpers. Filename/disjoint-diff similarity supplies no closure. |
| shared_state | Reviewed exhaustive Git-only state closure when semantically sufficient. Unretained cross-test/process state stays UNKNOWN. |
| generated_state | Reviewed immutable Git state only when it fully represents the required generated state. Unrecorded generated/runtime state stays UNKNOWN. |
| interpreter | L2-01's complete recorded Python implementation/version/executable tuple. Missing fields cannot be backfilled. |
| toolchain | Original recorded toolchain, including retained actual diagnostics on incomplete observations. Planned labels do not supply missing actual conditions. |
| observer | Recorded original observer blob, independently checked by L2-01 against each actual subject tree. |
| environment_digest | Exact original filtered environment digests. Equality does not cover absent original content or excluded variables. |
| environment_content | Historical V1/V2 content is unretained: UNKNOWN unless exact independent semantic review establishes irrelevance. A digest cannot reconstruct it. |
| configuration | Each original recorded repository-tool configuration identity, authenticated against its own tree. This is not an inferred general configuration footprint. |
| verification_policy | Explicit recorded TASK policy; absent historical V1 declaration stays UNKNOWN. A changed admission/TASK cannot inherit old authority. |
| execution_profile | Complete/stable original actual profile. Incomplete planned bindings stay UNKNOWN. |
| worker_topology | Recorded mode/count/distribution/restart/collect-only tuple, with reviewed mode/count consistency. A serial declaration cannot prove worker-state independence. |
| worker_state | Actual retained execution state. Unknown isolation/shared-state properties are not filled from worker count. |
| concurrency_interference | Unretained original interference/whole-topology effects stay UNKNOWN unless independently reviewed irrelevant. Worker labels do not establish absence of interference. |
| ordering | Unrecorded execution order stays UNKNOWN; sorted reports do not prove it. Requires exact reviewed irrelevance when no actual observation exists. |
| repetition | Unrecorded repeatability/repetition conditions stay UNKNOWN. Distinct repetition purposes remain separate obligations, not renamed equivalent work. |
| collection | Exact recorded collection identities and populations on each side. No collection command is run. |
| completeness | Recorded complete flag, including false; absent historical completeness stays UNKNOWN. This observation does not declare incomplete proof satisfactory. |
| stability | Recorded unstable flag, including true; missing stability remains UNKNOWN. No stability is inferred from an exit code. |
| phase_outcomes | Original exact retained phase/item outcomes, preserving failures and skips. Command/node equality alone is insufficient; semantic sufficiency remains the exact reviewed requirement. |
| integration_external_boundaries | Unretained unit/whole-suite/integration/external conditions stay UNKNOWN unless exact independent semantic review establishes inapplicability. Names cannot establish scope. |
| evidence_kind_schema | Actual original VERIFICATION type, V1/V2 shape and exact key population on both sides; equivalent values must match the reviewed kind/schema. No new EVIDENCE kind/schema is issued. |
| condition_diagnostics | Original retained errors/conflicts/observation behavior, or UNKNOWN. Embedded locators are content only and never opened for extra proof. |

Original prior raw digest, exact raw identity/size and original outcome are
preserved in the source provenance itself; they are mandatory identity bindings,
not dimensions that inapplicability can bypass. Historical missing prior digest
stays UNKNOWN even though L2-01 authenticates current exact local raw bytes.

## Deterministic reduction and outcome separation

Precedence is fixed:

1. Missing/conflicting/stale source, target, admission, lineage, currentness,
   selected mapping, review, condition-document authority or supported schema
   produces `authentication=NOT_ESTABLISHED`, **UNKNOWN**, `BLOCK`. Earlier
   changed/equivalent facts cannot override disputed authority.
2. With authenticated exact identities/authority/currentness, any proved relevant
   CHANGED dimension yields **INVALIDATED**, even when unrelated dimensions are
   UNKNOWN. It is not a claim that all other provenance is complete.
3. Only complete affirmative coverage, with every dimension EQUIVALENT or
   independently reviewed INAPPLICABLE, yields **VALID**. Otherwise **UNKNOWN**.

`AUTHENTICATED` describes exact identity/authority/currentness, not automatic
completeness of every condition. `source_outcome` always copies the original
PASS/FAIL/ERROR/UNKNOWN. VALID source FAIL remains FAIL; VALID source ERROR
remains ERROR. An incomplete/unstable exit-zero V2 source retains UNKNOWN.
Original command PASS is not target acceptance PASS. Even a VALID observation
always reports `satisfactory=false`, `target_execution=NOT_ASSERTED`,
`reuse=NOT_AUTHORIZED`; it discharges no obligation. The original source RUN,
subject, EVIDENCE and raw digests are never rewritten as target execution.

UNKNOWN/INVALIDATED return `BLOCK`; VALID returns `APPLICABILITY_ONLY`. These
are read dispositions, not lifecycle transitions or permissions. Missingness
does not authorize fresh proof, retry, continuation or automatic fallback.

## L2-03 consumer preconditions and threats

A later L2-03 consumer must independently have approved reuse semantics and
Runtime authority; obtain a fresh Runtime observation, not a parsed/cached
witness presented as authority; bind its exact admitted target/candidate/tree/
lineage, obligation/acceptance, contract/mapping and original source identities;
and establish currentness again at its own decision boundary. It must require
VALID plus genuinely satisfactory original outcome, completeness, stability and
all distinguishing conditions required by that exact reviewed obligation.
FAIL, ERROR, UNKNOWN, missing provenance or disputed authority cannot discharge
acceptance. It must preserve source execution identity rather than claim tests
ran on the target. No L2-03 consumer or discharge implementation is supplied here.

INVALIDATED or UNKNOWN does not by itself authorize a new proof. Any eventual
fresh execution needs separately existing, reviewed, already authorized finite
Runtime scope/resources; absent that, STOP/BLOCK. This reader cannot select
coverage/mapping, collect missing conditions, resolve by retry, request expanded
Human approval, select an Executor or dispatch L2-03/L3.

False-positive threats include copied provenance or raw paths; replayed RUN/
EVIDENCE/target SHA; matching-looking digests; equal trees without ancestry;
stale main/refs or racy private captures; unreviewed/cross-TASK semantic selectors;
altered contract/mapping/condition documents; copied Human body without original
Issue authentication; partial REVIEW PASS; fraudulent inapplicability;
incomplete dependency lists; unrecorded generated/shared/external state; assumed
serial isolation; same commands/nodeids/names; disjoint diffs; filtered environment
hashes; planned incomplete V2 bindings; ignored phase outcomes; and confusing
VALID applicability with successful source proof or target execution. The
observer never promotes any of these structural facts alone into VALID.

`tests/test_lean_vp_l2_applicability.py` defines isolated real immutable Git,
canonical transport and private original Runtime-layout fixtures with a replayed
HTTP transport only. Production authentication/validators are not replaced by
affirmative mocks. Definitions cover full authentication/VALID, changed static
and Runtime condition families, every omitted dimension, unsupported dynamic
conditions, altered authority and target lineage, spoofed inapplicability,
source FAIL/ERROR, V1/incomplete V2, schema changes and mutable-root races.
Runtime owns executing them and creating canonical EVIDENCE; their synthetic
subjects are never production PASS.

Existing L1, L2-01, legacy minimum-sufficient-v1 execution, available historical
V2 interpretation, public EVIDENCE/ResultPackage and independent Reviewer/
Publisher contracts remain unchanged. No new lifecycle artifact, DB, broker,
router, scheduler, publication, policy activation, retrospective rewrite,
roadmap advancement or downstream adoption occurs. L2-03, L3, G3 actual core
safety, G6 exact prospective opt-in and L4 measurements remain outstanding.
Measured benefit/speedup remains UNKNOWN; KA-01 remains NOT_ACTIVATED.
