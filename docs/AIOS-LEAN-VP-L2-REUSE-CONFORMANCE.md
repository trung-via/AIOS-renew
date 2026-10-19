# Lean VP L2-03: read-only reuse eligibility and correction conformance

This is the explicit, nondefault L2-03 observation seam. It supplies one
per-obligation eligibility observation for future L3 consumption. It does not
discharge an obligation, assert target acceptance PASS, execute verification,
schedule work, grant a retry, or activate KA-01, G3 or G6.

## API and immutable identities

`verification.observe_proof_reuse(repository, expected=ProofReuseRequest(...))`
is the only observation entry point. The frozen, version 1 request contains:

- An exact typed L2-02 `ProofApplicabilityRequest`: original TASK/revision,
  RUN/candidate/tree/artifacts/EVIDENCE/subject/command/raw exit; current target
  TASK/revision/RUN/base/candidate/tree/admission digest/artifacts and optional
  original condition-evidence locator; expected main; reviewed contract and
  mapping pins; obligation revision/digest and exact acceptance IDs.
- `original`, an `ApplicabilitySourceSnapshot` pin binding that same source to
  its entire original provenance digest, original evidence digest, raw digest
  and size, and actual original outcome. These are expected identities, not
  caller authentication or assertions of validity.
- The exact `proof_id` and SHA-256 of the normalized reviewed proof descriptor,
  including its claims, conditions and provenance, under the selected mapping.
- `operation`, exactly PRIMARY, REPAIR or REMEDIATION, and an optional typed
  `ReuseCorrectionLocator`. PRIMARY forbids a correction locator.

Correction locators retain exact expected predecessor RUN/subject/artifacts,
terminal FAILURE or REVIEW commit, authorization commit/action, scope and
original/affected verification digests, selected finding IDs and DELTA-review
commit where applicable. They do not authenticate any correction facts.

`format_reuse_request` / `parse_reuse_request` and `format_reuse` / `parse_reuse`
are pure structural codecs for `AIOS_L2_REUSE_REQUEST` and `AIOS_L2_REUSE`, version
1. Only their canonical sorted compact ASCII JSON encoding is accepted. Closed
fields, exact integer types, lowercase digest identities, ordered unique bounded
acceptance/finding populations, duplicate-key rejection, nesting/content limits
and the inherited 2 MiB total byte bound reject alternate or malformed encodings.
Extra result fields and altered computed projections are rejected. Normalized
reviewed proof/obligation/mapping-entry content remains inspectable in the result.
Parsing a serialized REUSE_ELIGIBLE or VALID never authenticates it; neither
witness type is accepted as an observer input.

## Independent authority and currentness

L2-03 freshly calls the unchanged L2-02 observer. That observer independently
reads L2-01 original Runtime records/raw, canonical Git source and target refs,
current committed HEAD, admitted ACTIVE target RUN, raw ancestry and immutable
TASK; it authenticates the exact Human-selected mapping review, original Issue
and carrier policy, canonical RESULT/EVIDENCE, independent full PRIMARY REVIEW
PASS, reviewed condition document and current main. Caller labels, parsed
witnesses, matching hashes alone and historical selection cannot replace these
readers. Missing/conflicting/stale authority blocks.

L2-03 then independently rereads the exact authenticated semantic blobs through
the bounded local-object adapter and the existing proof-mapping validator. The
selected obligation must be a blocking candidate obligation, with exactly its
authorized proof, claim, conditions, revision and acceptance. The original and
target must share the same TASK/revision/base and exact TASK blob/content digest;
this preserves immutable acceptance, constraints, original verification and
modification scope. Raw immutable source/target tree comparisons also require
every original and target delta from that base to stay within the original
exact modify paths. File modes and unsafe file/directory substitutions are
checked; missing objects or unsupported scope observations block.

L2-03 also checks the original source's full correction/recovery ref namespace,
including repair supersession and remediation selectors, before and after its
extra reads. Such competing source authority cannot be hidden by PRIMARY pins.
After its extra mapping and scope reads, L2-03 performs another complete fresh
L2-02 observation and requires exact equality. A moved source, raw, admission,
main, selected review, trust root, target ref or HEAD cannot inherit the earlier
observation. No fetching or mutable capture repair occurs. This is a bounded
current observation, not an atomic distributed transaction, durable lease or
future permission.

The L2 adapter retains its existing 4,096-commit ancestry, 16-parent,
8,192-object, 32 MiB aggregate-object and per-object/output/time bounds. L2-03's
additional adapter has the same finite limits; its tree scope walk additionally
allows at most 8,192 entries and 32 directory levels. There are two explicit
applicability reads, no open-ended polling, retry or authority discovery.

## Satisfaction and conservative states

The result separates original outcome, complete/unstable observations, source
satisfaction, applicability, operation authorization and final outcome. Original
RUN/subject/EVIDENCE/raw identity is preserved, including FAIL and ERROR. A
bounded snapshot preserves these known identities if the full projection exceeds
its output limit; such a result is blocked.

