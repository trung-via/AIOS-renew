# Lean VP L2-01: original evidence identity and provenance

Status: bounded implementation of the original-source observation seam. This
document is the single L2-01 contract. Runtime owns canonical verification and
EVIDENCE; the test definitions below assert no verification execution or result.
L1 selection/readiness and this observation do not establish production proof,
semantic mapping adequacy, or KA-01 eligibility or activation.

## Explicit call and authority

`verification.observe_original_evidence(repository, expected=OriginalEvidenceRequest(...))`
reads exactly one original candidate observation. Expected TASK/revision, RUN,
candidate/tree, artifacts commit, EVIDENCE id, original subject, command and exit
status are pins to check, never authentication supplied by the caller. There is
no raw-path, state-root, trust-root, context, resolver, runner, target, consent,
Executor/model or permission argument. Ordinary V1/V2 execution does not call it.

The reader derives the current branch's configured canonical upstream using the
existing transport convention, observes its exact `aios/review/<RUN>` and
`aios/artifacts/<RUN>` refs, and rejects missing/stale/competing failure/repair
refs. It requires the pinned objects already present locally. It does not fetch.
The canonical configuration and exact refs are reread before returning; a change
blocks. This is a source-ref snapshot, not a claim about future currentness or
Publisher/Reviewer approval. Detached/unconfigured readers fail closed.

Git commit/tree/blob bytes are bounded and their SHA-1 Git object identities
recomputed from `type + space + byte length + NUL + content`. Replace refs are
disabled; base ancestry is walked from immutable commit parent headers (at most
4,096 commits, 16 parents per commit). The exact TASK blob comes from the source
candidate and must match the admitted base's TASK blob. The original RUN and RESULT come from the canonical root artifacts
commit and are compared to the exact local Runtime records using existing
`public_run`/`public_result` redaction semantics or their unredacted historical
form. This adds no new interpretation to public EVIDENCE or ResultPackage.

Public transport replaces the private EVIDENCE summary with its standard exit
code summary. A private-summary-only change therefore leaves the public RESULT
blob and artifacts commit unchanged. Local RESULT/EVIDENCE digests describe the
exact private snapshot read now; they do not independently authenticate redacted
summary text as unchanged historical content. That historical property remains
UNKNOWN without a separate recorded commitment. Stale-artifact and unpublished
local-conflict regressions use the publicly committed command while retaining
the exact expected artifact pins, rather than treating private summary text as
an independent public identity.

The private source is `<git-dir>/aios/{runs,results}/<RUN>.json`. Raw capture is
only `<git-dir>/aios/verification/<RUN>/<EVIDENCE>.raw` for V1, or the existing
`candidate/` subdirectory for a TASK explicitly recording V2. A copied path,
even with identical bytes/digest/labels, is rejected. A protected public raw
reference is unavailable; it is never opened or reconstructed. Symlinks,
junctions/reparse points, traversal, hardlinks, nonregular files, changed reads,
oversized files and competing local FAILURE records block. Each private record
and raw capture is reread before return. Local snapshots compare file identity,
size and modification time across path and handle reads, and compare `ctime`
within each API: Windows path/handle APIs can expose different creation/change
clocks under that name. A change in either clock still blocks; an unchanged
file is not rejected merely because those clocks differ. The raw envelope must
retain the existing `STDOUT\n...\nSTDERR\n...` shape. SHA-256 covers all original raw bytes.
V2's recorded original raw hash must match; reused/early-probe records cannot
impersonate an original execution. No cache glob supplies source authority.
Recorded V2 observer/selected-policy and permitted repository-tool blob
identities are checked against the actual original candidate tree, including
recorded absence; arbitrary context paths or matching-looking labels block.

