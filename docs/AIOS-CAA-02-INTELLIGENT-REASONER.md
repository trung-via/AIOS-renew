# CAA-02: intelligent evidence retrieval and advisory reasoner

TASK-410 r1 implements one optional, transient consultation API. The Human's
2026-10-23 selection is prospective planning. CAA-01's exact independently
reviewed source is `4c5a961e17315aa6bdbea810e0fed803ef460e05`; its authenticated
RESULT/review/inclusion bindings are recorded in the CAA roadmap. This package
does not declare TASK-410 reviewed, published, activated or production-proven.

## Architecture decision

The AOA-1 v1 alternatives for this Brain-owned architectural decision are:

| Option | Benefit and limitation | Decision |
| --- | --- | --- |
| A: current Brain reads, no build | No additional invocation cost; Brain manually reads canonical facts. Cross-episode comparison remains manual and unmeasured. | Remains a lawful direct-authoring path. A1 supplies a fixed structured/lexical comparator. |
| B: transient grounded CAA-02 | Reuses the sealed reader, adds safe semantic features and at most one model-assisted mechanism comparison. Fixed budgets and independent citation checks constrain uncertainty. Cost and useful production effect are unmeasured. | Selected bounded implementation candidate, without a superiority claim. |
| C: persistent DB, multi-agent or trained model | Additional ingestion, privacy, lineage and operating cost; no measured residual problem establishes the need. | Outside this TASK. CAA-05 remains conditional and unselected. |

B1 composes conditional dependency hypotheses from source-specific relations;
the eight fixed advice cards belong only to the A1 lexical comparator. B1 has
no mechanism-name selection or inherited card prose. Its variable alignment,
proposed missing dependency, expected effect and falsifier depend on the exact
source and candidate declarations. It can propose zero to three hypotheses or
abstain. Arbitrary prose understanding and actual technical causes remain
UNKNOWN; model output cannot supply engineering facts or a lifecycle verdict.

## Callable boundary

`aios_renew.caa_reasoner.consult(repo, *, cut, candidate,
expected_candidate_fingerprint, mode="B1", provider=None)` returns a finite
JSON-compatible `AIOS_CAA_ADVISORY` dictionary. It does not write files, create
TASK/RUN records, invoke tools, choose CODE_FIX or an Executor, dispatch, verify,
review, publish, accept Human risk, or alter scope or policy.

`cut` must be an in-process `ExperienceCut` issued by
`capture_experience_cut`. Exported dictionaries, issuer labels, summaries, model
text and Git messages cannot authenticate it. Source access is exclusively the
existing CAA-01 reader and canonical validators; there is no second Git reader,
new store, implicit fetch, repository scan or Runtime-log fallback.

The caller-owned candidate is an exact transient dictionary with these fields:

```json
{
  "goal": "Authenticate source identity before consumption.",
  "invariants": ["Keep historical snapshots isolated."],
  "acceptance": [],
  "scope": {"inspect": [], "modify": ["src/example.py"]},
  "consumers": [],
  "verification": []
}
```

`candidate_fingerprint(candidate)` supplies the expected exact draft digest.
This binds all candidate fields, including excluded text, without authenticating
Human intent or granting authorization. Changed drafts require a new explicit
call. Substitution before or during consultation raises `CandidateContextError`.
The API is an independent helper with no four-flow hooks or authoring defaults.

`mode="A1"` chooses the deterministic structured/outcome plus exact-feature
retrieval comparator. `mode="B1"` chooses one explicitly supplied existing
`MappingBrainProvider` or `JsonBrainProvider`; its callable may use a pretrained
model. There is no built-in live integration, provider/model selection, retry,
failover, model voting, training or automatic A1 fallback. Both transports see
the identical source-bound request and portable response grammar.

## Safe semantics and temporal identity

`reconstruct_episodes(..., include_semantics=True)` is opt-in. The default CAA-01
shape, seal, private handling, selection and budgets remain compatible. The new
projection derives directly from authenticated TASK acceptance, hard constraints,
goal, declared scope, explicit consumer/dependency declarations and verification. The RUN's original immutable
TASK revision supplies its semantics, even if main has a later revision.
Non-goals, assumptions, claims, correction instructions, diagnostic raw text and
provider traces are excluded as semantic witnesses.

The lexical projection exports twelve code-owned concept names: source identity,
temporal isolation, bounded work, data/authority separation, privacy, schema
validation, failure containment, acceptance coverage, verification isolation,
consumer compatibility, direct authoring and correction lineage. Each concept
is an **OBSERVED lexical mention only**, with its exact document commit/blob,
path, JSON pointer, snapshot SHA and cut digest. Negation, implementation,
technical cause and normative policy stay UNKNOWN. These lexical mentions alone
cannot support a B1 causal proposal. The separate relational projection accepts
only whole-field declarations in this deliberately limited language:

