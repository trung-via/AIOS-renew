# VP-03D GitHub App writer isolation and staged ref-authority contract

Stage 2 enforcement is OFF. Stage 1 ruleset **24824106** and its bypass settings
remain unchanged. Coding/Runtime verification of TASK-334 does not authorize live
enforcement, provisioning, smoke execution or production ref mutation by Executor.

## Credentials and authenticated operations

Runtime Writer App **5257829** and Control Writer App **5257909** have separate
private keys. A dedicated protected Windows broker service, rather than the
`.\TRUNG` runner or Executor, signs the App JWT and mints installation tokens.
The configured numeric repository ID must resolve to `trung-via/AIOS-renew` in the
token response. The token request selects that single repository explicitly.
Contents write is requested; workflow write is requested only for an exact delta
affecting `.github/workflows/` and only when separately provisioned. Implicit
metadata read is accepted; additional permissions/repositories are rejected.
Missing key, missing workflow permission or rejected installation permissions
blocks the write. See the [GitHub installation-token API](https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app).

Tokens remain in the service's transient child-process environment. They never
enter argv, URLs, request/response payloads, Git configuration files, journal,
logs, canonical artifacts or Executor context. Git hooks and credential helpers
are disabled for the credential-bearing push, redirects are disabled, and the
destination is fixed to the authorized repository. The service Git repository
and installed executable/code are protected deployment inputs. Windows
Administrator is trusted Human recovery authority, not an adversarial principal.

Mutual TLS, approved peer certificate pins, protected client-key ACLs and actual
Windows service-token SID selection authenticate caller roles. A supplied role,
App ID, operation name, username, copied object or an unauthenticated IPC request
cannot authenticate a caller. The existing owner seals restrict integrated call
sites; trusted deployed owner processes remain responsible for admission and
lifecycle decisions. This is not a second Runtime or a new lifecycle authority.

Every mutation binds operation, writer role, exact ref, old SHA or explicit absence,
new commit SHA or narrowly allowed lease deletion, protected main reservation,
and one nonce. The service checks namespace and immutable object identity, observes
exact current refs, then performs an atomic Git push with exact force-with-lease.
Git's CAS still arbitrates races after observation. The nonce is durably burned
before I/O. Failure/crash/replay never silently re-authorizes the mutation; the
existing owner must re-observe exact refs and use its existing recovery semantics.
No expiry takeover, scheduler, admission or automatic retry authority is added.

With protected setup present, affected owner push paths fail closed through the
broker; rejection cannot fall back to runner credentials. With setup absent,
legacy transport remains available and its principal/source stays unproven.
The current hosted ingress/publisher workflows are not asserted to use either
App. Moving them to approved service owners is protected operational setup.

## Creation and update namespace coverage

Both rule categories require separate positive/negative evaluation. This table
is a proposed coverage inventory, not an activated GitHub ruleset or bypass list.

| Namespace | Creation owner/operation | Update owner/operation |
| --- | --- | --- |
| `main` | Existing repository bootstrap; outside this execution | Control AUTHOR_TASK and Runtime Publisher, each with exact shared reservation/CAS; Stage 1 remains unchanged |
| `aios/review/*`, `aios/artifacts/*` | Runtime terminal RESULT | Immutable; updates rejected |
| `aios/failure/*`, `aios/failure-artifacts/*` | Runtime terminal FAILURE | Immutable; updates rejected |
| `aios/admission-failure/*`, `aios/admission-failure-delivery/*` | Runtime admission diagnostics | Immutable; updates rejected |
| `aios/publication-recovery/*` | Existing Runtime recovery admission | Immutable; updates rejected; no original PASS/source conversion |
| `aios/publication-source/*` | Existing Runtime recovery materialization, with exact content-addressed identity and absent-ref CAS | Immutable; updates rejected; source object alone grants no RUN/RESULT/review authority |
| `aios/dispatch/*` | Control AUTHOR_TASK | Immutable; updates rejected |
| `aios/review-decision/*` | Control SUBMIT_REVIEW | Immutable; updates rejected; verdict remains separately authored |
| `aios/remediation/*`, `aios/correction-dispatch/*` | Control AUTHOR_REMEDIATION | Immutable; updates rejected |
| `aios/repair/*`, `aios/repair-supersession/*`, `aios/repair-dispatch/*` | Control AUTHOR_REPAIR and explicit successor authorization | Each authorization immutable; successor is a new ref, never an old-ref rewrite |
| `aios/integration/*` | Existing Runtime reviewed integration owner | Immutable; updates rejected; unsupported writer call sites block until separately proved |
| `aios/publication-reservation/main` | Control TASK/review ingress or Runtime Publisher/recovery | Shared owner lease, never blindly assigned to one App; exact token CAS release is permitted, expiry grants no takeover |
| Other attention, wake, measurement, fixture or future namespaces | Outside bounded writer/source contract | UNKNOWN/BLOCK for source/authority claims; no generic App grant |

The shared lease preserves MAIN_MUTATION for Control TASK authoring and
REVIEW_TO_PUBLICATION handoff from Control REVIEW ingress to Runtime Publisher.
Runtime cannot acquire a MAIN_MUTATION lease. The broker checks token SHA,
identity/source/main binding and lifetime; the existing owners retain the canonical
lease semantics, contention handling, recovery and decision-set/source guards.
Restrict-creations, restrict-updates and any proposed deletion coverage must not
break this handoff/release or replace its CAS. A broad single-App lease grant is
not an acceptable Stage 2 design.

## Required separate live smoke and Human activation gate

Before any Stage 2 activation, Human must separately provision and inspect service
identities/code/ACLs, distinct App keys, installation/repository binding, caller TLS
credentials and minimum App permissions. A separately authorized service-only
smoke harness may use disposable sibling test refs or a dedicated test repository;
it receives only exact preauthorized cases. This TASK does not add a generic smoke
RPC, arbitrary ref grant or production-ref smoke launcher. Candidate creation
rules and candidate update rules must be evaluated independently on isolated
targets before their production configuration is considered.

Required live cases and safe observations:

| Case | Required observation |
| --- | --- |
| Approved Runtime/Control create | Correct App principal, exact namespace/ref/new SHA, explicit absent-old CAS, success |
| Approved update where applicable | Correct owner, exact prior/new SHA, shared lease/source where required, success |
| Wrong App or ordinary runner create/update | Denial for each category; no ref changed |
| Existing immutable ref rewrite/delete | Denial; original ref retained |
| Stale old SHA, competing lease or changed main | Denial; existing CAS/lifecycle conflict retained |
| Shared lease handoff/release | Control REVIEW acquisition to Runtime publication/release succeeds only for exact token/source; TASK lease remains Control |
| Missing key/installation/workflow permission | Honest rejected/unproven outcome; no token in Executor/log/artifact |
| Unauthenticated IPC, source/decision swap, mixed RUN or nonce replay | Denial; no mutation or retrospective issuance |
| Interrupted publication/recovery | Exact immutable source and lease survive; existing owner re-observes before continuation |

Smoke observations record only safe App/ref/old-new identity, digest/size and
outcome metadata. Protected diagnostics stay local. A successful smoke proves
the tested writer boundary only; it is not canonical Runtime verification,
semantic Reviewer PASS, publication, current-target proof or proof-reuse authority.
Both live positive and negative smoke, separate Human authority and complete
creation/update coverage are mandatory before enforcement. Missing any item keeps
Stage 2 OFF. No code here calls a GitHub ruleset creation/update/bypass API.
