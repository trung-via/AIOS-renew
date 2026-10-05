# AIOS H4C0 Origin Capture Conformance v1

Status: PAGE-SCOPED FALLBACK CONTRACT / LIVE OBSERVATION UNPROVED

TASK bindings: TASK-292 revision 1 (preserved prior candidate); TASK-293 revision 1 (selected fallback); TASK-295 revision 2 (published insertion correction); TASK-296 revision 1 (published bounded editor-reconciliation correction); TASK-297 revision 1 (published literal-paste wrapper and insert-to-ready correction); TASK-298 revision 1 (published opt-in diagnostic attribution); TASK-299 revision 1 (published exact marked empty-paragraph correction); TASK-300 revision 1 (one-LF empty-editor post-submit correction)
Parent: [H4 Origin-Affine Return and Unattended Local Wake](AIOS-H4-ORIGIN-AFFINE-UNATTENDED-WAKE-v1.md)  
Authority: Runtime verifies implementation; Human/Brain evaluates later live feasibility.

Sections 1–5 preserve the TASK-292 OpenAI-session candidate and its historical
contract. Its accepted implementation, REVIEW/PASS, and exact publication remain
valid evidence. The current Human-observed regular-Chat surface did not expose
the Developer Mode/custom MCP entry for that candidate's planned live procedure;
that availability observation does not invalidate its engineering lineage.
Sections 6–13 define the selected page-scoped fallback, its bounded corrections,
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
whose only children are text, the exact literal-paste wrapper, or explicit BR leaves.
The wrapper is only `SPAN[data-prompt-literal-paste=""]`: exactly one attribute
named `data-prompt-literal-paste` with the empty string value, exactly one child
node, and that child exactly a `#text` node. It is accepted only as a direct child
of a bounded P/DIV block, never as a root or nested/rich inline grammar. Wrong or
additional attributes, non-empty values, zero/multiple children, non-text children,
nesting, hidden/aria-hidden/style/class decoration, or invisible wrapper content
fail closed. Each wrapper and its text leaf count against the traversal bound.
Blocks carry no attributes except the exact observed empty-paragraph root:
`P[data-empty-paragraph="true"]` with exactly that one attribute, empty
`textContent`, and exactly one child `BR` with exactly
`class="ProseMirror-trailingBreak"`. The existing BR checks still require no
children, empty `textContent`, and visibility; the marked P must also be visible.
This exception never applies to DIV, non-empty P, a missing/non-`true` marker
value, additional root attributes, or wrong/multiple/absent children.
BR leaves are bare or carry exactly `class="ProseMirror-trailingBreak"`. Every
block boundary contributes exactly one logical newline and every inline bare BR
contributes one. A sole BR is an empty-block placeholder; the named trailing BR
contributes no bytes only at the end of an empty block or after a proved newline.
No other nesting, nodes, or attributes are accepted, including rich content whose
aggregate text happens to match. Hidden, collapsed, or transparent block/BR
or literal-paste wrapper content fails closed. Traversal is bounded by the staged
logical draft length.

The derived logical text must equal the immutable Human draft plus exactly two
newline characters plus the bounded envelope. `textContent` must equal the
allowlisted text leaves. `innerText` may differ only by one of six explicit block
layout projections: uniformly zero, one, or two rendered newlines at proved block
boundaries, with empty BR placeholders uniformly rendered as zero or one newline.
Those display projections do not add, remove, or reorder logical separators.
Literal CRLF, Unicode, and whitespace bytes within text leaves remain exact.
Unknown or non-exact representations remain blocked even if visually similar.

The application's input event may reconcile that same composer asynchronously.
`insert()` establishes that the bounded native append succeeded and the exact
page/challenge/surface still holds. It does not require the final reconciled
representation synchronously at native insertion return.
Readiness polls only within its existing three-second bound and re-evaluates the
same staged composer, document, challenge, page, and exact logical draft. Waiting
itself supplies no equivalence or origin authority. A representation that never
reconciles exactly inside that window remains pre-submit `UNPROVED/INSERT_BLOCKED`;
no `ATTEMPTING` intent is written and no Send click occurs. The first accepted readiness
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
structural submission witness. The retained same composer must have `textContent`
exactly empty. The existing `innerText` exactly empty case remains accepted.
TASK-300 adds only `innerText` exactly one LF with exactly one visible P root,
empty root `textContent`, and exactly one visible child BR carrying exactly
`class="ProseMirror-trailingBreak"`, no children, and empty `textContent`.
Root attributes are not origin or content authority for this post-submit check;
the exact count/tag, empty logical content, sole BR grammar, and visibility close
the structure. This does not broaden the pre-submit attributed-root allowlist.
For the one-LF path, DIV, root text, multiple roots, missing/multiple children, bare/wrong/decorated/
hidden BR, non-empty content, spaces or other whitespace, and multiple LFs fail
closed. Every other post-submit operand is unchanged, including document/route,
regular surface, unique retained composer, and scoped Send count/enabledness.
An uncertain click, witness, completion write, or
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

