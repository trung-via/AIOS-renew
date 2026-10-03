# AIOS H4C0 Origin Capture Conformance v1

Status: IMPLEMENTATION CONTRACT / LIVE OBSERVATION UNPROVED  
TASK binding: TASK-292 revision 1  
Parent: [H4 Origin-Affine Return and Unattended Local Wake](AIOS-H4-ORIGIN-AFFINE-UNATTENDED-WAKE-v1.md)  
Authority: Runtime verifies implementation; Human/Brain evaluates later live feasibility.

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
