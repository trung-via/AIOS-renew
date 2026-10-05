"""Page-scoped Human origin attestation; local selectors, no lifecycle authority.

The browser adapter is an attach-only trusted local boundary. Challenges are
created by trusted in-document clicks, never supplied as CLI/tool arguments.
Raw routes and endpoints terminate in the external registry. Draft bytes stay
ephemeral inside the selected document and are used only for edit integrity.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import logging
import os
import re
import secrets
import sys
import time

from .return_affinity import HANDLE_PREFIX, HANDLE, MAX_GENERATION
from . import local_chat_wake as wake

CONTRACT = "PAGE_SCOPED_AIOS_SEND_ORIGIN_BOOTSTRAP_V1"
DIAGNOSTIC_CONTRACT = "PAGE_SCOPED_AIOS_SEND_ORIGIN_BOOTSTRAP_DIAGNOSTIC_V1"
READY_CATEGORIES = frozenset({
    "PROOF", "SURFACE_OR_COMPOSER_IDENTITY", "EXACT_TEXT",
    "SEND_SCOPE_OR_COUNT", "SEND_ENABLED", "RETAINED_FORM_OR_CONTROL_IDENTITY",
    "UNKNOWN",
})
PROOF_CATEGORIES = frozenset({
    "SPENT", "INVALIDATED", "DOCUMENT_IDENTITY", "HELPER_BUTTON_CONNECTIVITY",
    "SLOT_OR_NONCE_BINDING", "PENDING_OR_CHALLENGE_BINDING", "ROUTE_EQUALITY",
    "DEADLINE", "SURFACE_PROOF",
})
NONCE = re.compile(r"[0-9a-f]{64}")
MAX_ROUTES = 256
MAX_REGISTRY_BYTES = 262144
MAX_PAGES = 32
MAX_CONTEXTS = 8
CHALLENGE_TTL_MS = 30000
MAX_DRAFT_CHARS = 65536
MAX_ENVELOPE_BYTES = 512
MAX_ROUTE_CHARS = 512
MAX_ENDPOINT_CHARS = 128
SLOT = "__aiosPageOriginBootstrapV1"
REASONS = frozenset({
    "ACCEPTED", "INVALID_INPUT", "LOCAL_FAILURE", "CHALLENGE_UNPROVED",
    "CHALLENGE_NOT_UNIQUE", "TARGET_PAGE_CHANGED", "SURFACE_UNPROVEN",
    "REGISTRY_CONFLICT", "BINDING_GENERATION_CHANGED", "INSERT_BLOCKED",
    "SEND_BLOCKED", "SUBMISSION_UNPROVEN", "ATTEMPT_REQUIRES_HUMAN",
    "STATE_CAPACITY_REQUIRES_HUMAN", "STATE_LOCKED_OR_UNAVAILABLE",
    "STATE_WRITE_UNCERTAIN", "STATE_AMBIGUOUS", "INVALID_LOCAL_PATH",
    "CONFIG_OR_STATE_IN_REPOSITORY", "INVALID_LOCAL_ENDPOINT",
    "LOCAL_METADATA_INVALID", "INVALID_CHAT_BINDING",
})


class BootstrapBlocked(Exception):
    def __init__(self, reason):
        super().__init__(reason if reason in REASONS else "LOCAL_FAILURE")


@dataclass(frozen=True)
class BootstrapResult:
    status: str
    reason: str
    route_handle: str | None = None
    generation: int | None = None
    authoring_proof: str | None = None

    def as_dict(self):
        return dict(contract=CONTRACT, status=self.status, reason=self.reason,
                    route_handle=self.route_handle, generation=self.generation,
                    authoring_proof=self.authoring_proof)


@dataclass
class BootstrapDiagnostics:
    """Ephemeral fixed-enum attribution, never an attempt or acceptance authority.

    READY observations come from the same call that decides that poll. Retain only
    the last decision and a set bounded by the seven repository-owned categories.
    No identities, DOM values, exception strings, poll times or counts are stored.
    """

    phase: str = "NOT_REACHED"
    category: str | None = None
    proof_category: str | None = None
    ready: bool = False
    exhausted: bool = False
    observed_categories: set[str] = field(default_factory=set, repr=False)

    def insert_started(self):
        self.phase, self.category = "INSERT", "UNKNOWN"

    def insert_returned(self, accepted):
        self.category = None if accepted else "INSERT_REJECTED"

    def ready_started(self):
        self.phase, self.category = "READY", "UNKNOWN"

    def ready_observed(self, value):
        # Validate the entire finite grammar before copying any browser output.
        valid = (type(value) is dict and set(value) == {"ready", "category", "proof_category"}
                 and type(value["ready"]) is bool
                 and (value["category"] is None or
                      type(value["category"]) is str and value["category"] in READY_CATEGORIES)
                 and (value["proof_category"] is None or
                      type(value["proof_category"]) is str and value["proof_category"] in PROOF_CATEGORIES))
        if valid:
            valid = ((value["ready"] and value["category"] is None and value["proof_category"] is None)
                     or (not value["ready"] and value["category"] in READY_CATEGORIES
                         and ((value["category"] == "PROOF" and value["proof_category"] in PROOF_CATEGORIES)
                              or (value["category"] != "PROOF" and value["proof_category"] is None))))
        if not valid:
            self.ready_unknown()
            raise BootstrapBlocked("LOCAL_FAILURE")
        self.ready = value["ready"]
        self.category, self.proof_category = value["category"], value["proof_category"]
        if self.category is not None:
            self.observed_categories.add(self.category)
        return self.ready

    def ready_unknown(self):
        self.ready, self.category, self.proof_category = False, "UNKNOWN", None
        self.observed_categories.add("UNKNOWN")

    def as_dict(self, result):
        return dict(contract=DIAGNOSTIC_CONTRACT, result=result.as_dict(), diagnostic=dict(
            phase=self.phase, category=self.category, proof_category=self.proof_category,
            ready=self.ready, exhausted=self.exhausted,
            observed_categories=sorted(self.observed_categories)))


@dataclass(frozen=True)
class PageProof:
    # Never include operational identity in repr/diagnostics/result packages.
    page: object = field(repr=False)
    chat_url: str = field(repr=False)
    cdp_endpoint: str = field(repr=False)
    challenge: str = field(repr=False)
    document_nonce: str = field(repr=False)
    navigation_epoch: int = field(default=0, repr=False)


def conversation_key(url):
    """Project prefixes are location, while the conversation UUID is identity."""
    if type(url) is not str or not 1 <= len(url) <= MAX_ROUTE_CHARS:
        raise BootstrapBlocked("INVALID_CHAT_BINDING")
    normalized = wake.normalize_chat(url)
    return "https://chatgpt.com/c/" + normalized.rsplit("/", 1)[-1]


def local_endpoint(endpoint, state_path):
    # Reuse the wake adapter's loopback-only binding grammar, without admitting
    # a repository lane or using its registry/queue/delivery behavior.
    if type(endpoint) is not str or not 1 <= len(endpoint) <= MAX_ENDPOINT_CHARS:
        raise BootstrapBlocked("INVALID_LOCAL_ENDPOINT")
    return wake._binding(
        dict(chat_url="https://chatgpt.com/c/00000000-0000-0000-0000-000000000000",
             cdp_endpoint=endpoint, state_path=str(state_path)),
        state_path.with_name(state_path.name + ".config"), wake.REPOSITORY, 1,
    ).cdp_endpoint


def registry_path(value):
    path = wake.external_path(str(value))
    # The shared guard covers working trees (including .git indirection).
    # A bare Git store is also repository-owned state, never a registry home.
    if any((parent / "HEAD").is_file() and (parent / "objects").is_dir()
           and (parent / "config").is_file() for parent in (path, *path.parents)):
        raise BootstrapBlocked("CONFIG_OR_STATE_IN_REPOSITORY")
    return path


class OriginRegistry(wake.State):
    """Bounded local origin bindings, with one conservative attempt marker.

    This marker is not a transport queue: it has no payload, consumer, retry,
    wake event, semantic lineage, or route-resolution API. An uncertain attempt
    blocks future sends until explicit Human reconciliation of local state.
    """

    def __init__(self, path):
        self.path = registry_path(path)
        for suffix in (".lock", ".pending"):
            registry_path(self.path.with_name(self.path.name + suffix))

    def validate(self, data):
        registry_path(self.path)
        if (type(data) is not dict or set(data) != {"version", "routes"}
                or type(data["version"]) is not int or data["version"] != 1
                or type(data["routes"]) is not dict or len(data["routes"]) > MAX_ROUTES):
            raise BootstrapBlocked("STATE_AMBIGUOUS")
        handles = set()
        for key, item in data["routes"].items():
            if (type(item) is not dict
                    or set(item) != {"handle", "generation", "chat_url", "cdp_endpoint", "attempt"}
                    or type(item["handle"]) is not str or not HANDLE.fullmatch(item["handle"])
                    or type(item["generation"]) is not int
                    or not 1 <= item["generation"] <= MAX_GENERATION
                    or conversation_key(item["chat_url"]) != key
                    or wake.normalize_chat(item["chat_url"]) != item["chat_url"]):
                raise BootstrapBlocked("STATE_AMBIGUOUS")
            local_endpoint(item["cdp_endpoint"], self.path)
            if item["handle"] in handles:
                raise BootstrapBlocked("REGISTRY_CONFLICT")
            handles.add(item["handle"])
            attempt = item["attempt"]
            if attempt is not None and (
                    type(attempt) is not dict or set(attempt) != {"id", "status"}
                    or type(attempt["id"]) is not str or not NONCE.fullmatch(attempt["id"])
                    or attempt["status"] not in {"ATTEMPTING", "AMBIGUOUS", "SUBMITTED"}):
                raise BootstrapBlocked("STATE_AMBIGUOUS")
        if len(json.dumps(data).encode("utf-8")) > MAX_REGISTRY_BYTES:
            raise BootstrapBlocked("STATE_CAPACITY_REQUIRES_HUMAN")
        return data

    def read(self):
        if self.path.stat().st_size > MAX_REGISTRY_BYTES:
            raise BootstrapBlocked("STATE_CAPACITY_REQUIRES_HUMAN")
        return self.validate(wake.read_json(self.path))

    @contextmanager
    def locked(self):
        lock = self.path.with_name(self.path.name + ".lock")
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError:
            raise BootstrapBlocked("STATE_LOCKED_OR_UNAVAILABLE") from None
        try:
            os.close(fd)
            if self.path.with_name(self.path.name + ".pending").exists():
                raise BootstrapBlocked("STATE_WRITE_UNCERTAIN")
            yield self.read() if self.path.exists() else {"version": 1, "routes": {}}
        finally:
            lock.unlink()

    # State.write supplies exclusive pending creation, fsync, write-through
    # replacement, and directory fsync. No wake state or delivery is touched.

    def bind(self, data, proof):
        key = conversation_key(proof.chat_url)
        endpoint = local_endpoint(proof.cdp_endpoint, self.path)
        item = data["routes"].get(key)
        if item is None:
            if len(data["routes"]) >= MAX_ROUTES:
                raise BootstrapBlocked("STATE_CAPACITY_REQUIRES_HUMAN")
            handle = HANDLE_PREFIX + secrets.token_hex(32)
            if any(route["handle"] == handle for route in data["routes"].values()):
                raise BootstrapBlocked("REGISTRY_CONFLICT")
            item = data["routes"][key] = dict(handle=handle, generation=1,
                chat_url=wake.normalize_chat(proof.chat_url), cdp_endpoint=endpoint, attempt=None)
        elif item["cdp_endpoint"] != endpoint:
            # No automatic profile/endpoint ownership transfer or generation bump.
            raise BootstrapBlocked("REGISTRY_CONFLICT")
        elif item["attempt"] and item["attempt"]["status"] != "SUBMITTED":
            raise BootstrapBlocked("ATTEMPT_REQUIRES_HUMAN")
        item["chat_url"] = wake.normalize_chat(proof.chat_url)
        self.write(data)
        self.current(data, key)
        return key, item

    def current(self, expected, key):
        if self.path.with_name(self.path.name + ".pending").exists():
            raise BootstrapBlocked("STATE_WRITE_UNCERTAIN")
        actual = self.read()
        current = actual["routes"].get(key)
        if current and current["generation"] != expected["routes"][key]["generation"]:
            raise BootstrapBlocked("BINDING_GENERATION_CHANGED")
        if actual != expected:
            raise BootstrapBlocked("REGISTRY_CONFLICT")


def envelope(handle, generation, authoring_proof=None):
    if (type(handle) is not str or not HANDLE.fullmatch(handle)
            or type(generation) is not int or not 1 <= generation <= MAX_GENERATION):
        raise BootstrapBlocked("INVALID_INPUT")
    metadata = dict(contract=CONTRACT, route_handle=handle, generation=generation)
    if authoring_proof is not None:
        from .origin_authoring_proof import PROOF
        if type(authoring_proof) is not str or not PROOF.fullmatch(authoring_proof):
            raise BootstrapBlocked("INVALID_INPUT")
        metadata["authoring_proof"] = authoring_proof
    value = ("[AIOS ORIGIN BOOTSTRAP]\n" + json.dumps(
        metadata,
        separators=(",", ":")) + "\n[/AIOS ORIGIN BOOTSTRAP]")
    if len(value.encode("ascii")) > MAX_ENVELOPE_BYTES:
        raise BootstrapBlocked("INVALID_INPUT")
    return value


def bootstrap(registry, adapter):
    """One gesture, one attempt. All outputs have a fixed bounded grammar."""
    attempted = False
    proof = None
    try:
        proof = adapter.capture()  # Exact document proof precedes registry reads.
        with registry.locked() as data:
            key, item = registry.bind(data, proof)
            adapter.revalidate(proof)
            registry.current(data, key)
            from .origin_authoring_proof import issue_page_proof
            bootstrap_attempt = secrets.token_hex(32)
            authoring_proof = issue_page_proof(registry, item, bootstrap_attempt)
            metadata = envelope(item["handle"], item["generation"], authoring_proof)
            adapter.insert(proof, metadata)
            adapter.ready(proof)
            adapter.revalidate(proof)
            registry.current(data, key)
            # Persist no-blind-resend intent before the click boundary. A crash
            # here is conservative ambiguity, never permission to resend.
            item["attempt"] = dict(id=bootstrap_attempt, status="ATTEMPTING")
            registry.write(data)
            adapter.revalidate(proof)
            registry.current(data, key)
            attempted = True
            try:
                adapter.submit(proof)
                adapter.prove_submission(proof)
            except Exception:
                registry.current(data, key)
                item["attempt"]["status"] = "AMBIGUOUS"
                registry.write(data)
                return BootstrapResult("AMBIGUOUS", "SUBMISSION_UNPROVEN")
            registry.current(data, key)
            item["attempt"]["status"] = "SUBMITTED"
            registry.write(data)
            return BootstrapResult("SUBMITTED", "ACCEPTED", item["handle"], item["generation"], authoring_proof)
    except (BootstrapBlocked, wake.WakeBlocked) as error:
        reason = str(error) if str(error) in REASONS else "LOCAL_FAILURE"
        return BootstrapResult("AMBIGUOUS" if attempted else "UNPROVED", reason)
    except Exception:
        return BootstrapResult("AMBIGUOUS" if attempted else "UNPROVED", "LOCAL_FAILURE")
    finally:
        if proof is not None:
            try:
                adapter.finish(proof)
            except Exception:
                pass  # Never retry an attempt, even if page cleanup is unproved.


# Reuse the existing visible/unique-enclosing-form/enabled-Send predicates.
# The closure owns its gesture record; a copied challenge/handle is not an API
# input that can establish origin. Draft text never crosses evaluate's return.
_EXACT_TEXT = r"""(box, text) => {
  // Origin-only post-insertion grammar. Never normalize draft bytes or accept
  // an arbitrary rich tree just because its aggregate text happens to match.
  const limit = 2 * text.length + 1;
  let budget = limit, raw = '';
  const shown = node => {
    const style = getComputedStyle(node);
    return !node.hidden && node.getAttribute('aria-hidden') !== 'true' &&
      node.getClientRects().length && style.visibility !== 'hidden' &&
      style.visibility !== 'collapse' && style.display !== 'none' && style.opacity !== '0';
  };
  const plain = node => node.attributes.length === 0;
  const trailingBreak = node => {
    const attrs = node.attributes;
    return attrs.length === 1 && attrs[0].name === 'class' &&
      attrs[0].value === 'ProseMirror-trailingBreak';
  };
  // The sole attributed root shape; its BR leaf and visibility are still
  // proved by the existing child grammar below.
  const markedEmptyParagraph = node => node.tagName === 'P' &&
    node.attributes.length === 1 && node.attributes[0].name === 'data-empty-paragraph' &&
    node.attributes[0].value === 'true' && node.textContent === '' &&
    node.childNodes.length === 1 && node.childNodes[0].nodeType === 1 &&
    node.childNodes[0].tagName === 'BR' && trailingBreak(node.childNodes[0]);
  if (!box.childNodes.length || box.childNodes.length > limit || !shown(box)) return false;
  const nodes = [...box.childNodes];
  const parts = [];
  for (const node of nodes) {
    if (--budget < 0) return false;
    if (node.nodeType === 3) {
      raw += node.textContent;
      if (raw.length > text.length) return false;
      parts.push({text:node.textContent, block:false, placeholder:false});
      continue;
    }
    if (node.nodeType !== 1 || !['P', 'DIV'].includes(node.tagName) ||
        (!plain(node) && !markedEmptyParagraph(node)) || !shown(node)) return false;
    if (node.childNodes.length > budget) return false;
    const children = [...node.childNodes];
    let logical = '', placeholder = false;
    for (let i = 0; i < children.length; i++) {
      const child = children[i];
      if (--budget < 0) return false;
      if (child.nodeType === 3) {
        logical += child.textContent; raw += child.textContent;
      } else if (child.nodeType === 1 && child.tagName === 'SPAN' &&
                 child.attributes.length === 1 &&
                 child.attributes[0].name === 'data-prompt-literal-paste' &&
                 child.attributes[0].value === '' && child.childNodes.length === 1 &&
                 child.childNodes[0].nodeType === 3 && shown(child)) {
        // Only the observed literal-paste wrapper may expose one text leaf.
        // Count that leaf too; no other inline or nested grammar is admitted.
        if (--budget < 0) return false;
        const value = child.childNodes[0].textContent;
        logical += value; raw += value;
      } else if (child.nodeType === 1 && child.tagName === 'BR' &&
                 !child.childNodes.length && child.textContent === '' && shown(child)) {
        // A sole BR is the empty-block placeholder. The editor's explicitly
        // named trailing BR is padding only after a proved logical newline.
        if (trailingBreak(child)) {
          if (i !== children.length - 1 || (i !== 0 && !logical.endsWith('\n'))) return false;
          placeholder = i === 0;
        } else if (plain(child)) {
          if (children.length === 1) placeholder = true;
          else logical += '\n';
        } else return false;
      } else return false;
      if (logical.length > text.length || raw.length > text.length) return false;
    }
    parts.push({text:logical, block:true, placeholder});
  }
  if (raw !== box.textContent) return false;
  const project = (separator, placeholders) => parts.map((part, i) =>
    (i && (part.block || parts[i - 1].block) ? separator : '') +
    (placeholders && part.placeholder ? '\n' : part.text)).join('');
  // Every block boundary contributes exactly one logical LF. Inline plain BRs
  // contribute one LF; empty/trailing padding contributes none. Equality is
  // against the immutable Human draft + exactly two LFs + bounded envelope.
  if (project('\n', false) !== text) return false;
  if (!parts.some(part => part.block)) return box.innerText === text;
  // Explicit bounded render projections: browser block spacing can be zero,
  // one, or two LFs, with empty BR placeholders rendered uniformly as 0/1 LF.
  // These allowances affect rendered layout only, never the logical bytes.
  return ['', '\n', '\n\n'].some(separator =>
    [false, true].some(placeholders => project(separator, placeholders) === box.innerText));
}"""

INSTALL = r"""({slot, composer, send, stop, nonregular, login, ttl, maxDraft}) => {
""" + wake._SEND_GUARDS + r"""
  const normalize = value => value.length <= 512 && /^https:\/\/chatgpt\.com(?:\/g\/g-[A-Za-z0-9-]*)?\/c\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/?$/.test(value)
    ? value.replace(/\/$/, '') : null;
  const exactText = (""" + _EXACT_TEXT + r""");
  const oneLfEmpty = box => {
    // Post-submit compatibility only: no whitespace normalization or changes
    // to the pre-submit exactText grammar. Root attributes confer no authority.
    if (box.innerText !== '\n' || box.childNodes.length !== 1) return false;
    const root = box.childNodes[0];
    if (root.nodeType !== 1 || root.tagName !== 'P' || root.textContent !== '' ||
        root.childNodes.length !== 1) return false;
    const child = root.childNodes[0];
    if (child.nodeType !== 1 || child.tagName !== 'BR' || child.textContent !== '' ||
        child.childNodes.length !== 0 || child.attributes.length !== 1 ||
        child.attributes[0].name !== 'class' ||
        child.attributes[0].value !== 'ProseMirror-trailingBreak') return false;
    const shown = node => {
      const style = getComputedStyle(node);
      return !node.hidden && node.getAttribute('aria-hidden') !== 'true' &&
        node.getClientRects().length && style.visibility !== 'hidden' &&
        style.visibility !== 'collapse' && style.display !== 'none' && style.opacity !== '0';
    };
    return !!shown(root) && !!shown(child);
  };
  const surface = () => {
    const boxes = visible(composer);
    return normalize(location.href) && visible('main').length === 1 &&
      !visible(login).length && !visible(nonregular).length && !visible(stop).length &&
      boxes.length === 1 && boxes[0].getAttribute('aria-disabled') !== 'true' ? boxes[0] : null;
  };
  if (Object.hasOwn(window, slot)) return null;
  if (!surface()) return null;
  const random = () => [...crypto.getRandomValues(new Uint8Array(32))]
    .map(n => n.toString(16).padStart(2, '0')).join('');
  const doc = document, nonce = random();
  let pending = null, staged = null, authorizedDraft = null, spent = false, invalidated = false;
  const routeChanged = () => {
    if (pending && normalize(location.href) !== pending.chat_url) invalidated = true;
  };
  const pageHidden = () => {invalidated = true;};
  window.addEventListener('popstate', routeChanged);
  window.addEventListener('hashchange', routeChanged);
  window.addEventListener('pagehide', pageHidden);
  const historyHooks = ['pushState', 'replaceState'].map(name => {
    const original = history[name];
    const wrapped = function(...values) {
      const result = original.apply(this, values); routeChanged(); return result;
    };
    history[name] = wrapped; return {name, original, wrapped};
  });
  const button = document.createElement('button');
  button.type = 'button'; button.textContent = 'Connect this chat to AIOS and send draft';
  button.setAttribute('data-aios-origin-bootstrap', 'v1');
  button.style.cssText = 'position:fixed;bottom:100px;right:24px;z-index:2147483647;padding:12px;background:#154a32;color:white;border:2px solid white;border-radius:8px';
  // Observe only operands already evaluated by the published short-circuit
  // predicates. Returning the operand preserves their truthiness and ordering.
  const gate = (value, attribution, category, proofCategory = null) => {
    if (!value && attribution) {
      attribution.category = category; attribution.proof_category = proofCategory;
    }
    return value;
  };
  const proves = (p, attribution = null) =>
    gate(!spent, attribution, 'PROOF', 'SPENT') &&
    gate(!invalidated, attribution, 'PROOF', 'INVALIDATED') &&
    gate(doc === document, attribution, 'PROOF', 'DOCUMENT_IDENTITY') &&
    gate(button.isConnected, attribution, 'PROOF', 'HELPER_BUTTON_CONNECTIVITY') &&
    gate(window[slot] === nonce, attribution, 'PROOF', 'SLOT_OR_NONCE_BINDING') &&
    gate(pending, attribution, 'PROOF', 'PENDING_OR_CHALLENGE_BINDING') &&
    gate(p.challenge === pending.challenge, attribution, 'PROOF', 'PENDING_OR_CHALLENGE_BINDING') &&
    gate(p.document_nonce === nonce, attribution, 'PROOF', 'SLOT_OR_NONCE_BINDING') &&
    gate(p.chat_url === pending.chat_url, attribution, 'PROOF', 'ROUTE_EQUALITY') &&
    gate(normalize(location.href) === pending.chat_url, attribution, 'PROOF', 'ROUTE_EQUALITY') &&
    gate(performance.now() < pending.deadline, attribution, 'PROOF', 'DEADLINE') &&
    gate(surface(), attribution, 'PROOF', 'SURFACE_PROOF');
  const ready = (p, attribution = null) => {
    if (!proves(p, attribution) ||
        !gate(staged, attribution, 'SURFACE_OR_COMPOSER_IDENTITY') ||
        !gate(surface() === staged.box, attribution, 'SURFACE_OR_COMPOSER_IDENTITY') ||
        !gate(exactText(staged.box, staged.expected), attribution, 'EXACT_TEXT')) return null;
    const controls = sendControls(staged.box);
    if (!gate(controls, attribution, 'SEND_SCOPE_OR_COUNT') ||
        !gate(controls.length === 1, attribution, 'SEND_SCOPE_OR_COUNT') ||
        !gate(enabled(controls[0]), attribution, 'SEND_ENABLED')) return null;
    let form = null;
    for (let parent = staged.box.parentElement; parent; parent = parent.parentElement)
      if (parent.tagName === 'FORM') form = parent;
    if (staged.control && !gate(staged.control === controls[0] && staged.form === form,
        attribution, 'RETAINED_FORM_OR_CONTROL_IDENTITY')) return null;
    staged.control = controls[0]; staged.form = form;
    return controls[0];
  };
  button.addEventListener('click', event => {
    if (event.isTrusted !== true || event.currentTarget !== button || pending || spent || invalidated) return;
    const box = surface();
    if (!box || box.innerText.length > maxDraft || box.innerText !== box.textContent) return;
    // Edit-integrity snapshot only; never an identity input or exported value.
    authorizedDraft = {box, text:box.innerText};
    pending = {challenge: random(), chat_url: normalize(location.href), deadline: performance.now() + ttl};
    button.disabled = true; button.textContent = 'AIOS origin handshake in progress';
  });
  const api = Object.freeze({
    inspect: () => pending ? {challenge: pending.challenge, document_nonce: nonce, chat_url: pending.chat_url} : null,
    // Once staged, adapter revalidation also proves exact reconciliation and
    // the retained scoped control, both before intent and after its write.
    prove: p => !!proves(p) && (!staged || !!ready(p)),
    insert: ({proof, metadata}) => {
      if (!proves(proof) || staged || typeof metadata !== 'string' || metadata.length > 512 ||
          !metadata.startsWith('[AIOS ORIGIN BOOTSTRAP]\n') ||
          !metadata.endsWith('\n[/AIOS ORIGIN BOOTSTRAP]')) return false;
      const box = surface(), draft = box.innerText;
      // Narrow plaintext shape: unsupported rich layouts fail without edits.
      if (!authorizedDraft || authorizedDraft.box !== box || authorizedDraft.text !== draft ||
          !draft || draft.length > maxDraft || box.textContent !== draft ||
          draft.includes('[AIOS ORIGIN BOOTSTRAP]')) return false;
      box.focus();
      if (!proves(proof) || surface() !== box || document.activeElement !== box ||
          box.innerText !== draft || box.textContent !== draft) return false;
      const selection = window.getSelection(), range = document.createRange();
      if (!selection) return false;
      range.selectNodeContents(box); range.collapse(false);
      selection.removeAllRanges(); selection.addRange(range);
      const suffix = '\n\n' + metadata;
      staged = {box, expected: draft + suffix};
      if (!document.execCommand('insertText', false, suffix)) return false;
      box.dispatchEvent(new InputEvent('input', {bubbles:true, composed:true, inputType:'insertText', data:suffix}));
      // The native edit was issued on the still-proved surface. Final logical
      // bytes may reconcile asynchronously; only bounded ready() proves them.
      return !!proves(proof) && surface() === box;
    },
    ready: p => !!ready(p),
    readyDiagnostic: p => {
      const attribution = {ready:false, category:null, proof_category:null};
      attribution.ready = !!ready(p, attribution);
      return attribution;
    },
    submit: p => {
      const control = ready(p);
      if (!control) return false;
      // Consume before click, within the same JS turn. No second invocation can send.
      spent = true; control.click(); return true;
    },
    submitted: () => !!spent && !invalidated && !!staged && !!pending && doc === document &&
      normalize(location.href) === pending.chat_url && visible('main').length === 1 &&
      !visible(login).length && !visible(nonregular).length &&
      visible(composer).length === 1 && visible(composer)[0] === staged.box &&
      staged.box.textContent === '' && (staged.box.innerText === '' || oneLfEmpty(staged.box)) &&
      (() => {const controls = sendControls(staged.box);
        return controls && controls.length <= 1 && (!controls.length || !enabled(controls[0]));})(),
    finish: () => {
      spent = true; pending = null; staged = null; authorizedDraft = null;
      window.removeEventListener('popstate', routeChanged);
      window.removeEventListener('hashchange', routeChanged);
      window.removeEventListener('pagehide', pageHidden);
      for (const {name, original, wrapped} of historyHooks)
        if (history[name] === wrapped) history[name] = original;
      button.remove(); if (window[slot] === nonce) delete window[slot]; return true;
    },
  });
  // Publish only a collision guard. The trusted local adapter retains a JSHandle
  // to this closure; page callers cannot fabricate an API in window[slot] or
  // read/copy a pending challenge through a public page-token interface.
  Object.defineProperty(window, slot, {value:nonce, writable:false, configurable:true});
  document.body.appendChild(button);
  return api;
}"""

INSPECT = "api => api.inspect()"
CALL = "(api, {method, value}) => api[method](value)"


class PageBootstrapBrowser:
    """Attach to an existing authorized browser; never navigate/select focus."""

    def __init__(self, endpoint, state_path, *, diagnostic=None):
        self.endpoint = local_endpoint(endpoint, wake.external_path(str(state_path)))
        self.diagnostic = diagnostic
        self.driver = None
        self.installed = []
        self.epochs = {}
        self.listeners = []

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.driver = sync_playwright().start()
        try:
            self.browser = self.driver.chromium.connect_over_cdp(self.endpoint, timeout=10000)
            self.arm()
            return self
        except Exception:
            self.__exit__()
            raise

    def __exit__(self, *args):
        for page, handle in self.installed:
            try:
                handle.evaluate(CALL, dict(method="finish", value=None))
            except Exception:
                pass
            finally:
                try:
                    handle.dispose()
                except Exception:
                    pass
        for page, listener in self.listeners:
            try:
                page.remove_listener("framenavigated", listener)
            except Exception:
                pass
        if self.driver:
            try:
                self.driver.stop()  # Disconnect; do not close the Human's browser.
            except Exception:
                pass  # Cleanup never changes the receipt or retries submission.

    def pages(self):
        if len(self.browser.contexts) > MAX_CONTEXTS:
            raise BootstrapBlocked("STATE_CAPACITY_REQUIRES_HUMAN")
        pages = [page for context in self.browser.contexts for page in context.pages]
        if len(pages) > MAX_PAGES:
            raise BootstrapBlocked("STATE_CAPACITY_REQUIRES_HUMAN")
        return pages

    def arm(self):
        for page in self.pages():
            try:
                wake.normalize_chat(page.url)
            except wake.WakeBlocked:
                continue
            page.set_default_timeout(3000)
            handle = page.evaluate_handle(INSTALL, dict(slot=SLOT, composer=wake.COMPOSER,
                    send=wake.SEND, stop=wake.STOP, nonregular=wake.NONREGULAR,
                    login=wake.LOGIN, ttl=CHALLENGE_TTL_MS, maxDraft=MAX_DRAFT_CHARS))
            if handle.evaluate("api => api !== null"):
                self.installed.append((page, handle))
                self.epochs[page] = 0

                def navigated(frame, selected=page):
                    if frame is selected.main_frame:
                        self.epochs[selected] += 1

                page.on("framenavigated", navigated)
                self.listeners.append((page, navigated))
            else:
                handle.dispose()

    def observations(self):
        found = []
        pages = self.pages()
        for page, handle in self.installed:
            if page not in pages:
                raise BootstrapBlocked("TARGET_PAGE_CHANGED")
            # Document-owned handles become invalid on reload/replacement.
            # An inaccessible registered document is never silently skipped.
            value = handle.evaluate(INSPECT)
            if value is not None:
                if (type(value) is not dict
                        or set(value) != {"challenge", "document_nonce", "chat_url"}
                        or any(type(value[k]) is not str or not NONCE.fullmatch(value[k])
                               for k in ("challenge", "document_nonce"))):
                    raise BootstrapBlocked("CHALLENGE_UNPROVED")
                conversation_key(value["chat_url"])
                found.append(PageProof(page, wake.normalize_chat(value["chat_url"]),
                                       self.endpoint, value["challenge"], value["document_nonce"],
                                       self.epochs.get(page, 0)))
        return found

    def capture(self):
        found = self.observations()
        if len(found) != 1:
            raise BootstrapBlocked("CHALLENGE_NOT_UNIQUE")
        proof = found[0]
        self.revalidate(proof)
        return proof

    @staticmethod
    def proof_value(proof):
        return dict(challenge=proof.challenge, document_nonce=proof.document_nonce,
                    chat_url=proof.chat_url)

    def call(self, proof, method, value=None):
        handles = [handle for page, handle in self.installed if page is proof.page]
        if len(handles) != 1:
            raise BootstrapBlocked("TARGET_PAGE_CHANGED")
        return handles[0].evaluate(CALL, dict(method=method,
            value=self.proof_value(proof) if value is None else value))

    def revalidate(self, proof):
        found = self.observations()
        if len(found) != 1:
            raise BootstrapBlocked("CHALLENGE_NOT_UNIQUE")
        if (found[0] != proof or wake.normalize_chat(proof.page.url) != proof.chat_url
                or self.call(proof, "prove") is not True
                or self.epochs.get(proof.page, 0) != proof.navigation_epoch
                or wake.normalize_chat(proof.page.url) != proof.chat_url):
            raise BootstrapBlocked("TARGET_PAGE_CHANGED")

    def insert(self, proof, metadata):
        diagnostic = getattr(self, "diagnostic", None)
        if diagnostic is not None:
            diagnostic.insert_started()
        accepted = self.call(proof, "insert", dict(proof=self.proof_value(proof), metadata=metadata)) is True
        if diagnostic is not None:
            diagnostic.insert_returned(accepted)
        if not accepted:
            raise BootstrapBlocked("INSERT_BLOCKED")

    def ready(self, proof):
        diagnostic = getattr(self, "diagnostic", None)
        if diagnostic is not None:
            diagnostic.ready_started()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if diagnostic is None:
                accepted = self.call(proof, "ready") is True
            else:
                try:
                    accepted = diagnostic.ready_observed(self.call(proof, "readyDiagnostic"))
                except Exception:
                    diagnostic.ready_unknown()
                    raise
            if accepted:
                return
            time.sleep(0.05)
        if diagnostic is not None:
            diagnostic.exhausted = True
        raise BootstrapBlocked("INSERT_BLOCKED")

    def submit(self, proof):
        if self.call(proof, "submit") is not True:
            raise BootstrapBlocked("SEND_BLOCKED")

    def prove_submission(self, proof):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.call(proof, "submitted", {}) is True:
                break
            time.sleep(0.05)
        else:
            raise BootstrapBlocked("SUBMISSION_UNPROVEN") from None
        # Structural composer clearing only; no transcript or assistant scraping.
        found = self.observations()
        if (len(found) != 1 or found[0] != proof
                or wake.normalize_chat(proof.page.url) != proof.chat_url
                or self.call(proof, "submitted", {}) is not True):
            raise BootstrapBlocked("SUBMISSION_UNPROVEN")

    def finish(self, proof):
        self.call(proof, "finish", {})


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise BootstrapBlocked("INVALID_INPUT")


def main(argv=None):
    """One bounded development session. Emits only fixed/opaque result fields."""
    parser = _Parser(description="Expose an in-page Human origin handshake in an existing local browser.")
    parser.add_argument("--state", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--proof-delay-ms", type=int, default=0)
    parser.add_argument("--diagnostic", action="store_true",
                        help="Wrap the ordinary result with bounded readiness attribution.")
    diagnostic = None
    try:
        # Materialize once, as argparse does, including for iterable callers.
        # Preserve the opt-in envelope even when argument parsing itself rejects.
        arguments = sys.argv[1:] if argv is None else list(argv)
        if "--diagnostic" in arguments:
            diagnostic = BootstrapDiagnostics()
        args = parser.parse_args(arguments)
        if args.diagnostic and diagnostic is None:
            diagnostic = BootstrapDiagnostics()
        if (not 1 <= args.seconds <= 300 or not 0 <= args.proof_delay_ms <= 5000
                or os.environ.get("DEBUG") or os.environ.get("PWDEBUG")):
            raise BootstrapBlocked("INVALID_INPUT")
        logging.disable(logging.CRITICAL)
        registry = OriginRegistry(args.state)
        options = dict(diagnostic=diagnostic) if diagnostic is not None else {}
        with PageBootstrapBrowser(args.endpoint, registry.path, **options) as adapter:
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                if adapter.observations():
                    # Development ambiguity trial only; elapsed time is never
                    # origin proof. The document's challenge must still prove.
                    if args.proof_delay_ms:
                        time.sleep(args.proof_delay_ms / 1000)
                    result = bootstrap(registry, adapter)
                    print(json.dumps(diagnostic.as_dict(result) if diagnostic is not None else result.as_dict()))
                    return 0 if result.status == "SUBMITTED" else 1
                time.sleep(0.1)
        result = BootstrapResult("UNPROVED", "CHALLENGE_UNPROVED")
    except (BootstrapBlocked, wake.WakeBlocked) as error:
        result = BootstrapResult("UNPROVED", str(error) if str(error) in REASONS else "LOCAL_FAILURE")
    except Exception:
        result = BootstrapResult("UNPROVED", "LOCAL_FAILURE")
    print(json.dumps(diagnostic.as_dict(result) if diagnostic is not None else result.as_dict()))
    return 1