The appended envelope is at most 512 ASCII bytes after TASK-309's bounded proof addition:

```text
[AIOS ORIGIN BOOTSTRAP]
{"contract":"PAGE_SCOPED_AIOS_SEND_ORIGIN_BOOTSTRAP_V1","route_handle":"page-origin-v1:<64 lowercase hex digits>","generation":1,"authoring_proof":"origin-authoring-v1:<64 lowercase hex digits>"}
[/AIOS ORIGIN BOOTSTRAP]
```

Default console results contain only `contract`, `status`, `reason`, `route_handle`,
`generation`, and `authoring_proof`. Status is `SUBMITTED`, `UNPROVED`, or `AMBIGUOUS`; rejected/ambiguous
results expose null handle/generation/proof and fixed reason codes. Raw URLs/UUIDs,
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
Publisher publication of the exact reviewed TASK-300 correction. Human/Brain
separately decides historical ambiguous-attempt disposition and whether a fresh
bounded observation is authorized, as described in section 13. Human/Brain selects
that published SHA and authorizes the existing environment and each initial handoff.
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

After TASK-296 revision 1 completed RUN-296-001 Runtime PASS, REVIEW-296-001 PASS,
and exact publication of `7037ce457471c230536749cf8e591e21720c0782`, the resumed
bounded A1 observation again stopped pre-submit at `UNPROVED/INSERT_BLOCKED` with
no AIOS-owned submit attempt. Read-only structural diagnostics established one
visible composer, one enclosing form, one visible enabled form-scoped Send, and
five attribute-free P root blocks. Four non-empty branches were exactly
`P > SPAN[data-prompt-literal-paste=""] > #text`; each SPAN had only that one
empty-valued attribute and exactly one text child. The remaining empty separator
branch was `P > BR.ProseMirror-trailingBreak`, with exactly
`class="ProseMirror-trailingBreak"` on the childless BR.

This second observation is bounded pre-submit Human/Brain planning evidence for
TASK-297. It creates no canonical RUN failure, REVIEW finding, publication failure,
or engineering terminal state and records no raw identity, draft, DOM payload,
endpoint, registry, transcript, or assistant output. TASK-297 addresses only this
exact wrapper grammar and the premature synchronous reconciliation check at
insert return. It supplies no generic SPAN/rich-text acceptance, normalization,
manual-Send fallback, wake delivery, routing, or new authority. Runtime verification,
a fresh Reviewer verdict, and exact reviewed publication completed for TASK-297 as
recorded in section 11. The entire A/A/B/B comparison, continuity check, and ambiguity
trial below remain pending. Section 13 records the post-TASK-299 observations and
historical consumed ambiguous attempts. Before continuation, Human/Brain separately
decides their disposition and whether a fresh bounded observation is authorized
after TASK-300 publication. This numbered procedure does not authorize retrying
A1 or D1. H4C0 remains open and H4C1 remains blocked.

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

TASK-297 adds the observed five-block production fixture: four exact literal-paste
SPAN/text branches and one empty P/trailing-BR branch. A transient unallowlisted
immediate post-input shape is rejected by readiness and becomes exact only during
later ready polling. Focused regression definitions cover successful delayed
reconciliation before intent, a never-reconciled edit reaching the existing
three-second `INSERT_BLOCKED` bound without intent or submit, and rejection of
wrapper attribute/value/child deviations, nested/rich/hidden/decorated content,
unsupported BRs, byte or separator changes, control ambiguity, page/challenge
changes, and binding/generation races. The reconciled draft and retained scoped
control remain revalidated before `ATTEMPTING` and immediately before click.
These definitions claim no canonical verification or live conformance result.

