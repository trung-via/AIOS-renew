# VP-03D owner-bound provenance v1

Status: prospective TASK-335 r1 owner bridge. **NOT_ACTIVATED**.

This contract exposes bounded observations of actions performed by existing
operator and authoring ingress owners. It does not install an independent
canonical proof issuer. Runtime retains execution, terminal completion,
verification and EVIDENCE authority; Reviewer retains semantic verdict authority;
Publisher retains publication authority. Human selection and Brain authoring
remain separate. No observation admits another operation or discharges acceptance.

## Trust scope

`runtime_provenance_owner.py` retains immutable invocation-local snapshots on
the original `RuntimeCompletion` or `IngressResult` object. There is no registry,
second state store, public setter, enrollment, selectable issuer, trust callback,
key, endpoint, CLI option or provenance transport. Snapshots expire with their
owning objects. They cannot be recovered from a Git repository, immutable SHA,
serialized fact object or historical artifact.

Capture is restricted to the integrated code of the existing operator completion
sites and `_execute_submit_review`, after their existing admission checks. The
private capture functions reject external calls, functions with matching names,
constructors and fixture call sites. A closure-held seal binds each retained
snapshot to the original object identity. Copying a snapshot to another owner
does not transfer its origin. Public `OwnerFacts` construction confers nothing.

This is the existing trusted-operation, in-process operational boundary. It is
**not a guarantee against arbitrary malicious Python code that controls these
owners**, replaces their code, extracts closure state, rewrites process memory or
changes trusted workflow/policy inputs. An actual owner invocation on disposable
Git may observe that invocation; coherent Git content alone never authenticates
an independent issuer. `issuer_authenticated` is always false and `source_state`
is always UNKNOWN in this version. OWNER_BOUND identifies the observed operation,
not a globally authenticated repository or Human identity.

## Operator boundary

The six existing completion sites capture before handing control to Runtime:
PRIMARY, REPAIR, REMEDIATION, direct candidate, primary recovery and publication
source recovery. No constructor parameter can supply provenance.

For PRIMARY the observation binds exact admitted TASK ID/revision and base TASK
blob, admitted authoring commit when the existing exact authorization check was
used, admission-main SHA, actual RUN/base/Executor, persisted RUN digest and
execution profile/digest. It checks the actual owner-created lease's object
identity in the owner-created registry, all lease/RUN fields and return affinity.
It retains dispatch or migration/successor continuation identities already held
by the owner. Legacy admission lacking authoring-commit authorization remains
TASK_AUTHORIZATION_UNAVAILABLE. Unsynchronized local admission cannot provide
independent currentness.

Admission currentness is temporally scoped **AT_ADMISSION**. The candidate and
local-main snapshots taken at handoff are exact content observations. Consumers
check those local refs for subsequent movement. A snapshot is not a fresh remote
main/CAS/ABA proof, a terminal RESULT or a production canonical source.

REPAIR uses its existing failure/repair admission, preserving failed RUN/head,
root/result bases, action, authorization when present and repair-lineage metadata
digest. Executorless reuse records executor invocation as NO and retains the
existing inherited RUN Executor label; it does not select an Executor or invent a
profile. REMEDIATION and direct candidate preserve their existing exact
authorization and predecessor/execution-base bindings. No correction or recovery
path receives a fabricated PRIMARY lease. Unavailable profile, authoring commit,
continuation or independent currentness remains an explicit gap. Supported
recoveries may therefore expose only partial owner-bound facts.

## Independent SUBMIT_REVIEW boundary

Only a successful existing canonical SUBMIT_REVIEW operation records exact RUN,
TASK/revision/blob, candidate and artifacts SHA, immutable decision SHA, REVIEW
digest/size, review ID and **recorded verdict**. REVIEW bytes are checked against
the exact committed metadata. They are not included in the public observation.
No Runtime function supplies a review verdict.

The existing idempotent replay fast path validates an identical decision but
does not freshly re-admit all RUN/artifact content. It retains exact decision,
candidate and REVIEW digest while exposing REPLAY_BINDING_UNAVAILABLE for the
missing fresh artifact join. Conflicting replay remains the original ingress
error. Remote ingress observations do not independently requery mutable refs;
they explicitly retain INDEPENDENT_CURRENTNESS_UNAVAILABLE.

GitHub `deliver_event` separately records that the existing Issue policy admitted
the repository, Issue author, marker and bounded body. It records a policy/body
digest and bounded repository/actor/Issue identity. This differs from direct
local ingress, which has no Issue-policy observation.

**CARRIER_ORIGIN_UNAVAILABLE:** the established workflow delivers a GitHub event
file under its protected operational policy, but its existing independently
authenticated origin receipt is restricted to AUTHOR_TASK. SUBMIT_REVIEW has no
such authenticated receipt in the supplied boundary. This bridge cannot turn
that file, actor allowlist, ticket, request content, credential-free CLI,
environment labels, configured remote or Git SHA into a protected-origin
credential. Even policy-admitted delivery retains `carrier_origin=UNKNOWN` and
`reviewer_authority=false`. Negative/anonymous policy routes retain their
original rejection. Any future authenticated review carrier needs separately
authorized protected policy/capability work; this version does not invent it or
reuse AUTHOR_TASK's operation-specific receipt.

An authenticated carrier, if independently available in a future approved
extension, would still attest only delivery origin. It could never itself issue
semantic Reviewer PASS. Recorded PASS and Reviewer authority remain distinct.

## Read-only interface and classifications

```python
from aios_renew.runtime_provenance_owner import (
    read_owner_provenance, join_owner_provenance,
)

admission = read_owner_provenance(original_completion)
decision = read_owner_provenance(original_ingress_result)
joined = join_owner_provenance(original_completion, original_ingress_result)
# Also: original_completion.owner_provenance / original_ingress_result.owner_provenance
```