A missing original EVIDENCE identity is not invented from a FAILURE command or
filename. FAILURE-only terminals, remote-only/public-only captures, missing
objects, unsupported lineages and unestablished source facts return
`NOT_ESTABLISHED`, `BLOCK`, no provenance, and a bounded reason. A V1 capture
without an earlier digest can authenticate its exact Runtime-controlled local
bytes now; its independently recorded prior raw digest remains UNKNOWN. This
does not establish historical tamper resistance against a compromised Runtime
or canonical upstream. These are the existing trust boundaries, not caller
replaceable authorities.

## Immutable consumer representation

`OriginalProofProvenance` and its request/dimensions are frozen, slotted types.
Nested observed values are immutable canonical JSON strings. The pure explicit
`original_proof_data` projection returns a detached copy;
`format_original_proof` returns canonical bytes;
`parse_original_proof` strictly decodes structure and grants no authentication.
A consumer must obtain provenance from the Runtime observation, rather than
treat a caller's structurally valid JSON or a recomputed hash as authority.

Format `AIOS_L2_ORIGINAL_PROOF`, integer version 1, has a closed schema. It keeps
the exact expected-and-verified identities, base SHA, TASK blob identity and
byte digest, canonical/local RUN and RESULT byte digests, exact local EVIDENCE
canonical-content digest, original raw digest/size, original command outcome,
sorted exact source refs, and SHA-256 of the exact observed configured upstream
URL (without exposing credentials or private paths). No target is acquired. Original exit zero is
command PASS, one is FAIL, other statuses are ERROR. An incomplete/unstable V2
exit-zero observation is UNKNOWN. FAIL/ERROR cannot be promoted by provenance;
completeness and stability remain separate source facts. A source command PASS
does not mean target proof PASS or satisfactory acceptance coverage.

JSON uses ASCII UTF-8, sorted keys, `ensure_ascii=True`, separators `,`/`:`, no
NaN/floats, no trailing newline. Digests use lowercase hex. Projection/EVIDENCE
content hashes are SHA-256 of those exact canonical bytes; capture/artifact
hashes cover exact original bytes, including whitespace. Structural decoding
rejects duplicate/extra keys, bool-as-int, wrong versions, malformed identities,
dimensions, sources, reasons, digests and outcome promotions. Limits are 2 MiB
per record/projection, 16 MiB raw, 24 JSON levels, 200,000 visited values,
65,536 characters per content string, 8,192 per command, 256 EVIDENCE items,
and bounded Git output/time. Exceeding a bound blocks; nothing is clipped into
proof. Serialization order for dimensions is the following fixed order.

## Dimension sources and missingness

Every dimension has its name, `OBSERVED` or `UNKNOWN`, exact JSON value (null for
UNKNOWN), source and reason. OBSERVED means `RECORDED_SOURCE_CONTENT` at the
listed exact source, not a completeness/preservation assertion. UNKNOWN uses
`SOURCE_RECORDS` / `NOT_OBSERVED`. No V1 omissions are filled by current machine
inventory, task parser defaults, command syntax, filenames, unchanged labels or
assumed serial execution. Incomplete V2 planned profile/toolchain bindings are
not treated as actual process observations.