TASK-299 adds the exact marked empty P beside the existing four literal-paste
branches. Its focused positive regression requires readiness, retained scoped
controls, and a single consumed click in both ordinary and diagnostic modes.
Negative definitions cover DIV/other tags, wrong marker name, missing/wrong value,
extra root attributes, non-empty marked P, wrong/multiple/absent children, BR
decoration/children, and invisible roots or BRs. The marked fixture also exercises
the existing rich-DOM, byte/separator, wrapper visibility, traversal, control,
page/challenge, binding, bounded-ready, and pre-intent/pre-click regressions.
These are regression definitions; Runtime owns their canonical execution.

TASK-300 adds focused post-click fixture definitions for both exactly empty
rendered text and the sole one-LF P/trailing-BR shape, including roots with
attributes that confer no origin or content authority. Negative definitions cover
root/child count and node types, non-empty root/BR/composer text, wrong/decorated/
child-bearing BRs, hidden or invisible roots/BRs, other whitespace and multiple
LFs, document/route changes, composer ambiguity/replacement, and scoped Send/form
failures. They retain the pre-submit exact-text/readiness regressions, single-click
consumption, submission-unproven ambiguity, and no-blind-resend definitions.
These definitions claim no Runtime verification or live conformance result.

Runtime alone runs canonical verification and constructs EVIDENCE. Deterministic
tests, Runtime PASS, Reviewer PASS, and publication of TASK-293, TASK-295, TASK-296, TASK-297, TASK-298, TASK-299, or TASK-300 alone do **not** close
H4C0 or authorize H4C1. The real two-regular-chat procedure above is a later Human/
Brain conformance decision. H4C0 remains the unique canonical NEXT, ahead of H4C1,
H4D, H4E, final H4B production-shape proof, and H5; no phase advances automatically.

## 11. Post-TASK-297 evidence and diagnostic-only TASK-298

TASK-297 revision 1 completed RUN-297-001 Runtime PASS, REVIEW-297-001 PASS,
exact publication of `295b7ad63b7dd6a9417ec0d46d115013d2f3e10f`, and subsequent
Human/Brain roadmap reconciliation. A fresh instrumented A1 on that published
subject still ended `UNPROVED/INSERT_BLOCKED`, with no AIOS-owned submission.
This is Human/Brain live-conformance planning evidence only: it is not a RUN,
FAILURE, REVIEW finding, publication failure, or engineering terminal state.

The bounded observations were:

- Bootstrap insertion and the production DOM grammar were present by approximately
  111 ms. One visible composer retained its identity, with one enclosing form and
  one visible enabled form-scoped Send. Stop was absent.
- Four branches had the exact `P > SPAN[data-prompt-literal-paste=""] > #text`
  grammar; the empty branch had `P > BR.ProseMirror-trailingBreak`. The wrappers
  were visible and had layout rectangles.
- Final rendered text had length 271, with newline runs at offset/length
  `47/5`, `75/2`, and `245/2`. This matches TASK-297's two-LF block-boundary
  projection with the empty-block placeholder; no text or raw DOM is retained here.
- Handshake cleanup occurred around 3161 ms, consistent with entry into and
  exhaustion of the existing three-second READY polling window. The challenge
  TTL is 30000 ms, so challenge expiry alone does not explain that failure.

These observations rule out late editor settlement, selector drift, composer
replacement, Send readiness, wrapper grammar/visibility, and rendered newline
projection as primary explanations for this bounded attempt. They do not reveal
the internal first failing readiness operand, including sticky invalidation.
**Timeout-extension and DOM-selector fixes are explicitly retracted as unproven.**
No root-cause correction, invalidation reset, predicate weakening, longer wait,
retry, manual Send fallback, or alternative acceptance is authorized by TASK-298.

The explicit opt-in entry is the same development helper with `--diagnostic`:

```powershell
python scripts/aios_origin_bootstrap.py --state $originRegistry --endpoint http://127.0.0.1:9222 --seconds 120 --diagnostic
```

This example is for a later Human-authorized observation against exact reviewed
publication, using the already authorized local environment from section 8.
It does not authorize an Executor live observation or operational-state reset.
The default invocation continues to emit exactly the ordinary five-field
`PAGE_SCOPED_AIOS_SEND_ORIGIN_BOOTSTRAP_V1` result. Diagnostic mode emits one JSON
object under `PAGE_SCOPED_AIOS_SEND_ORIGIN_BOOTSTRAP_DIAGNOSTIC_V1`, containing
`contract`, `result` (the unchanged ordinary result), and `diagnostic`:

