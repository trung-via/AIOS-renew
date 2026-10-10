# VP-03D prospective Runtime terminal-source issuance v1

Status: implementation contract; deployment, live credentials and live smoke are
unproven. No ruleset, checkpoint, lifecycle lane or proof-reuse gate is activated.
TASK-335's published owner observations remain intact and subordinate.

## Origin and issuance

Only the actual existing operator-admitted PRIMARY `RuntimeCompletion.complete`
path can prepare issuance, after structural gates, Runtime verification, canonical
package validation and terminal persistence. Preparation requires the original
owner observation, original owner lease, admitted TASK authoring commit/blob,
revision, serialized RUN digest and execution-profile pin. Public constructors,
copied/equal objects, fixtures and externally called helpers do not enroll owners.
Python call-site seals are integration checks, not a Python adversary sandbox.

Independent authentication is the separately deployed Windows broker service.
An approved Runtime owner process uses its own protected TLS private key. The
service requires a valid mutually authenticated TLS peer and a separately pinned
certificate fingerprint for the RUNTIME role. The service's TLS identity is
pinned by the client before any protected payload is sent. A JSON role, workflow
label, runner username, environment variable, Git ref or GitHub App cannot supply
this authority. The current `.\TRUNG` runner is not an approved caller.

The service verifies immutable Git objects against the request: TASK revision and
blob at base, candidate and authoring commit; admitted RUN identity/base; profile
RUN/executor; candidate/tree; exact terminal artifact; exported RESULT/EVIDENCE;
original local RUN/RESULT/EVIDENCE byte metadata; and original raw digest/size.
The exact source record also includes PRIMARY admission operation and the admitted
RUN digest. Raw locators are checked against original EVIDENCE and the authorized
Runtime raw root. The service reads the raw bytes itself and returns metadata only.
The separate service journal authenticates immutable records with a service-held
key. Copying, swapping, tearing or changing journal content/identity fails closed.
The journal records issuance; it does not admit, select, dispatch or review work.

The original canonical byte representation remains local. New public exports use
the same frozen RUN/RESULT/EVIDENCE shapes, with opaque workspace/raw locators and
bounded verification summaries. Broker receipts contain safe identity/digest/size
metadata; they contain no raw bytes, filesystem locators, credentials or REVIEW
payload. Transport resolves the exact artifact SHA before the owner records
issuance; transport/App identity alone is never source authentication.

## Independent REVIEW ingress

The existing `_execute_submit_review` path records provenance after its canonical
decision write and existing reservation/validation gates. A separately authenticated
CONTROL peer must bind the same Runtime issuance, TASK/admission/RUN/profile,
candidate/tree, artifact and exported RESULT/EVIDENCE plus decision SHA/ref,
REVIEW digest/size and recorded verdict. The service independently checks the
decision parent, sole REVIEW delta and recorded payload binding. It does not
infer who made a semantic judgment from an App, certificate or Git identity.

`reviewer_origin_authenticated` means the approved ingress owner recorded that
exact decision. `reviewer_authority` remains false. PASS and CHANGES_REQUIRED are
recorded verdict values, never an inference from authenticated transport. A replay
reads an existing exact receipt only; it cannot mint historical provenance. A
conflicting decision, artifact, candidate, RUN, admission or digest is BLOCK.

## Read-only consumption and honest limits

`inspect_terminal_source` / `inspect_sources` take exact expected content pins,
then query the fixed independently authenticated service. They accept no issuer,
repository, remote, callback or selectable trust root. Original live owners can
also use `join_authenticated_provenance`; identity seals cannot be transferred to
equal objects, and returned metadata is copied rather than exposing mutable storage.
Reading creates no receipt, admission, continuation or publication authority.

An authenticated source is still UNKNOWN for current-target applicability and
currentness. Raw remains RAW_UNAVAILABLE unless a separately authenticated READER
with authorized local access compares the immutable original digest/size. Missing
reader permission is unavailable; a changed/swapped raw file is BLOCK. No source
proof is silently converted into current-target EVIDENCE or acceptance discharge.
The frozen VP-03C handoff and all inactive effects remain unchanged.

REPAIR, REMEDIATION, direct candidate, publication recovery and pre-verification
or failure objects have no terminal issuance in this bounded v1. Their existing
lifecycle paths remain available, but their source provenance is UNKNOWN. Failed
TASK-333/334 candidates and original artifacts are untouched. Historical records
without an original prospective service receipt cannot be retroactively minted.

Legacy artifacts still decode. Transport replay recognizes exact legacy bytes or
the coherent new privacy view, without changing the existing artifact ref. Embedded
RUN privacy views stay coherent with correction lineage. Failure diagnostics remain
local; public failure identity/phase/candidate facts remain usable by the existing
owners. Diagnostic reduction grants no original-raw/source provenance.
Verification planning/cache reuse is untouched: evidence is reused until the
existing policy invalidates it, and no automatic baseline/full-suite replay is added.

## Protected deployment gate

Deployment must pin reviewed Runtime/Control/broker code outside the Executor
checkout and run approved owners/broker as dedicated Windows virtual service
identities. Protect the canonical service repository, journal, receipt key, TLS
keys, trust configuration and original Runtime raw/state location against runner
write/delete and credential-read access. Executor candidate objects can be imported
as bounded Git packs into the separate service repository; no workspace hooks or
remote URL can execute with the installation token. A shared runner identity or
an Executor-writable trust root cannot complete this gate.

Native Executor and candidate verification subprocesses must retain their
untrusted execution identity, separate from the approved owner's TLS key and
protected state. Do not provision/enroll a Runtime service that launches candidate
code with approved owner credentials. The existing injected native/verification
runner boundaries must establish this in protected setup and negative live smoke;
running the default inherited-identity subprocess runner under an enrolled owner
is not an approved deployment. The implementation does not assert that the
current shared runners satisfy this deployment requirement.

`C:/ProgramData/AIOS/owner-client.json` is protected machine setup, never a TASK or
environment input. Its `owners` mapping selects host/port, CA, certificate/key
locators and pinned service certificate digest by the actual Windows process-token
SID. Ordinary runner accounts are rejected. TLS key ACLs permit only their owning
service identity, SYSTEM and trusted Administrators. SYSTEM/Administrator recovery
remains explicitly trusted; there is no anti-Administrator or hardware-attestation
claim. Missing setup/credentials/permissions yields UNKNOWN or a rejected write.

The tests substitute isolated approved-peer delivery and temporary Git objects;
they do not establish deployed OS/TLS isolation or live GitHub writer provenance.
Runtime owns execution and canonical EVIDENCE for those tests. This implementation
does not deploy a service, read a live key or claim live smoke success.