```text
For <consumer>, <effect> requires <condition>.
For <consumer>, <effect> is independent of <condition>.
Consumer: <consumer>.
```

Each phrase contains at most six words from the code-owned ordinary-word
allowlist. Dependency atoms have explicit roles (for example acceptance, match,
identity or validity). Direction and independence are retained, with exact
TASK commit/blob/pointer, snapshot and cut bindings. Only a declared requirement
or independence is OBSERVED; implementation and actual cause are UNKNOWN.
Unrecognized wording, negation, identifiers and concepts are not translated or
guessed. Scope paths never identify consumers. Neither projection exports
arbitrary source words, credentials or private paths. Suspicious
fields are excluded whole; they are not cleaned into authoritative statements.
Unsupported meaning and field coverage are explicitly UNKNOWN. The projection
is deliberately lossy; absence of a feature does not prove absence of a concept.

Historical pre-author snapshots use `task_only=True, as_of_sha=...`, or an actual
earlier captured full cut for prior episodes. TASK-only cuts acquire no later
FAILURE, REPAIR, REVIEW or publication, even when future artifacts name the same
head. Full cuts use exactly their captured refs. Historical policy is analogy
data, never current normative policy. Removed/moved/forged refs, a rewritten
main, cross-repository identity, source/subject substitution or immutable TASK
drift fail through the existing authentication boundary. B1 reauthenticates the
same cut after the call, even if the optional transport failed; later refs never
enrich it. Main descendants may be observed for currentness without using their
new content.

## Retrieval, counterexamples and independent support checks

Every selected episode remains in the bounded retrieval view. Exact/shared
concept overlap supplies A1 lexical ranking and terminal presence supplies a
separate structured signal. B1 independently checks declared relational paths,
including cross-module paths with different atoms and consumer names. The view
retains positive and negative outcomes, independence disanalogies, and unknown
outcomes. The lexical relation label is only a retrieval hint. RESULT is not a proof that
a mechanism worked; FAILURE is not a proven technical cause.

The version-2 request supplies exact source and candidate citation catalogs and
a closed B1 grammar:

```text
response: request_fingerprint, candidate_digest, cut_digest, outcome, suggestions
suggestion: source_path, candidate_path, candidate_probe, counterexamples,
            applicability, status, caution
candidate_probe: citation, side (effect | condition)
status: INFERRED
caution: NO_CAUSE_ESTABLISHED
applicability: LIMITED
```

The provider proposes a 2..4-edge source REQUIRES path, a candidate prefix one
edge shorter, and a separately declared candidate endpoint. The validator
resolves every citation itself, checks edge polarity, connected direction,
consumer continuity, acyclicity and role-preserving node alignment, and requires
an eligible observed source outcome. It rejects a terminal/lexical citation as
dependency support, a fabricated atom, a reversed/disconnected chain, an
already-declared edge or an explicit independence that contradicts the proposed
edge or its upstream consequence. Independently selected opposing-outcome and
independence witnesses must all be cited. Valid citation syntax or lexical
overlap cannot pass these relational checks. Duplicate proposals, unsupported
OBSERVED causes, certainty, model prose and action fields cause abstention.

For example, an artifact reader may declare `replay acceptance -> revision
match -> snapshot identity`. A decision adapter draft declares `advice acceptance
-> request match` and separately `candidate identity -> source seal`. B1 can
align these declarations and hypothesize the missing `request match -> candidate
identity` edge. The resulting conditional predicts advice unavailability when
candidate identity is absent, through request match; advice remaining available
under that controlled absence falsifies it. The names and dependencies come
from the selected context, not a card. An explicit statement that request match
is independent of candidate identity defeats that analogy despite identical
lexical identity mentions. This is a testable transfer hypothesis, not a claim
that any declared dependency is implemented or caused an observed outcome.

Each B1 suggestion renders this checked expression, source chain, alignment,
draft references, conditional experiment/effect and falsifier. Arbitrary model
prose never reaches the output. Source facts remain OBSERVED declarations;
transfer is INFERRED and LIMITED, with technical cause, efficacy and actual
transfer UNKNOWN. A1 retains its explicitly lexical card-based output.

Consumer comparisons require canonical declarations for both subjects. A
candidate's declarations become source-bound only when its **entire** goal,
invariants, acceptance, scope, consumer names and verification projection matches
an authenticated TASK at this cut; a draft label or fingerprint cannot
authenticate itself. Comparisons report only agreement/difference of those
declarations, with exact sources and actual runtime relationships UNKNOWN. When
either side lacks that support, different scope digests yield `scope differs;
consumer relationship UNKNOWN`. Identical scope digests also cannot establish
consumer agreement. Authenticated distinct consumers sharing the same paths
remain distinguishable by declarations rather than by scope.