| Field | Closed meaning |
| --- | --- |
| `phase` | `NOT_REACHED`, `INSERT`, or `READY`; the last reached attribution phase |
| `category` | null before attribution or after an accepted operation, `INSERT_REJECTED` on an ordinary false insert return, or a READY gate below; `UNKNOWN` only for unclassified internal failure |
| `proof_category` | null except when READY's `PROOF` gate blocks; then one proof subcategory below |
| `ready` | Whether the last existing READY poll accepted; false before READY |
| `exhausted` | Whether the existing three-second READY window exhausted; no extension or extra poll |
| `observed_categories` | Sorted distinct blocking READY gate enums observed during those existing polls, bounded to seven entries |

The first blocking READY category is one of `PROOF`,
`SURFACE_OR_COMPOSER_IDENTITY`, `EXACT_TEXT`, `SEND_SCOPE_OR_COUNT`,
`SEND_ENABLED`, or `RETAINED_FORM_OR_CONTROL_IDENTITY`. The seventh enum,
`UNKNOWN`, covers an inaccessible/internal failure or malformed diagnostic return
and never permits submission. `PROOF` (the READY_PROOF gate) is further classified
as `SPENT`, `INVALIDATED`, `DOCUMENT_IDENTITY`, `HELPER_BUTTON_CONNECTIVITY`,
`SLOT_OR_NONCE_BINDING`, `PENDING_OR_CHALLENGE_BINDING`, `ROUTE_EQUALITY`,
`DEADLINE`, or `SURFACE_PROOF`. Both route equalities retain their original order;
nonce/slot checks and pending/challenge checks share only their bounded label.
The proof's surface check can block before the later surface/composer identity
gate; external DOM observations do not override that original ordering.

The browser classification observes the operands already evaluated by the
published short-circuit predicates. That same call supplies the boolean poll
decision. There is no second diagnostic predicate pass, DOM mutation, focus
change, click, retry, navigation, extra wait, or registry operation. The three-second
window, 50 ms poll cadence, challenge TTL, exact-text grammar, Send/form guards,
retained identities, and consume-before-single-click boundary are preserved.
INSERT rejection and READY exhaustion retain ordinary `UNPROVED/INSERT_BLOCKED`.
If readiness succeeds but a subsequent proof/intent/click/witness fails, `result`
remains authoritative; `ready: true` describes only the completed READY polling
phase. Ambiguous attempts still require Human reconciliation and cannot resend.

Only the contract identifier, original bounded result fields, fixed phase/gate
enums, two booleans, and the seven-member-bounded enum set leave the diagnostic
boundary. Failure/ambiguity keeps the ordinary route handle and generation null.
No URL/UUID, endpoint, provider/session/account identity, nonce, challenge, draft,
DOM text/content, selector-matched content, registry dump, per-poll time/count,
arbitrary exception string, or event trace is emitted or persisted. The diagnostic
state is ephemeral and does not enter the local registry. Historical timing and
newline positions above are supplied planning observations, not diagnostic fields.

Focused regression definitions compare both modes to frozen TASK-297 predicates,
including every proof subcategory, every READY category, simultaneous failures
and first-gate ordering, INSERT rejection before authorized edit, success, and
never-ready exhaustion. They compare predicate-call order, focus/edit/input/click
counts, waits and poll cadence, ordinary status/reason, durable registry writes,
attempt markers, and no-blind-resend behavior. The literal-paste/BR success fixture
reproduces length 271 and exactly `47/5`, `75/2`, `245/2` using synthetic bytes;
the production grammar is unchanged. These are regression definitions, not
Executor canonical verification or live conformance EVIDENCE.

TASK-298 revision 1 completed RUN-298-001 Runtime PASS, REVIEW-298-001 PASS, and
exact publication of `8d2bf871f559e5d021c42e5c5765d0e8a7600017`. Its bounded
post-publication A1 and the supplied same-attempt planning attribution are
recorded in section 12. TASK-298 changed no lifecycle or roadmap state and
provided no H4C1 routing, wake delivery, later phase, or publication authority.

## 12. Post-TASK-298 planning facts and exact-root TASK-299