| Dimension | Authenticated recorded source, or UNKNOWN semantics |
| --- | --- |
| verification_policy | Exact explicitly present TASK `verification.policy`; absent V1 policy stays UNKNOWN even though legacy execution has a default. |
| execution_profile | Complete/stable V2 `candidate.profile`, preserving only its recorded content. |
| toolchain | Complete/stable V2 `candidate.toolchain`, or explicitly recorded `candidate.observed_toolchain` diagnostics when incomplete; no current inventory lookup. |
| interpreter | The three recorded Python implementation/version/executable fields together; missing any means UNKNOWN. |
| environment_digest | Recorded V2 profile `environment_digest`; this binds Runtime's filtered environment, excluding temp/observer variables. |
| environment_content | UNKNOWN: original environment content and excluded variables were not retained. A digest cannot reconstruct them. |
| configuration | Recorded V2 profile `repository_tool_blobs`, including exact recorded missing entries; this is a tool/config snapshot, not a complete dependency footprint. |
| observer | Recorded V2 profile `observer_blob`. |
| worker_topology | Recorded V2 profile workers/distribution/restart/collect-only fields together; partial controls mean UNKNOWN. |
| worker_state | Recorded V2 `candidate.execution`, including controller/worker collections and isolation facts when retained; no inference about unrecorded shared state. |
| collection | Exact V2 `candidate.collection`, including its identity/nodeids when retained; never inferred from command paths. |
| population | Exact sorted unique recorded report nodeids; partial reports remain partial and completeness stays separate. |
| phase_outcomes | Exact V2 `candidate.reports`, preserving failures, phases and recorded conditions. |
| completeness | Exact recorded V2 `candidate.complete`, including false. |
| stability | Exact recorded V2 `candidate.unstable`, including true. |
| test_dependencies | UNKNOWN: no authenticated exhaustive dependency footprint is retained. Candidate tree identity alone does not supply one. |
| fixtures_helpers_shared_state | UNKNOWN: no complete fixture/helper/generated or cross-test state footprint. |
| concurrency_interference | UNKNOWN: worker controls do not prove absence of interference. |
| ordering | UNKNOWN: sorted reports do not record execution ordering. |
| repetition | UNKNOWN: no approved repetition/repeatability observation is retained. |
| integration_external_boundaries | UNKNOWN: names/commands do not establish unit/whole-suite/integration adequacy or external boundaries. |
| evidence_kind_schema | Exact observed `type`, existing V1/V2 record shape and top-level key population; no new EVIDENCE schema/version is issued. |
| prior_raw_digest | Exact recorded V2 `raw_digest`, checked against the original capture; absent V1 digest stays UNKNOWN. The newly recomputed `raw_sha256` is separate. |
| condition_diagnostics | Exact retained V2 candidate errors, observed-toolchain conflicts and observation-source locators when present. Locators are content only and are never opened; they grant no comparison validity. |

## L2-02 requirements and false positives

L2-02 must independently authenticate this original source observation and its
currentness, exact target RUN/candidate/tree and authorized lineage, obligation
and acceptance identities, contract/mapping pins and reviewed semantic adequacy.
It must establish condition-specific witnesses covering every relevant code,
test/population, fixture/helper/shared/generated state, interpreter/toolchain/
observer, environment/configuration, admitted policy/profile, worker mode/count/
state, concurrency/interference, ordering/repetition, collection/completeness,
integration/external and evidence-kind/schema dimension in KA-01 C.5. Every
relevant dimension needs authenticated affirmative coverage or independently
established inapplicability. UNKNOWN cannot mean preserved, VALID, satisfactory,
or acceptance discharged. Original FAIL remains FAIL even with applicability.
Original subject/RUN/raw digests must survive unchanged in any later witness.

Threats include caller-supplied matching labels/digests, copied or redacted raw
paths, stale/moving/competing refs, replacement/graft lineage, replayed RUN or
swapped EVIDENCE/command/subject, changed/missing captures, incomplete context,
selective green reports, hash-only trust, current-host backfill, assumed serial
workers, command/nodeid or filename equality, disjoint paths, and treating
tool-blob or environment hashes as complete semantic footprints. Structural
serialization alone also cannot prove origin. None is an applicability witness.

Focused regressions in `tests/test_lean_vp_l2_original_evidence.py` define real Git
fixtures and original local raw bytes for positive observations and these
adversarial boundaries, plus the published RUN-383-004 minimal V1 public shape
and existing V2 observation shapes. They preserve the existing parsers and
execution/selection functions. Runtime owns running them and constructing proof.

L2-02 applicability and L2-03 safe reuse/correction remain outstanding, as do L3,
G3 actual eligible core safety, G6 exact prospective opt-in and L4 measurements.
No future proof is asserted executed. No roadmap state changes, evidence
broker/issuer/database, scheduler, verification owner, generic router, lifecycle
artifact, mapping revision, publication or permission/consent token is introduced.