## Failure containment and finite budgets

Outcomes are ADVICE, NO_MATERIAL_ADVICE, INSUFFICIENT_GROUNDED_EVIDENCE and
UNAVAILABLE. Missing useful history or no observed outcome abstains without a
model call. A model may return no material advice. Missing/busy/failed/invalid
transport or exhausted request/output budget returns bounded unavailability;
invalid semantic support yields insufficient grounded evidence. None of these
adds a gate to otherwise lawful direct Brain authoring. Existing cut
authentication errors propagate; advisory degradation is not an admission,
authentication bypass or a way to conceal a genuine canonical failure.

| Surface | Ceiling |
| --- | --- |
| Candidate | 32 KiB, 4096 characters per string, 32 items per list, closed fields |
| CAA source read | Original 8 TASKs/episodes, 128 refs, 256 KiB/document, 4 MiB source output, depth 24, 32,768 nodes, 512 Git calls, 60 s total / 10 s per command |
| Semantic view | 128 KiB; default CAA-01 remains 64 KiB |
| Consultation | One initial read; at most one reauthentication after a provider invocation (aggregate at most 1024 Git calls / 8 MiB / 120 s of source-reading budgets) |
| Model | Zero or one invocation, 128 KiB request, 16 KiB semantic response, existing 768 KiB native adapter response ceiling, 10 s wait |
| Advisory | At most 3 suggestions, 96 KiB JSON output, depth 16 |

The local transport is trusted executable code, not a sandboxed model response.
It must implement its own I/O deadline and have no model tool authority. Python
cannot forcibly cancel an arbitrary caller-supplied function. A timed-out
transport runs in one daemon thread; a process-local semaphore permits no new
CAA invocation until it exits. There are no retries or accumulating background
calls. The caller's wait is bounded; a late result is discarded and cannot cause
a lifecycle action. No model-supplied source, instruction or authorization is
executed. CPU projection is bounded by finite document, node and output sizes.

Metrics record observed invocation count, source-read count, request/semantic
response bytes and elapsed milliseconds. Tokens, monetary cost, live-provider
quality and production uplift remain UNKNOWN unless separately measured; this
package makes no superiority or cross-provider-live equivalence claim.

## Bounded comparative fixture and unchanged policy

`tests/test_caa_reasoner.py` contains a read-only deterministic comparative
fixture with predeclared dependency/abstention oracles, fixed candidate drafts and
pre-author source cuts. A1 and B1 receive identical candidate/cut inputs. It
records conditional proposals, opposing-outcome counterexamples, correct
abstention and observed resource overhead. Positive, failure, cross-module,
disanalogy and abstention cases accompany separate false-source, injection,
stale-policy, future-event, attribution, malformed and budget adversaries.
The polarity twin keeps the same lexical concepts while changing a required
dependency to independence: A1 selects the same cards while B1 rejects the
transfer. Candidate-endpoint variation changes B1's hypothesis and falsifier;
malformed role/direction/citation variants abstain. Consumer adversaries cover
different paths with the same consumers, distinct consumers with identical
paths, and unauthenticated draft labels. These are authored fixtures for Runtime
verification, not an Executor verification report.

Fixture B1 responses simulate compositional model output; they do not measure
actual pretrained-model reasoning quality. Live quality, hindsight-free generalization,
cross-provider superiority and production improvement remain UNKNOWN. CAA-04's
independent holdout/shadow/rollout remains separate, conditional and unselected.

TASK-408's HUMAN_ON_DEMAND direct AUTHOR_TASK / AUTHOR_REPAIR /
AUTHOR_REMEDIATION policy remains in effect. TCA/ROA/MOA remain Human-on-demand,
AOA keeps its soft default and explicit opt-out, and exact Human V1 retains its
existing constraints. Brain, Executor, Runtime, independent Reviewer and Publisher
retain their separate boundaries. CAA-01 remains usable without CAA-02.

Only the seven TASK-410 modify paths comprise this implementation: roadmap
state, CAA roadmap, this contract, CAA experience, CAA reasoner and their two test
files. The modified-source boundary test checks the exact permitted delta and
unchanged provider/policy surfaces. Runtime alone runs immutable TASK-410's
authored **minimum-sufficient-v1** commands and constructs canonical EVIDENCE.
Executor syntax/local sanity is not canonical verification. No full suite,
TASK-409/408/407 rewrite, CAA-03 flow hook, automatic next, learned model or
production uplift is delivered or inferred.