The bounded A1 against published TASK-298 ended pre-submit as
`UNPROVED/INSERT_BLOCKED`: diagnostic phase `READY`, category `EXACT_TEXT`,
`exhausted: true`, and no AIOS-owned submit attempt. One fresh bounded A1 retry
supplied matching same-attempt observations: the production helper remained
`READY/EXACT_TEXT` through the existing window, while a concurrent read-only
observer stably attributed the internal exact-text root subgate to
`ROOT_ATTRIBUTES`. That observer attribution is a supplied planning fact, not a
new production diagnostic enum or output field.

Read-only inspection of that same failed composer established exactly one root
offender: `P` with exactly one attribute `data-empty-paragraph="true"`, empty
`textContent`, and exactly one child `BR.ProseMirror-trailingBreak`. The BR had
exactly `class="ProseMirror-trailingBreak"`, no children, and empty `textContent`.
The attribute-free root predicate rejected this P before reaching its existing
trailing-BR grammar. These bounded structural facts supply TASK-299's correction
basis; no raw identity, route handle, challenge/nonce, draft, DOM payload, endpoint,
registry, transcript, or assistant output is retained here.

These probes are Human/Brain planning evidence only, not a RUN, FAILURE, REVIEW
finding, publication failure, or Runtime verification. TASK-299 admits only that
exact marked empty P and retains the existing literal-paste wrapper grammar,
raw/textContent and logical-byte equality, rendered projection, visibility,
traversal budget, page/challenge/origin proof, scoped Send/form checks, diagnostic
ordering, three-second window, 50 ms cadence, challenge TTL, and
consume-before-single-click/no-blind-resend behavior. It authorizes no Executor
live observation, operational-state reset, roadmap change, semantic review, or
publication.

TASK-299 revision 1 completed RUN-299-001 Runtime PASS, REVIEW-299-001 PASS, and
exact publication of `5f5fbdabd0f77d5d0d8a9e2309b1472d19744b27`. The separately
authorized post-publication A1 and later bounded observations are recorded in
section 13. Implementation, tests, review, and publication supply no live closure:
**H4C0 stays open and H4C1 stays blocked**. The remaining two-chat comparison,
continuity/ambiguity proof, and Human/Brain closure decision remain required.
These observations do not automatically authorize another execution or later phase.

## 13. Post-TASK-299 bounded planning facts and one-LF TASK-300

The single authorized post-TASK-299 A1 progressed beyond the submission-attempt
boundary and ended `AMBIGUOUS/SUBMISSION_UNPROVEN`. Human observation confirmed
that a sent user turn became present. That observation does not retroactively
reclassify A1 as `SUBMITTED/ACCEPTED` or resolve its consumed ambiguous attempt.

A separately authorized disposable D1 reproduced
`AMBIGUOUS/SUBMISSION_UNPROVEN`. Its concurrent read-only observer stably reported
only `COMPOSER_NOT_EMPTY` in the settled post-submit tail, with
`STRUCTURAL_PASS_SEEN` false. This same-attempt attribution is a supplied bounded
planning fact, not a production diagnostic enum, a Runtime result, or permission
to retry.

A later read-only probe over the already-consumed ambiguous routes observed two
ambiguous routes but only one currently open page. That page's retained composer
had `textContent` EMPTY, `innerText` ONE_LF, exactly one P root with empty text,
and a sole BR child with exactly `class="ProseMirror-trailingBreak"`, no children,
and empty text. This is a **1-of-2 observation**: it does not establish that both
A1 and D1 had that final DOM shape, or that the shape was present throughout
either earlier submission-proof window. The pre-TASK-300 predicate rejected this
representation at its exact-empty `innerText` equality despite empty `textContent`.

These are privacy-safe Human/Brain planning facts only. They supply the bounded
prospective compatibility basis for TASK-300; they are not RUN, FAILURE, REVIEW,
publication, Runtime verification, or live closure truth. No raw conversation
URL/UUID, route handle, challenge/nonce, endpoint, registry contents, draft or
bootstrap text, transcript, assistant output, or raw DOM payload is recorded here.

TASK-300 adds only the exact one-LF post-submit shape defined in section 6. It
preserves the fully-empty case and every pre-submit exactText/readiness rule,
including TASK-299's attributed-root allowlist. All other post-submit proof
operands, three-second submission and READY windows, 50 ms poll cadence,
challenge TTL, selectors, scoped Send/form logic, consume-before-click ordering,
registry generation and attempt transitions, and no-blind-resend behavior remain
unchanged. It authorizes no Executor live observation, ambiguous-state reset,
roadmap mutation, semantic review, or publication.

