# AIOS H4C0 Origin Capture Conformance v1

Status: PAGE-SCOPED FALLBACK CONTRACT / LIVE OBSERVATION UNPROVED

TASK bindings: TASK-292 revision 1 (preserved prior candidate); TASK-293 revision 1 (selected fallback); TASK-295 revision 2 (published insertion correction); TASK-296 revision 1 (bounded editor-reconciliation correction)
Parent: [H4 Origin-Affine Return and Unattended Local Wake](AIOS-H4-ORIGIN-AFFINE-UNATTENDED-WAKE-v1.md)  
Authority: Runtime verifies implementation; Human/Brain evaluates later live feasibility.

Sections 1–5 preserve the TASK-292 OpenAI-session candidate and its historical
contract. Its accepted implementation, REVIEW/PASS, and exact publication remain
valid evidence. The current Human-observed regular-Chat surface did not expose
the Developer Mode/custom MCP entry for that candidate's planned live procedure;
that availability observation does not invalidate its engineering lineage.
Sections 6–10 define the selected page-scoped fallback, its bounded corrections,
and its later Human/Brain live gate.

## 1. Feasibility basis and limits

The [OpenAI Plugin Reference](https://developers.openai.com/plugins/reference), consulted on 2026-10-04, documents tool-call `_meta["openai/session"]` as an anonymized conversation id for correlating tool calls within the same ChatGPT session. This is external transport feasibility evidence. It is not canonical engineering truth, a guarantee of availability or permanence, authentication, or proof of exact conversation affinity in the deployed environment.

The value is not assumed to equal, contain, or be reversibly convertible into a `chatgpt.com` conversation URL or UUID. The probe does not inspect its meaning. A successful synthetic test or TASK-292 Runtime acceptance does **not** prove the future live two-conversation observation, close H4C0, or authorize H4C1. Even a live two-chat comparison would not establish a return destination or delivery capability.

[OpenAI's developer-mode connection guide](https://developers.openai.com/plugins/deploy/connect-chatgpt) describes public HTTPS and Secure MCP Tunnel as testing connection options. The [Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) supports private stdio MCP servers. This candidate uses a dedicated stdio entry through that later development connection; it adds no public listener or network call to origin derivation.

## 2. Frozen v1 transport boundary

The provider adapter contract is `OPENAI_SESSION_ORIGIN_CAPTURE_V1`. Its only origin input is the exact `openai/session` key in the host-supplied tool-call `_meta` object. The MCP adapter obtains that object from the SDK request context, not from a tool argument, initialization metadata, model text, or HTTP/browser state, and passes it to the provider-neutral `capture_origin` primitive. A private OpenAI extractor terminates at the neutral result contract. Offline tests inject synthetic metadata into this same adapter boundary.

Local safety grammar:

- Metadata must be a plain JSON object with at most 64 fields. Every key is a string of 1 through 128 characters. Unrelated values are ignored and never traversed as origin candidates.
- The exact session field must contain 1 through 1024 visible ASCII bytes (`0x21` through `0x7e`); whitespace, controls, non-ASCII, null, collections, numbers, booleans, empty strings, and over-bound strings fail closed.
- This grammar is a bounded local v1 policy, not a claim that OpenAI specifies a UUID, prefix, encoding, or identifier shape. If real metadata falls outside it, the live result is `UNPROVED`; no coercion or automatic grammar expansion is permitted.
- The exact accepted value is hashed without trimming, case folding, URL parsing, decoding, or normalization.
- Stdio requests are bounded to 65,536 UTF-8 bytes per JSON line. Duplicate keys anywhere, invalid JSON/UTF-8, nonstandard numeric constants, and over-bound requests close the connection without a handle. Duplicate origin keys cannot silently become last-value-wins input. SDK-level malformed requests also fail closed.

Provider-neutral output contract: `ORIGIN_HANDLE_V1`.

```text
digest = SHA256(b"AIOS\x00ORIGIN_HANDLE_V1\x00openai/session\x00" + exact_session_ascii_bytes)
origin_handle = "origin-v1:" + digest_as_64_lowercase_hex_digits
```

The domain identifies the derivation version and provider input namespace; the exposed 74-character handle has no raw-provider component. The response contains only `contract`, `status`, `reason`, and `origin_handle`. `CAPTURED` carries a handle and `ACCEPTED`; `UNPROVED` carries null and one fixed code: `METADATA_ABSENT`, `METADATA_INVALID`, `ORIGIN_INPUT_ABSENT`, `ORIGIN_INPUT_INVALID`, `ORIGIN_INPUT_OVER_BOUND`, or `PROBE_INPUT_INVALID`.

SHA-256 supplies a deterministic opaque selector, not encryption, an authorization token, or a guarantee against guessing low-entropy inputs. Replacing the provider adapter can retain the neutral output contract without changing Brain, Runtime, Reviewer, Publisher, or lifecycle semantics. Provider/session identity never becomes canonical semantic identity.

There is no fallback to repository identity, configured/default chat, caller-supplied chat identifiers, tool arguments, assistant output, transcripts, model memory, active tab, browser focus, timestamps, or account/profile state. This stateless primitive cannot prove freshness, permanence, conflicts between later calls, or regular-Chat surface eligibility; ambiguity in the later observation remains `UNPROVED`.

## 3. One read-only development entry

Entry: `scripts/aios_origin_capture_probe.py`. Exactly one MCP tool is advertised: `aios_origin_capture`. It takes `{}` (no semantic or identity arguments). Additional arguments or an unknown tool name return `UNPROVED` with no handle. Annotations declare read-only, nondestructive, idempotent, and closed-world computation.

After reviewed publication, the Human may prepare an isolated development environment with:

```powershell
python -m pip install -e '.[origin-capture-probe]'
python scripts/aios_origin_capture_probe.py
```

The second command is the stdio MCP entry to configure as the tunnel's MCP command, with the published checkout as its working directory and that environment's Python interpreter. It is not an AIOS execution/operator/worker launcher. Follow the current official tunnel guide to configure the authorized development connection. A public HTTPS connection would require separately prepared MCP hosting; this stdio script alone is not an HTTP endpoint.

The probe computes in memory, makes no acquisition call, reads no route or repository configuration, and writes no state. Its process disables Python/SDK logging and does not print exception details or tracebacks. Only fixed status metadata and opaque handles leave its tool-result boundary. The source metadata is transient input, never attached to a result, stored in a cache, or retained in a registry. Logs and diagnostics in external hosting/tunnel infrastructure must also avoid raw request bodies; the probe cannot control third-party logging.

The optional `origin-capture-probe` extra installs the MCP SDK. Core primitive and adapter tests need no SDK or network; the SDK registration/request-context case runs offline when that extra is installed. All committed test tokens are synthetic. Tests cover repeat and distinct identity, exact-input/domain/version separation, invalid/missing/bounded metadata, forbidden fallback sources, argument rejection, output confidentiality, duplicate wire keys, and no adapter filesystem writes. They are implementation checks for Runtime, not live conformance records.

## 4. Bounded post-publication Human/Brain live procedure

Preconditions: TASK-292 has passed its Runtime and Reviewer boundaries and Publisher has published the exact reviewed candidate. Human/Brain selects that published SHA for this observation and authorizes the development connection. No Executor or Runtime verification invocation substitutes for this procedure.

1. Use the published probe through a Human-controlled Secure MCP Tunnel stdio connection (or separately authorized public HTTPS MCP hosting). Keep request-body/debug logging, raw metadata exports, transcript capture, and raw-metadata diagnostics disabled. Confirm discovery advertises only `aios_origin_capture`, an empty input schema, and the read-only annotations. Lack of developer-mode/tunnel access or uncertain logging leaves the observation `UNPROVED`.
2. Open two distinct **regular ChatGPT conversations** A and B within the same repository/project context. Human identifies the two surfaces directly; the probe does not acquire browser state or infer which chat is active. Do not use Work, MCP Events, an API Playground session, or synthetic MCP calls as substitutes.
3. Attach the same development connection to both conversations. In A, request two separate tool invocations of `aios_origin_capture` with `{}`. In B, request two separate invocations with `{}`. Inspect the actual structured tool results directly; do not parse assistant prose to infer identity. Call them A1, A2, B1, B2 for comparison; these are observation labels, not TASK/RUN/flow identities.
4. Require all four results to be `CAPTURED` under `ORIGIN_HANDLE_V1`, with A1 = A2, B1 = B2, and A1 != B1. Compare only opaque handles. Never inspect, copy, log, or record the raw session metadata or transcript content. No chat URL/UUID is needed for the comparison or canonical record.
5. Stop after those four calls. Any missing/invalid handle, identity change within a conversation, equal handles across distinct conversations, unclear result provenance, or uncertainty about the host metadata is `UNPROVED`. Do not retry until a desired result appears, guess identity, select a default chat, or normalize input. Human/Brain may authorize a new bounded observation after a concrete invalidating condition is addressed.
6. Human/Brain records only the published implementation SHA, contract version, connection mode (no credentials/endpoint details), A/B observation labels, four fixed statuses, same/different comparison booleans, and its feasibility decision with a bounded reason. Opaque handles may remain transient for comparison; raw session values, chat URLs, transcript content, screenshots of requests, and raw request/response dumps must never enter canonical TASK/RUN/RESULT/FAILURE/REVIEW/roadmap records, Runtime EVIDENCE, repository files, or a reasoning store.
7. Human/Brain combines the observation with the required two-stage architecture audit and explicitly decides H4C0 live closure or architecture fallback. A successful comparison is bounded evidence for that exact environment, not a permanence guarantee or automatic roadmap advancement. An `UNPROVED` outcome keeps H4C0 open and blocks H4C1.

## 5. Preserved authority and sequencing

H4C1 remains **BLOCKED UNTIL H4C0 LIVE CLOSURE**. TASK-292 acceptance, reviewed publication, and synthetic tests alone do not clear that block.

This candidate adds no return routing, route database/registry/ownership, mapping to a chat URL, browser automation, wake delivery, local-wake changes, second transport, Work/MCP-Events dependency, transcript scraping, or assistant-output interpretation. It allocates no TASK/RUN, chooses no semantic flow or `next_action`, mutates no canonical engineering state, and changes no lifecycle, Runtime, Reviewer, Publisher, or roadmap authority. TASK-291's permissive local-wake state is not origin proof and is untouched.

Runtime owns canonical verification and EVIDENCE. Reviewer owns the semantic verdict. Publisher owns exact reviewed publication. Human/Brain alone evaluates this later live observation and decides H4C0 closure or architecture fallback; later routing and wake phases require their own authorized contracts.

## 6. Selected page-scoped origin-bootstrap contract

Contract: `PAGE_SCOPED_AIOS_SEND_ORIGIN_BOOTSTRAP_V1`. Implementation:
`src/aios_renew/origin_bootstrap.py`; one local development entry:
`scripts/aios_origin_bootstrap.py`.

The entry attaches to an existing Human-authorized regular-Chat Chromium browser
through its existing loopback CDP endpoint. It neither starts a browser nor opens,
navigates, focuses, or chooses a tab. It exposes **Connect this chat to AIOS and
send draft** inside eligible regular-Chat documents. The Human writes and approves
the initial handoff, then clicks that document's button. This click authorizes
only that initial submission; the helper never interprets the draft or dispatches
an AIOS execution itself. Developer Mode, MCP, Responses API, ChatGPT Work, account
metadata, and `openai/session` are not dependencies.

A browser-trusted click on the installed button creates a random 256-bit challenge
in a document-owned closure, with a 30-second monotonic expiry. Programmatic clicks
and copied tokens cannot create a pending attestation. A separate random document
nonce distinguishes exact documents, including multiple tabs of one conversation.
The trusted local adapter retains a private document JSHandle to the attestation
closure; the page exposes only an installation collision guard, never a challenge
API. A caller-created page object or copied guard cannot establish origin. Neither
challenge nor document nonce is durable. Scanning is bounded to 8 browser
contexts and 32 total documents; absent, multiple, inaccessible, malformed, expired,
or replaced challenge-bearing documents fail closed. The adapter never asks for
the active tab, recent chat, focus, timestamps, repository identity, or content as
origin authority. Clock values bound freshness only.

The existing local-wake HTTPS/regular-Chat URL grammar is reused. An exact normalized
route may include a Project prefix; its final conversation UUID is the conversation
key, consistent with the existing wake binding grammar. Trailing slash and valid
Project location changes on a later explicit bootstrap preserve that conversation's
route. During one bootstrap the exact normalized location must remain unchanged;
main-frame navigation epochs and temporary page-history listeners/hooks also
invalidate a route-change-and-return during proof. Cleanup removes its listeners
and restores only history functions that still belong to that closure.
One document can prove while another tab shows the same conversation: those tabs
share a conversation route, rather than competing routes. Two pending attestations
are ambiguity and are rejected together by this bounded one-gesture session.

Mandatory order:

1. Prove exactly one eligible document owns the fresh Human challenge.
2. Mint or reuse its conversation-scoped opaque handle under the exclusive local
   registry lock.
3. Durably write the local binding using the existing exclusive pending-file,
   fsync, write-through replacement, and directory-sync primitives.
4. Revalidate the same document, challenge, exact route, registry contents, and
   binding generation.
5. Append the bounded opaque envelope to that same Human-authored draft, then
   prove exact draft preservation and the application's scoped Send readiness.
6. Revalidate again; durably record attempt intent; revalidate the page and
   generation once more; click the unique enabled Send in the composer's unique
   enclosing form. Final page/draft/surface checks and click share one browser
   event-loop turn. The challenge is consumed before the click.

No submission occurs if binding durability, challenge uniqueness, page continuity,
regular surface eligibility, registry consistency, generation, or insertion is
unproved. Login, Work/team/business/enterprise markers, active generation,
non-unique/disabled composers, unsupported rich draft representations, or
ambiguous Send/form structure block the attempt. The supported draft shape is
nonempty exact plaintext up to 65,536 characters, with identical rendered and
DOM text. The gesture snapshots those bytes only for edit integrity; a later draft
change blocks insertion and never changes origin identity. After append, an
origin-bootstrap-only predicate proves logical bytes from the selected composer's
DOM. Its bounded allowlist consists of root plaintext nodes and flat P/DIV blocks
whose only children are text or explicit BR leaves. Blocks carry no attributes;
BR leaves are bare or carry exactly `class="ProseMirror-trailingBreak"`. Every
block boundary contributes exactly one logical newline and every inline bare BR
contributes one. A sole BR is an empty-block placeholder; the named trailing BR
contributes no bytes only at the end of an empty block or after a proved newline.
No other nesting, nodes, or attributes are accepted, including rich content whose
aggregate text happens to match. Hidden, collapsed, or transparent block/BR
content fails closed. Traversal is bounded by the staged logical draft length.

The derived logical text must equal the immutable Human draft plus exactly two
newline characters plus the bounded envelope. `textContent` must equal the
allowlisted text leaves. `innerText` may differ only by one of six explicit block
layout projections: uniformly zero, one, or two rendered newlines at proved block
boundaries, with empty BR placeholders uniformly rendered as zero or one newline.
Those display projections do not add, remove, or reorder logical separators.
Literal CRLF, Unicode, and whitespace bytes within text leaves remain exact.
Unknown or non-exact representations remain blocked even if visually similar.

The application's input event may reconcile that same composer asynchronously.
Readiness polls only within its existing three-second bound and re-evaluates the
same staged composer, document, challenge, page, and exact logical draft. Waiting
itself supplies no equivalence or origin authority. The first accepted readiness
retains the unique visible enabled scoped Send and its enclosing form; replacing
either blocks later revalidation. After insertion, the existing revalidation
boundaries also re-prove exact draft equivalence and that retained control before
durable attempt intent and again after its write. The final check and AIOS-owned
click still share one browser event-loop turn. Send selection continues to use
only the composer's unique enclosing form, never the document.
Editing only appends two newlines plus metadata; it never replaces,
clears, trims, normalizes, or semantically interprets the draft. Draft bytes remain
ephemeral in the selected document and are released on cleanup, never returned
to Python or stored in the registry. Transcript and assistant output are not read.

After submission, composer clearing and bounded scoped controls supply only a
structural submission witness. An uncertain click, witness, completion write, or
process crash leaves an `ATTEMPTING`/`AMBIGUOUS` local marker. There is no automatic
resend, even on a new helper invocation or gesture. Human reconciliation is
required; deleting markers blindly is not a recovery procedure. A pre-submit
failure may leave appended metadata in the draft; the Human must inspect it.
The helper never rolls back or retries an uncertain edit, and an existing bootstrap
marker prevents duplicate metadata insertion.

## 7. Local registry and bounded output

The registry path and its `.lock`/`.pending` siblings must resolve outside every
Git working tree or bare Git store. Use one fixed registry for this authorized
browser environment; parallel helpers must use the same path, never independent
registry copies. Its parent directory must already exist with appropriate local
access controls. State is bounded to 256 conversations and 262,144 encoded bytes;
duplicate JSON keys, malformed records, duplicate handles, generation conflict,
capacity exhaustion, stale locks, and pending writes fail closed. It stores only
normalized conversation identity, last explicitly proved location, loopback browser
endpoint, opaque route handle, generation, and one bounded submission-attempt marker.
It stores no repository/TASK/RUN, prompt, transcript, assistant text, credentials,
challenge, semantic decision, or wake payload. The endpoint refers to the already
authorized browser/profile; changing it requires explicit later Human ownership
handling and is rejected by this contract. Generation is a positive bounded integer;
this helper does not implement rebind/transfer or increment generation automatically.

Handles have the grammar `page-origin-v1:` plus 64 lowercase hexadecimal digits,
minted randomly and collision-checked. Repeated flows and long TASK/RUN/review/repair/
publication continuations reuse the established handle and generation. A new Human
gesture is for initial binding or a deliberate development bootstrap, not every
subsequent message. Valid reload/reopen uses a fresh page challenge to find the same
durable conversation record. Losing local state is a blocker for continuity, not
permission to guess a prior route.

The appended envelope is at most 384 ASCII bytes:

```text
[AIOS ORIGIN BOOTSTRAP]
{"contract":"PAGE_SCOPED_AIOS_SEND_ORIGIN_BOOTSTRAP_V1","route_handle":"page-origin-v1:<64 lowercase hex digits>","generation":1}
[/AIOS ORIGIN BOOTSTRAP]
```

Console results contain only `contract`, `status`, `reason`, `route_handle`, and
`generation`. Status is `SUBMITTED`, `UNPROVED`, or `AMBIGUOUS`; rejected/ambiguous
results expose null handle/generation and fixed reason codes. Raw URLs/UUIDs,
endpoints, local paths, challenge bytes, drafts, exceptions, and browser diagnostics
must not enter repository/canonical outputs or Runtime EVIDENCE. The entry disables
Python logging and rejects enabled `DEBUG`/`PWDEBUG` environments before attaching.
Do not enable external browser/CDP tracing, request-body logs, or transcript exports.

The handle is a bounded selector, not a credential or authority. Its presence or
value cannot allocate TASK/RUN, select `next_action`, authorize execution, review,
publication, transfer ownership, or advance roadmap state. The registry exposes no
H4C1 return-resolution/delivery API and its attempt marker is not a wake queue.
Existing local-chat-wake delivery code, state, dedupe, recovery, and lane semantics
are unchanged; only its safety/normalization/durability primitives are reused.

## 8. One bounded local development surface

After exact reviewed publication, Human/Brain may authorize this local procedure
against that published checkout. Use the existing authenticated regular-Chat
browser and its authorized loopback CDP endpoint. If that environment is unavailable
or its authorization/profile provenance is uncertain, stop with `UNPROVED`.
The helper does not acquire an account, create a new profile, or launch a browser.

```powershell
python -m pip install -e '.[local-chat-wake]'
$originRegistry = Join-Path $env:LOCALAPPDATA 'AIOS\origin-bootstrap\registry.json'
New-Item -ItemType Directory -Force -Path (Split-Path $originRegistry) | Out-Null
python scripts/aios_origin_bootstrap.py --state $originRegistry --endpoint http://127.0.0.1:9222 --seconds 120
```

The endpoint above is an illustrative loopback value; use only the already
authorized environment's endpoint. Protect the state directory machine-locally.
The gesture-wait phase lasts at most 300 seconds; the session handles at most one pending gesture/attempt,
prints one bounded JSON result, removes its buttons, and disconnects without closing
the browser. Open the intended chats before starting the session. A new session
installs a fresh private closure after cleanup and always requires a new trusted
Human click. Reload/reopen invalidates the old document handle. The optional bounded
`--proof-delay-ms 3000` (0–5000) lets the Human arrange a page-change/ambiguity trial
before proof; waiting supplies no origin authority and cannot extend the challenge.

This is a development origin-handshake surface, not an AIOS operator/worker launcher.
There is no daemon, generic service, return router, polling wake consumer, or second
transport queue. The finite local loop observes only document-owned pending gestures.

## 9. Required post-publication two-regular-chat Human/Brain procedure

Preconditions: Runtime acceptance, a fresh independent Reviewer verdict, and
Publisher publication of the exact reviewed TASK-296 correction. Human/Brain selects that SHA
and authorizes the existing browser environment and each initial test handoff.
These boundaries make the candidate available; they do not establish live conformance.

The first bounded live observation against published TASK-295 revision 2
(`49734b00e7fab8da35d56f3f802c09b8aabc8b75`) stopped at
`UNPROVED/INSERT_BLOCKED`, with no generation in the bounded result and no
AIOS-owned submit attempt. Page-scoped Human challenge proof and bounded envelope
insertion had been reached. Read-only structural diagnostics found the same
regular-Chat composer, its unique enclosing form, and exactly one visible enabled
scoped Send; asynchronous editor reconciliation expanded the child structure and
made `innerText` diverge from `textContent`.

This is a bounded pre-submit Human/Brain conformance finding. It creates no
canonical RUN failure, REVIEW finding, publication failure, or engineering terminal
state. No raw draft, DOM diagnostic, conversation identity, endpoint, registry,
transcript, or assistant output is recorded here. Manual Send is not a contract
fallback or an authorized workaround. TASK-296 corrects production editor
reconciliation only; it does not change wake delivery, origin authority, routing,
or lifecycle authority. The full A/A/B/B comparison, continuity check, and ambiguity
trial below remain pending after exact reviewed publication of the correction.

1. Open two distinct regular conversations **A** and **B** in the same repository/
   Project context. Identify the surfaces directly as Human observation labels;
   keep raw URLs, UUIDs, profile/endpoint details, and transcripts machine-locally.
2. In A, prepare a bounded Human-approved initial handoff. Start one helper session
   and click A's in-page button. Read its actual bounded JSON result as **A1**;
   require `SUBMITTED/ACCEPTED`, a valid route handle, and positive generation.
   No assistant prose supplies the comparison. After the surface is idle, prepare
   a second deliberate approved handoff, start a new session, and click A again:
   record **A2**. Require the same handle and generation.
3. Repeat those two deliberate bootstraps in B to obtain **B1**, **B2**. Require
   B1 = B2 and A1 != B1. A and B remain distinct despite their same repository.
   No repository-default binding is consulted.
4. In a separately bounded continuity check, validly reload/reopen A (and, if
   authorized, open another tab of A), then bootstrap once with a fresh in-page
   gesture. Require route A and its unchanged generation. A second tab never
   creates a competing conversation route or substitutes for the selected document.
5. In one separately authorized ambiguity trial, start with
   `--proof-delay-ms 3000`; click the selected document's button and replace/navigate
   that page before proof, or click buttons in two eligible documents during that
   delay. Require a fixed `UNPROVED` outcome and no submission. Challenge absence,
   expiration, non-regular surfaces, or unclear provenance likewise leave the gate
   unproved. Do not retry until a desired comparison appears. Ambiguous post-submit
   state requires Human reconciliation and never triggers automatic resend.
6. Inspect only machine-local binding state for confidentiality, without exporting
   raw values. Record canonically only the exact published SHA, contract version,
   A/B labels, bounded statuses, generation/equality/difference booleans, continuity
   and ambiguity booleans, and Human/Brain's bounded conformance decision. No raw
   conversation identity, endpoint, local path, draft, transcript, screenshots of
   raw state, or request dumps enter TASK/RUN/RESULT/FAILURE/REVIEW/roadmap/EVIDENCE.
7. Human/Brain reconciles this real observation with the two-stage architecture
   audit and explicitly decides H4C0 closure or fallback. Any missing comparison,
   unstable route, shared A/B handle, unproved continuity, ambiguous provenance,
   or failed confidentiality/safety observation keeps H4C0 open.

## 10. Implementation verification and closure limits

`tests/test_origin_bootstrap.py` defines focused deterministic coverage for stable/
distinct conversation handles, same-conversation tabs, exclusive local binding,
reload/Project-location continuity, exact ordering, uncertain writes, generation/
registry races, raw-identity output exclusion, and no lifecycle/wake calls. Its
synthetic DOM harness executes the actual page closure for trusted/untrusted gesture,
copied challenge, expiry, changed document/route, non-regular surface, edit integrity,
scoped Send controls, single-use click, cleanup, and fresh rearm. Node is required for
that harness; absence is an explicit skip, not live proof. Existing local-wake tests
remain the regression contract for its unchanged delivery primitives.

TASK-296 adds an asynchronous synthetic editor update after successful native
insertion/input notification. The regressions require exact allowlisted multi-node
reconciliation with divergent rendered/DOM text before readiness succeeds. They
cover P/DIV, inline BR, empty/trailing padding, literal whitespace/Unicode/CRLF,
and fail-closed missing/extra/reordered separators, changed draft/envelope bytes,
hidden/duplicated content, rich/unknown/decorated nodes, bounded traversal,
ambiguous composer/form/Send, hidden/disabled Send, changed page/challenge, and
binding/generation races. Revalidation regressions also cover draft, form, and
Send replacement between accepted readiness, attempt intent, and click. These are
implementation regression definitions; their canonical execution and EVIDENCE
belong to Runtime.

Runtime alone runs canonical verification and constructs EVIDENCE. Deterministic
tests, Runtime PASS, Reviewer PASS, and publication of TASK-293, TASK-295, or TASK-296 alone do **not** close
H4C0 or authorize H4C1. The real two-regular-chat procedure above is a later Human/
Brain conformance decision. H4C0 remains the unique canonical NEXT, ahead of H4C1,
H4D, H4E, final H4B production-shape proof, and H5; no phase advances automatically.