Consumers accept original live owner objects only. They accept no root, remote,
issuer, callback, raw path, Git fixture, REVIEW bytes or expected-currentness
assertion. Exact supported object types and the retained identity seal are
checked before any read. No upstream VP-03 handoff consumer is activated or
rewritten to treat these observations as proof.

`OwnerFacts` has schema `owner-bound-provenance-v1`, kind, status, owner_origin,
immutable sorted primitive bindings and typed `Gap` values. Status OBSERVED
means the bounded owner metadata was captured without a known binding gap;
UNKNOWN means a required fact is unavailable; BLOCK means a disagreement or
changed binding was detected. None is a canonical source PASS. The invariant
fields are `source_state=UNKNOWN`, `issuer_authenticated=false`,
`reviewer_authority=false`, `raw_state=RAW_UNAVAILABLE`.
`source_gaps` always identifies the independent issuer, fresh currentness,
terminal-source and protected-raw gaps separately from the observation status.

| Condition | Classification |
| --- | --- |
| External constructor, copied snapshot, old artifact | OWNER_UNAVAILABLE / UNKNOWN |
| Unsupported object/subclass | UNSUPPORTED_OWNER / UNKNOWN |
| Missing capture or unsupported owner facts | CAPTURE_UNAVAILABLE / UNKNOWN |
| TASK blob/revision, RUN, profile or PRIMARY lease disagreement | Typed binding mismatch / BLOCK |
| Missing authoring commit, profile, continuation, lease or main | Corresponding UNAVAILABLE / UNKNOWN |
| Detached PRIMARY/direct subject or later main/ref/file/owner movement | DETACHED_SUBJECT, MAIN_CHANGED or OWNER_BINDING_CHANGED / BLOCK |
| Missing review or replay artifact binding | REVIEW_UNAVAILABLE or REPLAY_BINDING_UNAVAILABLE / UNKNOWN |
| Different RUN/TASK/revision/blob/candidate in the two owners | REVIEW_BINDING_MISMATCH / BLOCK |
| Missing independently protected review carrier | CARRIER_ORIGIN_UNAVAILABLE / UNKNOWN |
| Missing independent issuer, fresh currentness or terminal source | Corresponding UNAVAILABLE / UNKNOWN |
| Protected original raw not independently authorized/retrieved | RAW_UNAVAILABLE |

Metadata reads are bounded to one MiB per owner file; duplicate JSON keys are
rejected. There is no fallback to caller data. Read-only joins retain residuals,
match exact identities and never promote a recorded verdict, metadata digest or
local-currentness check into source authority. Capture/read diagnostics contain
codes, not exception messages, raw output or local paths. Subordinate capture
failure cannot replace an original canonical exception or override successful
completion. Existing failure causes and transition ordering remain intact.

## Public-repository privacy and authority effects

The observation contains only bounded identity metadata, SHAs/digests, sizes and
classifications. It includes no local workspace/raw path, command output,
environment variables, credential, private log or complete REVIEW payload. It
never reads verification raw and never writes a public ref or artifact. Digest
and size are content metadata, not permission or proof. Consumers without
authorized protected raw access remain RAW_UNAVAILABLE.

Canonical TASK/RUN/RESULT/EVIDENCE/REVIEW schemas, historical decoders, return
affinity, authoring origin admission, Executor selection, execution, verification
scheduling, review verdict submission, protected transport, publication and
failure terminalization are unchanged. `IngressResult.as_dict()`/render and
Runtime's canonical serialization do not include the invocation snapshot. No
provenance consumer dispatches, schedules, publishes, repairs, reuses evidence or
changes acceptance coverage.

## Deterministic focused coverage and remaining gates

`tests/test_runtime_provenance_owner.py` exercises actual existing owner admission
and canonical authoring ingress with disposable repositories/local bare remotes.
Native execution and Runtime completion are intercepted; unchanged upstream test
suites and live GitHub are not invoked. It covers owner vs constructor/fixture,
exact TASK/profile/lease/main binding, original rejection causes, correction and
direct-candidate lineage, independent review absence and recorded verdict,
policy-admitted vs unauthenticated carrier, replay conflicts, stale bindings,
raw privacy and invariant lack of authority effects. Runtime owns execution of
the focused verification and production of canonical EVIDENCE.

This is prospective only. RUN-333-001 and RUN-334-001 retain immutable FAILURE
artifacts `53230d3eeac098a9350ce99669877b51623006e4` and
`7179c1eb16bbb2b97d1ddbe389ec41925c4ceb3d`, respectively. Their failed candidates
`af16ec63c8700b9f7411434d338f044300392b86` and
`321227b18fe67eff43547ee620d9d7b3ac50e3ec` receive no retrospective owner identity,
RESULT/PASS, repair or publication. TASK-334's failed-candidate raw/transport
implementation is not imported into active main by this bridge.

This task requires its own independent Runtime RESULT, semantic REVIEW PASS and
exact publication. Any TASK-334/333 correction follows a separate Brain-authored
authorized correction on preserved failed lineage, with lawful evidence reuse
and no unnecessary unchanged base replay. Review and publication of this bridge
alone do not establish TASK-334 raw access or transport success. Future source
issuance, protected review-origin authentication, fresh currentness and protected
raw access remain distinct gates.

VP-01 remains DEFERRED_NOT_WAIVED; VP-02/VP-03 exits are not established; KA-01
remains NOT_ACTIVATED. Live preterminal C1/C2, CAS/ABA, VP-04/VP-05/VP-07,
production proof reuse, acceptance discharge and downstream adoption are not
authorized. The authored verification policy remains minimum-sufficient-v2.