Runtime owns canonical verification and EVIDENCE, Reviewer owns a fresh semantic
verdict, and Publisher owns exact reviewed publication. After publication,
Human/Brain separately decides disposition of the historical A1/D1 ambiguous
attempts and whether a fresh bounded live observation is authorized. Those
historical attempts remain unresolved pending that disposition; publication of
TASK-300 alone cannot relabel, replay, resend, or convert them to `SUBMITTED`.
**H4C0 remains open and H4C1 remains blocked.** H4C0 planning, its remaining
two-chat/continuity/ambiguity proof and closure decision, any later live proof,
and later lifecycle work remain downstream authority-owned facts. No automatic
TASK-301 or roadmap advance follows this correction.

## 14. Bounded authoring proof addition (TASK-309)

H4C0 now emits `authoring_proof` with grammar
`origin-authoring-v1:<64 lowercase hexadecimal digits>`. It is a fresh random
capability from the already proved exact-document bootstrap boundary, not a
derived chat identity or a caller-supplied selector. After registry durability
and the first exact-page revalidation, bootstrap creates the proof's external
record before appending metadata. The record binds the opaque route handle,
generation, planned bootstrap-attempt id and a fixed one-hour validity interval.
The same id is used for the existing pre-Send `ATTEMPTING` marker. Admission
requires that exact attempt to have completed as `SUBMITTED`; a staged token in
an unsubmitted/ambiguous draft is unusable. No successful authoring proof is
returned on a rejected or ambiguous bootstrap. Existing in-document challenge,
draft, unique retained form/Send, navigation, generation and single-click checks
are unchanged. The browser metadata bound alone increases from 384 to 512 ASCII
bytes to carry the proof; draft interpretation and rich-editor grammar do not grow.

The sidecar is the fixed sibling `<registry file>.authoring`, with its own
exclusive `.lock` and `.pending` barriers, outside Git stores and working trees.
It uses the existing durable state replacement primitive. The original origin
registry's schema and wake attempt markers are unchanged. The sidecar stores at
most 256 proof digests and 262,144 bytes; it stores no URL/UUID, endpoint, raw
draft, transcript, challenge or account credential. A consumed record holds only
bounded operational admission selectors and their signature. A new successful
bootstrap supersedes the old proof by replacing the bootstrap-attempt marker,
without changing route ownership or generation. Exhaustion, stale locks,
uncertain writes or malformed/duplicate state fail closed and require Human
handling; there is no automatic pruning or resend.

`src/aios_renew/origin_authoring_proof.py` implements local proof admission.
The production self-hosted provenance job reads only the machine-owned
`AIOS_ORIGIN_REGISTRY` setting and that sidecar, and validates the unique exact
route/generation plus successful attempt while holding both locks. It consumes
the proof for the exact carrier attempt, TASK id, expected main and envelope
digest. Same-attempt replay is idempotent; mismatch or another attempt fails.
The authenticated receipt exports only bounded opaque operational selectors,
validity times and an HMAC signature. The deployment-owned 256-bit key is
provisioned as `AIOS_ORIGIN_ADMISSION_KEY` for the self-hosted admission and hosted
consumption steps; neither the key nor local registry contents are exported.
The hosted ingress job authenticates the receipt, never pretends to validate
machine-local state, and fails before TASK mutation if admission is unavailable.
See [TASK-309's H4C1 gate](AIOS-H4-ORIGIN-AFFINE-UNATTENDED-WAKE-v1.md#new-task-origin-provenance-gate-task-309)
for the exact production carrier and revision/legacy boundaries.

Regression definitions cover A-to-A and B-to-B, crossed proof/selector rejection,
missing/expired/wrong-generation/ambiguous proof, changed bootstrap, exact-attempt
replay and reuse rejection, authenticated hosted consumption, pre-mutation
failure, revision preservation and explicit legacy separation. Runtime owns
their canonical execution and EVIDENCE. This addition records no Runtime PASS,
Reviewer verdict, publication, TASK-308 resolution, TASK-303 resume, H4/H5 closure,
or roadmap advancement, and changes no historical TASK/RUN lineage.