| Outcome | Required observation | Meaning |
| --- | --- | --- |
| REUSE_ELIGIBLE | Fresh authenticated PRIMARY authority; VALID applicability; exact reviewed mapping; satisfactory original proof | Only this exact approved obligation is eligible for future L3 consideration. |
| INVALIDATED | Fresh authenticated PRIMARY/mapping authority and a proved relevant CHANGED condition | Reports only the requested reviewed mandatory proof/obligation and changed dimensions as affected/outstanding. New execution still requires separate authorization. |
| BLOCKED | UNKNOWN, disputed/missing/stale authority, unsupported correction lineage, or unsatisfactory/unestablished source proof | No acceptance PASS, execution permission, fallback, retry or continuation. |

An authenticated source FAIL or ERROR is UNSATISFACTORY even if applicability is
VALID. Exit zero alone is insufficient. Satisfactory proof requires original
PASS, actual complete=true and unstable=false, matching retained original raw
digest, a V2 VERIFICATION schema, observed non-collection execution and empty
recorded observer errors. Its exact collection must equal its original report
population and the reviewed population size. Every item must have exactly one
passing setup, call and teardown report; skips, collection-only results, empty
populations, missing phases, partial collections and diagnostics cannot qualify.
These actual semantic facts cannot be waived by reviewed INAPPLICABLE labels:
population, collection, phases, completeness, stability and schema must have
affirmative EQUIVALENT witnesses. Historical V1 missing facts stay unestablished.

All L2-02 condition dimensions survive unchanged: implementation/tests,
fixtures/setup/teardown/helpers, generated/shared state, interpreter/toolchain,
observer/environment/configuration, policy/profile, worker topology/state,
concurrency/ordering/repetition/collection, completeness/stability/phase outcomes,
integration/external boundaries, evidence schema and diagnostics. Relevant
changes invalidate; missingness blocks; only independently reviewed exact
inapplicability can excuse the other unsupported dimensions. Distinct proof
conditions or obligations are never erased by a narrower green result.

Every outcome says `target_execution=NOT_ASSERTED`,
`target_acceptance=NOT_ASSERTED`, `execution_permission=NOT_AUTHORIZED`, and
`consumer_gate=L3_REAUTHENTICATION_REQUIRED`. An outstanding-obligation list is
an observation, not a queue. An original satisfactory proof may coexist with
INVALIDATED target conditions; it cannot then yield REUSE_ELIGIBLE.

## REPAIR and REMEDIATION conformance boundary

Both correction operations always return NOT_ESTABLISHED/BLOCKED in this
version, including exact-looking locator pins and a fully authentic PRIMARY
applicability observation. The existing L2 target adapter supports only the
primary-shaped admission. Existing correction-currentness readers fetch objects
and do not supply the complete read-only native L2 positive correction binding.
L2-03 does not call those fetching readers, weaken the adapter, fabricate a
positive correction witness, or substitute a caller operation/action label.

A future positive REPAIR observation must independently authenticate the exact
terminal predecessor failed RUN/FAILURE and uniquely current AUTHOR_REPAIR
authorization/action, original immutable TASK, scope and original plus affected
verification. Stale/superseded/missing authorizations, action switching and
terminal-RUN reopening must fail closed.

A future positive REMEDIATION observation must independently authenticate exact
CHANGES_REQUIRED reviewed source, outstanding selected finding or explicitly
authorized causal group, current AUTHOR_REMEDIATION authorization/action/scope,
affected verification and DELTA-review continuity. Unrelated, closed or new
findings and guessed grouping cannot inherit authority. No native positive path
is claimed for either correction operation here; this conservative boundary is
part of L2-03 conformance, not permission for another admission or execution.

## Conformance definitions and future consumers

`tests/test_lean_vp_l2_reuse_conformance.py` builds on the isolated real-Git and
canonical-shaped original Runtime/L2-02 fixtures. Only the GitHub HTTP transport
is replayed. Production authenticators are exercised for the positive PRIMARY
path and negative raw/evidence/source, semantic mapping, currentness, actor,
ref-conflict, phase/population and condition cases. Correction negatives retain
exact-looking, missing, stale, action-switched, unrelated/closed/grouped-finding
and changed-verification pins and require native-lineage blocking. An adversarial
boundary move invokes the actual mapping reader before moving main. No
affirmative mock supplies authentication, VALID or eligibility. Runtime owns
canonical execution of these definitions and creation of verification EVIDENCE.

L3 still needs its independently authorized consumer, current admission and
operation-specific authorization, exact candidate/mapping/obligation binding,
fresh source/currentness reauthentication, live current-RUN Executor/lease,
finite Human limits and explicit prospective opt-in. A future consumer must
independently enforce all distinguishing mandatory obligations and separately
authorized outstanding execution before any terminal discharge. L2-03 provides
none of scheduling, proof collection, feedback delivery, continuation, Executor
selection, repair authorization, review or publication.

No canonical TASK/RUN/EVIDENCE/RESULT/FAILURE/REVIEW is created or mutated. L2-01,
L2-02, legacy minimum-sufficient-v1/V2 decoding and behavior, the original 16-slot
plan, published KA-01 v1.2, roadmap and downstream pins remain unchanged. No new
canonical artifact kind, trust root, router or scheduler is introduced. KA-01
remains NOT_ACTIVATED; G3/G6 activation, L3 engineering and later L4 measurements
retain their independent gates.
