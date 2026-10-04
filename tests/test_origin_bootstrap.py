"""Focused deterministic origin-bootstrap contracts; synthetic local/browser data.

No live ChatGPT, account metadata, canonical lifecycle, or network is involved.
Runtime owns execution of these tests and construction of canonical EVIDENCE.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import shutil
import subprocess
from threading import Event
from types import SimpleNamespace

import pytest

from aios_renew import local_chat_wake as wake
from aios_renew import origin_bootstrap as origin

URL_A = "https://chatgpt.com/c/00000000-0000-0000-0000-000000000001"
URL_B = "https://chatgpt.com/c/00000000-0000-0000-0000-000000000002"
ENDPOINT = "http://127.0.0.1:9222"


def test_h4c1_shared_opaque_selector_grammar_preserves_h4c0_contract():
    from aios_renew import return_affinity as affinity
    assert origin.HANDLE is affinity.HANDLE
    assert origin.HANDLE_PREFIX == affinity.HANDLE_PREFIX
    assert origin.MAX_GENERATION == affinity.MAX_GENERATION == 2147483647
    handle = origin.HANDLE_PREFIX + "a" * 64
    result = origin.BootstrapResult("SUBMITTED", "ACCEPTED", handle, 1).as_dict()
    assert set(result) == {"contract", "status", "reason", "route_handle", "generation"}
    assert result["contract"] == "PAGE_SCOPED_AIOS_SEND_ORIGIN_BOOTSTRAP_V1"
    assert affinity.parse_affinity(dict(kind="ORIGIN_AFFINE", route_handle=handle, generation=1)).route_handle == handle


@pytest.fixture
def registry(tmp_path):
    return origin.OriginRegistry(tmp_path / "origin.json")


class Adapter:
    """Trusted browser boundary double with a strictly ordered trace."""

    def __init__(self, registry, url=URL_A, *, blocked=None, race=None):
        self.registry = registry
        self.proof = origin.PageProof(object(), url, ENDPOINT, "a" * 64, "b" * 64)
        self.blocked, self.race = blocked, race
        self.trace, self.metadata, self.submits = [], None, 0
        self.revalidations = 0

    def step(self, stage):
        self.trace.append(stage)
        if self.race:
            self.race(stage, self)
        if self.blocked == stage:
            raise origin.BootstrapBlocked({
                "capture": "CHALLENGE_NOT_UNIQUE", "revalidate": "TARGET_PAGE_CHANGED",
                "insert": "INSERT_BLOCKED", "ready": "INSERT_BLOCKED",
                "submit": "SEND_BLOCKED", "prove_submission": "SUBMISSION_UNPROVEN",
            }.get(stage, "LOCAL_FAILURE"))

    def capture(self):
        self.step("capture")
        assert not self.submits and self.metadata is None
        return self.proof

    def revalidate(self, proof):
        self.revalidations += 1
        self.step("revalidate")
        assert proof == self.proof
        stored = self.registry.read()["routes"][origin.conversation_key(proof.chat_url)]
        assert stored["chat_url"] == proof.chat_url

    def insert(self, proof, metadata):
        self.step("insert")
        assert self.revalidations >= 1 and not self.submits
        assert self.registry.path.exists() and self.registry.read()["routes"]
        self.metadata = metadata

    def ready(self, proof):
        self.step("ready")

    def submit(self, proof):
        self.step("submit")
        assert self.metadata is not None and self.revalidations == 3
        attempts = [item["attempt"] for item in self.registry.read()["routes"].values()]
        assert any(attempt and attempt["status"] == "ATTEMPTING" for attempt in attempts)
        self.submits += 1

    def prove_submission(self, proof):
        self.step("prove_submission")

    def finish(self, proof):
        self.trace.append("finish")


def test_durable_order_and_bounded_opaque_envelope(registry, monkeypatch):
    adapter = Adapter(registry)
    write = registry.write

    def observed_write(data):
        adapter.trace.append("durable_write")
        write(data)

    monkeypatch.setattr(registry, "write", observed_write)
    result = origin.bootstrap(registry, adapter)
    assert result.status == "SUBMITTED"
    assert adapter.trace == ["capture", "durable_write", "revalidate", "insert", "ready",
                             "revalidate", "durable_write", "revalidate", "submit",
                             "prove_submission", "durable_write", "finish"]
    assert result.generation == 1 and origin.HANDLE.fullmatch(result.route_handle)
    assert len(adapter.metadata.encode("ascii")) <= origin.MAX_ENVELOPE_BYTES
    assert result.route_handle in adapter.metadata
    assert set(result.as_dict()) == {"contract", "status", "reason", "route_handle", "generation"}


def test_repeat_distinct_same_repository_and_reload_reopen_routes(registry):
    a1 = origin.bootstrap(registry, Adapter(registry))
    # Different exact documents, including trailing-slash normalization/reopen.
    a2 = origin.bootstrap(registry, Adapter(registry, URL_A))
    b1 = origin.bootstrap(registry, Adapter(registry, URL_B))
    b2 = origin.bootstrap(registry, Adapter(registry, URL_B))
    assert a1.route_handle == a2.route_handle
    assert b1.route_handle == b2.route_handle
    assert a1.route_handle != b1.route_handle
    assert len(registry.read()["routes"]) == 2
    assert origin.conversation_key(URL_A + "/") == origin.conversation_key(URL_A)
    project_url = URL_A.replace("/c/", "/g/g-fixture/c/")
    reopened = origin.bootstrap(registry, Adapter(registry, project_url))
    assert reopened.route_handle == a1.route_handle and reopened.generation == a1.generation
    assert registry.read()["routes"][origin.conversation_key(URL_A)]["chat_url"] == project_url


def test_overlapping_same_conversation_tabs_cannot_compete(registry):
    entered, release = Event(), Event()

    def hold(stage, adapter):
        if stage == "insert":
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(origin.bootstrap, registry, Adapter(registry, race=hold))
        try:
            assert entered.wait(5)
            second = origin.bootstrap(registry, Adapter(registry))
            assert second.reason == "STATE_LOCKED_OR_UNAVAILABLE"
        finally:
            release.set()
        original = first.result(timeout=5)
    later = origin.bootstrap(registry, Adapter(registry))
    assert original.route_handle == later.route_handle
    assert len(registry.read()["routes"]) == 1


@pytest.mark.parametrize("stage", ["capture", "revalidate", "insert", "ready"])
def test_pre_submission_failures_do_not_send(registry, stage):
    adapter = Adapter(registry, blocked=stage)
    result = origin.bootstrap(registry, adapter)
    assert result.status == "UNPROVED" and adapter.submits == 0
    assert "submit" not in adapter.trace
    if stage == "capture":
        assert not registry.path.exists()


@pytest.mark.parametrize("boundary", ["binding", "attempt", "completion"])
def test_uncertain_durable_write_never_blind_resends(registry, monkeypatch, boundary):
    adapter = Adapter(registry)
    real_write, calls = registry.write, []
    failing_write = {"binding": 1, "attempt": 2, "completion": 3}[boundary]

    def write(data):
        calls.append(1)
        if len(calls) == failing_write:
            registry.path.with_name(registry.path.name + ".pending").write_text("uncertain", encoding="utf-8")
            raise wake.WakeBlocked("STATE_WRITE_UNCERTAIN")
        real_write(data)

    monkeypatch.setattr(registry, "write", write)
    result = origin.bootstrap(registry, adapter)
    assert result.reason == "STATE_WRITE_UNCERTAIN"
    assert adapter.submits == (1 if boundary == "completion" else 0)
    assert result.status == ("AMBIGUOUS" if boundary == "completion" else "UNPROVED")
    monkeypatch.setattr(registry, "write", real_write)
    second = Adapter(registry)
    assert origin.bootstrap(registry, second).reason == "STATE_WRITE_UNCERTAIN"
    assert second.submits == 0


@pytest.mark.parametrize("stage", ["submit", "prove_submission"])
def test_ambiguous_attempt_blocks_even_a_new_gesture(registry, stage):
    first = Adapter(registry, blocked=stage)
    result = origin.bootstrap(registry, first)
    assert result.status == "AMBIGUOUS" and result.route_handle is None
    assert registry.read()["routes"][URL_A]["attempt"]["status"] == "AMBIGUOUS"
    second = Adapter(registry)
    assert origin.bootstrap(registry, second).reason == "ATTEMPT_REQUIRES_HUMAN"
    assert second.submits == 0 and "insert" not in second.trace


@pytest.mark.parametrize("stage", ["revalidate", "ready"])
@pytest.mark.parametrize("field", ["generation", "handle", "cdp_endpoint"])
def test_binding_generation_and_registry_races_fail_closed(registry, field, stage):
    raced = []

    def race(current_stage, adapter):
        if current_stage == stage and not raced:
            raced.append(True)
            data = registry.read()
            data["routes"][URL_A][field] = {
                "generation": 2, "handle": origin.HANDLE_PREFIX + "c" * 64,
                "cdp_endpoint": "http://127.0.0.1:9223",
            }[field]
            registry.write(data)

    adapter = Adapter(registry, race=race)
    result = origin.bootstrap(registry, adapter)
    assert result.reason == ("BINDING_GENERATION_CHANGED" if field == "generation" else "REGISTRY_CONFLICT")
    assert adapter.submits == 0


def test_collision_and_endpoint_transfer_are_not_new_origin_authority(registry, monkeypatch):
    original = origin.bootstrap(registry, Adapter(registry))
    monkeypatch.setattr(origin.secrets, "token_hex", lambda _: original.route_handle[len(origin.HANDLE_PREFIX):])
    other = Adapter(registry, URL_B)
    assert origin.bootstrap(registry, other).reason == "REGISTRY_CONFLICT"
    assert other.submits == 0
    transfer = Adapter(registry)
    transfer.proof = origin.PageProof(object(), URL_A, "http://127.0.0.1:9223", "a" * 64, "b" * 64)
    assert origin.bootstrap(registry, transfer).reason == "REGISTRY_CONFLICT"
    assert registry.read()["routes"][URL_A]["generation"] == 1


def test_post_submit_registry_change_is_not_overwritten_or_resent(registry):
    def race(stage, adapter):
        if stage == "prove_submission":
            data = registry.read()
            data["routes"][URL_A]["generation"] = 2
            registry.write(data)

    adapter = Adapter(registry, race=race)
    result = origin.bootstrap(registry, adapter)
    assert result.status == "AMBIGUOUS" and result.reason == "BINDING_GENERATION_CHANGED"
    assert adapter.submits == 1 and registry.read()["routes"][URL_A]["generation"] == 2
    assert registry.read()["routes"][URL_A]["attempt"]["status"] == "ATTEMPTING"
    assert origin.bootstrap(registry, Adapter(registry)).reason == "ATTEMPT_REQUIRES_HUMAN"


def test_local_only_identity_and_no_lifecycle_or_wake_authority(registry, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("bootstrap must not call wake delivery/canonical freshness")

    monkeypatch.setattr(wake, "deliver", forbidden)
    monkeypatch.setattr(wake, "operate", forbidden)
    monkeypatch.setattr(wake.CanonicalFreshness, "observe", forbidden)
    output_file = tmp_path / "canonical-result.json"
    adapter = Adapter(registry)
    result = origin.bootstrap(registry, adapter)
    output_file.write_text(json.dumps(result.as_dict()), encoding="utf-8")
    canonical = output_file.read_text(encoding="utf-8") + adapter.metadata + repr(adapter.proof)
    for sensitive in (URL_A, URL_A.rsplit("/", 1)[-1], ENDPOINT, str(registry.path), "Human authored draft"):
        assert sensitive not in canonical
    data = registry.read()
    assert data["routes"][URL_A]["chat_url"] == URL_A
    assert data["routes"][URL_A]["cdp_endpoint"] == ENDPOINT
    assert set(data) == {"version", "routes"}
    assert not {"repository", "task", "run", "next_action", "wake", "queue", "prompt", "transcript"} & set(data)
    assert {p.name for p in tmp_path.iterdir()} == {"origin.json", "canonical-result.json"}
    # A handle alone is not an origin-proof argument to either entry.
    assert origin.main(["--route-handle", result.route_handle]) == 1


def test_repository_paths_raw_failures_and_duplicate_local_keys_are_closed(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    assert origin.main(["--state", str(repo / "raw-secret.json"), "--endpoint", ENDPOINT]) == 1
    output = capsys.readouterr().out
    assert "CONFIG_OR_STATE_IN_REPOSITORY" in output
    assert str(repo) not in output and ENDPOINT not in output and "raw-secret" not in output
    registry = origin.OriginRegistry(tmp_path / "duplicate.json")
    registry.path.write_text('{"version":1,"routes":{},"routes":{}}', encoding="utf-8")
    assert origin.bootstrap(registry, Adapter(registry)).reason == "LOCAL_METADATA_INVALID"


def test_bare_repository_and_pending_sibling_cannot_store_raw_identity(tmp_path):
    bare = tmp_path / "bare.git"
    bare.mkdir()
    (bare / "objects").mkdir()
    (bare / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (bare / "config").write_text("[core]\n bare = true", encoding="utf-8")
    with pytest.raises(origin.BootstrapBlocked, match="CONFIG_OR_STATE_IN_REPOSITORY"):
        origin.OriginRegistry(bare / "registry.json")
    registry = origin.OriginRegistry(tmp_path / "registry.json")
    registry.path.with_name(registry.path.name + ".pending").write_text("uncertain", encoding="utf-8")
    adapter = Adapter(registry)
    assert origin.bootstrap(registry, adapter).reason == "STATE_WRITE_UNCERTAIN"
    assert not registry.path.exists() and not adapter.submits


def test_raw_exception_debug_and_copied_identity_arguments_are_not_output(registry, monkeypatch, capsys):
    adapter = Adapter(registry)

    def raw_failure():
        raise RuntimeError(URL_A + " Human authored draft " + ENDPOINT)

    adapter.capture = raw_failure
    assert origin.bootstrap(registry, adapter).as_dict() == dict(
        contract=origin.CONTRACT, status="UNPROVED", reason="LOCAL_FAILURE",
        route_handle=None, generation=None)
    monkeypatch.setenv("DEBUG", "pw:api")
    assert origin.main(["--state", str(registry.path), "--endpoint", ENDPOINT]) == 1
    assert "INVALID_INPUT" in capsys.readouterr().out
    monkeypatch.delenv("DEBUG")
    assert origin.main(["--state", str(registry.path), "--endpoint", ENDPOINT,
                        "--challenge", "a" * 64, "--chat-url", URL_A]) == 1
    output = capsys.readouterr().out
    assert "INVALID_INPUT" in output and URL_A not in output and ENDPOINT not in output
    assert not registry.path.exists()


@pytest.mark.parametrize("arguments", [["--seconds", "0"], ["--seconds", "301"],
                                        ["--proof-delay-ms", "-1"], ["--proof-delay-ms", "5001"]])
def test_local_surface_wait_and_delay_are_bounded(registry, arguments, capsys):
    assert origin.main(["--state", str(registry.path), "--endpoint", ENDPOINT, *arguments]) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "INVALID_INPUT"
    assert not registry.path.exists()


@pytest.mark.parametrize("url", ["https://chatgpt.com/", URL_A + "?secret=x", URL_A + "#x",
                                  "https://example.invalid/c/a", "http://chatgpt.com/c/a"])
def test_invalid_route_has_no_local_binding_or_submission(registry, url):
    adapter = Adapter(registry, url)
    assert origin.bootstrap(registry, adapter).status == "UNPROVED"
    assert adapter.submits == 0 and not registry.path.exists()


def test_malformed_duplicate_handle_generation_and_capacity(registry, monkeypatch):
    origin.bootstrap(registry, Adapter(registry))
    original = registry.read()
    for value in (True, 0, -1, origin.MAX_GENERATION + 1):
        data = json.loads(json.dumps(original))
        data["routes"][URL_A]["generation"] = value
        with pytest.raises(origin.BootstrapBlocked, match="STATE_AMBIGUOUS"):
            registry.validate(data)
    data = json.loads(json.dumps(original))
    data["routes"][URL_B] = dict(data["routes"][URL_A], chat_url=URL_B)
    with pytest.raises(origin.BootstrapBlocked, match="REGISTRY_CONFLICT"):
        registry.validate(data)
    monkeypatch.setattr(origin, "MAX_ROUTES", 1)
    assert origin.bootstrap(registry, Adapter(registry, URL_B)).reason == "STATE_CAPACITY_REQUIRES_HUMAN"


class Page:
    def __init__(self, url=URL_A, observation=None, proven=True):
        self.url, self.observation, self.proven = url, observation, proven
        self.calls = []

    def evaluate(self, program, arguments=None):
        self.calls.append((program, arguments))
        if program == origin.INSPECT:
            return self.observation
        assert program == origin.CALL
        return self.proven


def observation(url=URL_A):
    return dict(chat_url=url, challenge="a" * 64, document_nonce="b" * 64)


def browser(pages):
    adapter = origin.PageBootstrapBrowser.__new__(origin.PageBootstrapBrowser)
    adapter.endpoint = ENDPOINT
    adapter.browser = SimpleNamespace(contexts=[SimpleNamespace(pages=pages)])
    adapter.installed = [(page, page) for page in pages]
    adapter.epochs = {page: 0 for page in pages}
    return adapter


def test_exact_challenge_selects_document_despite_same_conversation_tabs():
    selected, other = Page(observation=observation()), Page()
    adapter = browser([other, selected])
    proof = adapter.capture()
    assert proof.page is selected
    assert proof.chat_url == URL_A
    assert other.calls and selected.calls  # Enumerate; never use focus/active tab.


@pytest.mark.parametrize("kind", ["absent", "duplicate", "nonregular", "replaced", "route_change", "route_roundtrip",
                                   "malformed", "inaccessible"])
def test_challenge_absence_ambiguity_route_and_document_change_are_closed(kind):
    selected = Page(observation=observation())
    adapter = browser([selected])
    proof = adapter.capture()
    if kind == "absent":
        selected.observation = None
    elif kind == "duplicate":
        duplicate = Page(observation=observation())
        adapter.browser.contexts[0].pages.append(duplicate)
        adapter.installed.append((duplicate, duplicate))
    elif kind == "nonregular":
        selected.proven = False
    elif kind == "replaced":
        adapter.browser.contexts[0].pages = [Page(observation=observation())]
    elif kind == "route_change":
        selected.url = URL_B
    elif kind == "route_roundtrip":
        selected.url = URL_A
        adapter.epochs[selected] += 2
    elif kind == "malformed":
        selected.observation["challenge"] = "caller copied token"
    else:
        def unavailable(*args):
            raise RuntimeError("raw secret diagnostic")
        selected.evaluate = unavailable
    with pytest.raises((origin.BootstrapBlocked, RuntimeError)):
        adapter.revalidate(proof)


def test_browser_inventory_is_bounded():
    adapter = browser([Page() for _ in range(origin.MAX_PAGES + 1)])
    with pytest.raises(origin.BootstrapBlocked, match="STATE_CAPACITY"):
        adapter.pages()


# Execute the production page closure against a synthetic DOM. The fixture's
# trusted event bit represents browser provenance; programmatic events lack it.
DOM_HARNESS = r"""
(async () => {
const fs = require('fs'), input = JSON.parse(fs.readFileSync(0, 'utf8'));
const args = input.args, scenario = input.scenario;
const literal = scenario.startsWith('async_literal_');
const markedEmpty = scenario.startsWith('async_literal_empty_');
const variant = markedEmpty ? scenario.replace('async_literal_empty_', 'async_') :
  literal ? scenario.replace('async_literal_', 'async_') : scenario;
let clock = 0, randomCount = 0, clicks = 0, inserts = 0, notifications = 0, focuses = 0, button;
const operations = [], readyDiagnostics = [];
global.performance = {now: () => {operations.push('proof-clock'); return clock;}};
global.crypto = {getRandomValues: bytes => {bytes.fill(++randomCount); return bytes;}};
global.location = {href: input.url}; global.window = {addEventListener() {}, removeEventListener() {}};
global.history = {pushState(state, title, url) {location.href=url;},
  replaceState(state, title, url) {location.href=url;}};
global.getComputedStyle = element => {operations.push('style');
  return {visibility:'visible', display:'block', opacity:'1', ...element.css};};
const element = () => ({attributes:[], childNodes:[],
  getClientRects: () => {operations.push('rects'); return [1];},
  getAttribute: () => {operations.push('attribute'); return null;}});
const text = value => ({nodeType:3, textContent:value});
const br = trailing => Object.assign(element(), {nodeType:1, tagName:'BR', textContent:'',
  attributes:trailing ? [{name:'class', value:'ProseMirror-trailingBreak'}] : []});
const paragraph = (line, tagName='P') => Object.assign(element(), {nodeType:1, tagName,
  textContent:line, childNodes:line === '' ? [br(false)] : [text(line)]});
const literalSpan = value => Object.assign(element(), {nodeType:1, tagName:'SPAN', textContent:value,
  attributes:[{name:'data-prompt-literal-paste', value:''}], childNodes:[text(value)]});
const humanDraft = input.draft ?? (literal ? 'Human exact draft  ' : 'Human authored draft\nsecond exact line  ');
let reconciliation;
const box = Object.assign(element(), {innerText:humanDraft, textContent:humanDraft, childNodes:[text(humanDraft)],
  focus() {focuses++; document.activeElement = box;
    if (scenario === 'focus_route') location.href = input.otherUrl;
    if (scenario === 'focus_edit') box.innerText = box.textContent = humanDraft + ' Human edit';
  }, dispatchEvent(event) {
    if (event.data !== '\n\n' + input.metadata || event.inputType !== 'insertText' ||
        !event.bubbles || !event.composed) throw Error('wrong draft edit notification');
    notifications++;
    if (scenario === 'input_edit') box.innerText = box.textContent += ' Human edit';
    if (scenario === 'input_route') location.href = input.otherUrl;
    if (scenario.startsWith('async_')) {
      // Native insertion returns first. The observed class retains the same
      // enabled Send while the editor reconciles in a later microtask. A
      // separate wait case also models delayed application Send enablement.
      send.disabled = variant === 'async_wait_exact';
      if (literal) {
        // A transient, unallowlisted representation at native insert return.
        // The final production wrapper arrives only during later ready polls.
        box.childNodes = [Object.assign(element(), {nodeType:1, tagName:'SPAN',
          textContent:box.textContent, childNodes:[text(box.textContent)]})];
      }
      if (variant !== 'async_never_reconciled') reconciliation = Promise.resolve().then(reconcile);
    }
  }});
const send = Object.assign(element(), {disabled:false, click() {
  clicks++; box.innerText = box.textContent = ''; send.disabled = true;
  elements[args.stop] = [element()];
}});
const elements = {[args.composer]:[box], [args.stop]:[], [args.login]:[],
  [args.nonregular]:[], main:[element()]};
let controls = [send];
const form = {tagName:'FORM', parentElement:null, contains: e => e === box || controls.includes(e),
  querySelectorAll: selector => {operations.push('scoped-send');
    if (selector !== args.send) throw Error('bad form selector'); return controls;}};
box.parentElement = form;
global.document = {
  activeElement:null,
  body:{appendChild(node) {node.isConnected = true; button = node;}},
  querySelectorAll: selector => {operations.push('query:' + selector);
    if (selector === args.send) throw Error('global Send query');
    return elements[selector] || [];},
  createElement: tag => {
    if (tag !== 'button') throw Error('unexpected UI surface');
    return {style:{}, setAttribute() {}, remove() {this.isConnected=false;},
      addEventListener(type, listener) {this.listener=listener;}};
  },
  createRange: () => ({selectNodeContents(node) {if (node !== box) throw Error('wrong composer');},
    collapse(start) {if (start !== false) throw Error('draft replacement attempted');}}),
  execCommand: (command, unused, suffix) => {
    if (command !== 'insertText' || suffix !== '\n\n' + input.metadata) throw Error('wrong edit');
    inserts++;
    if (scenario === 'native_false') return false;
    box.innerText = box.textContent += suffix;
    box.childNodes = [text(box.textContent)];
    if (scenario === 'blocks_exact') {
      const lines = box.innerText.split('\n');
      box.childNodes = lines.map(line => Object.assign(element(), {nodeType:1, tagName:'P',
        textContent:line, childNodes:[text(line)]}));
      box.textContent = lines.join(''); box.innerText = lines.join('\n\n');
    }
    if (scenario === 'native_edit') box.innerText = box.textContent += ' Human edit';
    return true;
  },
};
window.getSelection = () => scenario === 'selection_absent' ? null : {removeAllRanges() {}, addRange() {}};
global.InputEvent = class {constructor(type, options) {Object.assign(this, options); this.type=type;}};
if (scenario === 'no_main') elements.main = [];
if (scenario === 'login') elements[args.login] = [element()];
if (scenario === 'nonregular') elements[args.nonregular] = [element()];
if (scenario === 'busy') elements[args.stop] = [element()];
if (scenario === 'disabled') box.getAttribute = () => 'true';
if (scenario === 'multiple_composers') elements[args.composer].push(element());
if (scenario === 'invalid_url') location.href = 'https://example.invalid/';
if (scenario === 'forged_page_api') window[args.slot] = {arm:() => true, inspect:() => ({
  challenge:'01'.repeat(32), document_nonce:'02'.repeat(32), chat_url:input.url}), prove:() => true};
const api = eval('(' + input.install + ')')(args), installed = !!api;
if (!installed) {process.stdout.write(JSON.stringify({installed, clicks, inserts, notifications})); process.exit(0);}
button.listener({isTrusted:scenario !== 'untrusted' && scenario !== 'copied_token', currentTarget:button});
let proof = api.inspect(), genuineProof = proof;
if (scenario === 'copied_token') proof = {challenge:'01'.repeat(32), document_nonce:'02'.repeat(32), chat_url:input.url};
if (scenario === 'wrong_challenge' && proof) proof = {...proof, challenge:'ff'.repeat(32)};
if (scenario === 'wrong_document' && proof) proof = {...proof, document_nonce:'ff'.repeat(32)};
if (scenario === 'expired') clock = args.ttl;
if (scenario === 'route_change') location.href = input.otherUrl;
if (scenario === 'route_roundtrip') {
  history.pushState({}, '', input.otherUrl); history.replaceState({}, '', input.url);
}
if (scenario === 'page_replacement') global.document = {...document};
if (scenario === 'empty_draft') box.innerText = box.textContent = '';
if (scenario === 'rich_draft') box.textContent = 'different plaintext representation';
if (scenario === 'overbound_draft') box.innerText = box.textContent = 'x'.repeat(args.maxDraft + 1);
if (scenario === 'existing_envelope') box.innerText = box.textContent += '[AIOS ORIGIN BOOTSTRAP]';
if (scenario === 'gesture_edit') box.innerText = box.textContent += ' Human edit';
const proved = proof ? api.prove(proof) : false;
const inserted = proof ? api.insert({proof, metadata:input.metadata}) : false;
const staged = box.textContent;
const pollReady = p => {
  if (!input.diagnostic) return api.ready(p);
  const value = api.readyDiagnostic(p); readyDiagnostics.push(value); return value.ready;
};
const beforeReconciliation = scenario.startsWith('async_') ? pollReady(proof) : null;
const expected = humanDraft + '\n\n' + input.metadata;
function reconcile() {
  let lines = expected.split('\n');
  const separator = literal ? 1 : 2;
  if (variant === 'async_missing_separator') lines.splice(separator, 1);
  if (variant === 'async_extra_separator') lines.splice(separator, 0, '');
  if (variant === 'async_reordered_separator') lines.push(lines.splice(separator, 1)[0]);
  if (variant === 'async_reordered_lines') [lines[0], lines[1]] = [lines[1], lines[0]];
  if (variant === 'async_altered_draft') lines[0] = 'h' + lines[0].slice(1);
  if (variant === 'async_altered_bootstrap') lines[lines.length - 2] += ' altered';
  if (variant === 'async_trimmed_draft') lines[literal ? 0 : 1] = lines[literal ? 0 : 1].trimEnd();
  if (variant === 'async_unicode_normalized') lines = lines.map(line => line.normalize('NFC'));
  if (variant === 'async_crlf_collapsed') lines = lines.map(line => line.replaceAll('\r', ''));
  if (variant === 'async_duplicated_content' || variant === 'async_hidden_duplicate') lines.push(lines[0]);
  const tag = variant === 'async_div_exact' ? 'DIV' : 'P';
  box.childNodes = lines.map(line => paragraph(line, tag));
  if (literal) box.childNodes.forEach((block, i) => {
    block.childNodes = lines[i] === '' ? [br(true)] : [literalSpan(lines[i])];
    if (markedEmpty && lines[i] === '')
      block.attributes = [{name:'data-empty-paragraph', value:'true'}];
  });
  let rendered = lines.map(line => line === '' ? '\n' : line);
  if (['async_inline_exact', 'async_trailing_exact', 'async_missing_inline_break', 'async_extra_inline_break'].includes(variant)) {
    const trailing = variant === 'async_trailing_exact';
    const count = trailing ? 3 : 2, group = lines.slice(0, count);
    const children = [];
    group.forEach((line, i) => {if (i) children.push(br(false)); if (line) children.push(text(line));});
    if (trailing) children.push(br(true));
    if (variant === 'async_missing_inline_break') children.splice(1, 1);
    if (variant === 'async_extra_inline_break') children.splice(1, 0, br(false));
    const combined = paragraph(group.join('\n'), tag);
    combined.childNodes = children;
    combined.textContent = children.map(child => child.textContent).join('');
    box.childNodes.splice(0, count, combined);
    rendered.splice(0, count, group.join('\n'));
  }
  if (variant === 'async_hidden_block') box.childNodes[0].hidden = true;
  const emptyBlock = box.childNodes.find(node => node.textContent === '');
  const emptyBreak = emptyBlock?.childNodes[0];
  if (markedEmpty) {
    if (variant === 'async_root_div') emptyBlock.tagName = 'DIV';
    if (variant === 'async_root_wrong_tag') emptyBlock.tagName = 'SECTION';
    if (variant === 'async_marker_wrong_name') emptyBlock.attributes[0].name = 'data-empty';
    if (variant === 'async_marker_wrong_value') emptyBlock.attributes[0].value = 'false';
    if (variant === 'async_marker_missing_value') emptyBlock.attributes[0].value = '';
    if (variant === 'async_marker_case') emptyBlock.attributes[0].value = 'True';
    if (variant === 'async_marker_whitespace') emptyBlock.attributes[0].value = 'true ';
    if (variant === 'async_root_extra_attribute') emptyBlock.attributes.push({name:'data-extra', value:''});
    if (variant === 'async_marked_nonempty')
      box.childNodes[0].attributes = [{name:'data-empty-paragraph', value:'true'}];
    if (variant === 'async_empty_child_plain_br') emptyBlock.childNodes = [br(false)];
    if (variant === 'async_empty_child_wrong_tag') emptyBreak.tagName = 'SPAN';
    if (variant === 'async_empty_child_text') emptyBlock.childNodes = [text('')];
    if (variant === 'async_empty_child_comment') emptyBlock.childNodes = [{nodeType:8, textContent:''}];
    if (variant === 'async_empty_children_multiple') emptyBlock.childNodes.push(br(true));
    if (variant === 'async_empty_children_absent') emptyBlock.childNodes = [];
    if (variant === 'async_empty_root_hidden') emptyBlock.hidden = true;
    if (variant === 'async_empty_root_no_rects') emptyBlock.getClientRects = () => [];
    if (variant === 'async_empty_root_invisible') emptyBlock.css = {visibility:'hidden'};
    if (variant === 'async_empty_root_collapsed') emptyBlock.css = {visibility:'collapse'};
    if (variant === 'async_empty_root_display_none') emptyBlock.css = {display:'none'};
    if (variant === 'async_empty_root_transparent') emptyBlock.css = {opacity:'0'};
  }
  if (variant === 'async_hidden_br') emptyBreak.css = {visibility:'hidden'};
  if (variant === 'async_hidden_duplicate') box.childNodes.at(-1).getClientRects = () => [];
  if (variant === 'async_transparent_block') box.childNodes[0].css = {opacity:'0'};
  if (variant === 'async_collapsed_block') box.childNodes[0].css = {display:'none'};
  if (variant === 'async_nested_rich') {
    box.childNodes[0].childNodes = [Object.assign(element(), {nodeType:1, tagName:'SPAN',
      textContent:lines[0], childNodes:[text(lines[0])]})];
  }
  if (variant === 'async_unknown_node') box.childNodes[0].childNodes.push({nodeType:8, textContent:''});
  if (variant === 'async_decorated_block') box.childNodes[0].attributes = [{name:'class', value:'rich'}];
  if (variant === 'async_decorated_br') emptyBreak.attributes = [{name:'data-rich', value:'true'}];
  if (literal) {
    const span = box.childNodes[0].childNodes[0];
    if (variant === 'async_attr_missing') span.attributes = [];
    if (variant === 'async_attr_wrong') span.attributes[0].name = 'data-prompt-paste';
    if (variant === 'async_attr_case') span.attributes[0].name = 'DATA-PROMPT-LITERAL-PASTE';
    if (variant === 'async_attr_multiple') span.attributes.push({name:'data-extra', value:''});
    if (variant === 'async_value_nonempty') span.attributes[0].value = 'true';
    if (variant === 'async_value_whitespace') span.attributes[0].value = ' ';
    if (variant === 'async_span_class') span.attributes.push({name:'class', value:'rich'});
    if (variant === 'async_span_style') span.attributes.push({name:'style', value:'color:red'});
    if (variant === 'async_span_aria_hidden') span.attributes.push({name:'aria-hidden', value:'true'});
    if (variant === 'async_span_hidden_attr') span.attributes.push({name:'hidden', value:''});
    if (variant === 'async_span_hidden') span.hidden = true;
    if (variant === 'async_span_no_rects') span.getClientRects = () => [];
    if (variant === 'async_span_invisible') span.css = {visibility:'hidden'};
    if (variant === 'async_span_collapsed') span.css = {visibility:'collapse'};
    if (variant === 'async_span_display_none') span.css = {display:'none'};
    if (variant === 'async_span_transparent') span.css = {opacity:'0'};
    if (variant === 'async_children_zero') span.childNodes = [];
    if (variant === 'async_children_multiple') span.childNodes.push(text(''));
    if (variant === 'async_children_split') span.childNodes = [text(lines[0].slice(0, 1)), text(lines[0].slice(1))];
    if (variant === 'async_child_nested') span.childNodes = [literalSpan(lines[0])];
    if (variant === 'async_child_rich') span.childNodes = [Object.assign(element(), {
      nodeType:1, tagName:'B', childNodes:[text(lines[0])], textContent:lines[0]})];
    if (variant === 'async_child_comment') span.childNodes = [{nodeType:8, textContent:lines[0]}];
    if (variant === 'async_child_br') span.childNodes = [br(false)];
    if (variant === 'async_span_wrong_tag') span.tagName = 'EM';
    if (variant === 'async_root_span') box.childNodes[0] = span;
    if (variant === 'async_leaf_duplicate') span.childNodes[0].textContent += lines[0];
    if (variant === 'async_leaf_altered') span.childNodes[0].textContent += ' edit';
    if (variant === 'async_br_wrong_class') emptyBreak.attributes[0].value = 'other';
    if (variant === 'async_br_extra_class') emptyBreak.attributes[0].value += ' rich';
    if (variant === 'async_br_multiple_attrs') emptyBreak.attributes.push({name:'data-extra', value:''});
    if (variant === 'async_br_child') emptyBreak.childNodes = [text('')];
  }
  if (variant === 'async_invalid_trailing_break') box.childNodes[0].childNodes.push(br(true));
  if (variant === 'async_overbound_nodes') box.childNodes[0].childNodes.push(...Array.from(
    {length:2 * expected.length + 2}, () => text('')));
  if (variant === 'async_rich_equal_text') {
    const rich = Object.assign(element(), {nodeType:1, tagName:'SPAN',
      textContent:expected, childNodes:[text(expected)]});
    const richBlock = paragraph(expected); richBlock.childNodes = [rich];
    box.childNodes = [richBlock];
  }
  if (variant === 'async_hidden_duplicate') rendered.pop();
  const treeText = node => node.nodeType === 3 || node.nodeType === 8 ? node.textContent :
    (node.textContent = node.childNodes.map(treeText).join(''));
  box.textContent = box.childNodes.map(treeText).join('');
  box.innerText = variant === 'async_rich_equal_text' ? expected : rendered.join('\n\n');
  if (variant === 'async_extra_rendered_separator') box.innerText += '\n';
  if (variant === 'async_altered_rendered_text') box.innerText += ' altered';
  send.disabled = variant === 'async_send_disabled';
  if (variant === 'async_send_hidden') send.getClientRects = () => [];
  if (variant === 'async_send_aria_disabled') send.getAttribute = () => 'true';
  if (variant === 'async_send_multiple') controls.push(element());
  if (variant === 'async_send_absent') controls = [];
  if (variant === 'async_send_replaced') controls = [Object.assign(element(), {disabled:false, click:send.click})];
  if (variant === 'async_form_replaced') box.parentElement = {tagName:'FORM', parentElement:null,
    contains:form.contains, querySelectorAll:form.querySelectorAll};
  if (variant === 'async_no_form') box.parentElement = null;
  if (variant === 'async_nested_forms') form.parentElement = {tagName:'FORM', parentElement:null};
  if (variant === 'async_multiple_composers') elements[args.composer].push(element());
  if (variant === 'async_composer_replaced') elements[args.composer] = [element()];
  if (variant === 'async_page_replaced') global.document = {...document};
  if (variant === 'async_route_change') location.href = input.otherUrl;
  if (variant === 'async_route_roundtrip') {
    history.pushState({}, '', input.otherUrl); history.replaceState({}, '', input.url);
  }
  if (variant === 'async_expired') clock = args.ttl;
  if (variant === 'async_challenge_changed') proof = {...proof, challenge:'ff'.repeat(32)};
  if (variant === 'async_nonce_changed') proof = {...proof, document_nonce:'ff'.repeat(32)};
  if (variant === 'async_busy') elements[args.stop] = [element()];
  if (variant === 'async_nonregular') elements[args.nonregular] = [element()];
  if (variant === 'async_login') elements[args.login] = [element()];
}
// Await only the scheduled application update, never use elapsed time as proof.
if (!literal && reconciliation) await reconciliation;
if (scenario === 'send_disabled') send.disabled = true;
if (scenario === 'send_aria_disabled') send.getAttribute = () => 'true';
if (scenario === 'send_absent') controls = [];
if (scenario === 'send_multiple') controls.push(element());
if (scenario === 'no_form') box.parentElement = null;
if (scenario === 'nested_forms') form.parentElement = {tagName:'FORM', parentElement:null};
if (scenario === 'submit_edit') box.innerText = box.textContent += ' Human edit';
if (scenario === 'submit_route') location.href = input.otherUrl;
if (scenario === 'submit_busy') elements[args.stop] = [element()];
if (scenario === 'submit_composer') elements[args.composer] = [Object.assign(element(), {innerText:staged, textContent:staged})];
if (input.readyState) proof = applyReadyState(input.readyState, proof);
const readyPolls = [proof ? pollReady(proof) : false];
if (literal && reconciliation) {
  readyPolls.push(pollReady(proof));
  await reconciliation;
  readyPolls.push(pollReady(proof));
}
const ready = readyPolls.at(-1), diverged = box.innerText !== box.textContent;
const productionGrammar = {
  blocks:box.childNodes.filter(node => node.nodeType === 1 && node.tagName === 'P' && !node.attributes.length).length,
  literals:box.childNodes.filter(node => node.childNodes?.length === 1 &&
    node.childNodes[0].tagName === 'SPAN' && node.childNodes[0].attributes.length === 1 &&
    node.childNodes[0].attributes[0].name === 'data-prompt-literal-paste' &&
    node.childNodes[0].attributes[0].value === '' && node.childNodes[0].childNodes.length === 1 &&
    node.childNodes[0].childNodes[0].nodeType === 3).length,
  emptyTrailing:box.childNodes.filter(node => node.childNodes?.length === 1 &&
    node.childNodes[0].tagName === 'BR' && node.childNodes[0].attributes.length === 1 &&
    node.childNodes[0].attributes[0].value === 'ProseMirror-trailingBreak').length,
};
const emptyRoot = box.childNodes.find(node => node.textContent === '');
const emptyParagraphFixture = emptyRoot ? {tag:emptyRoot.tagName, attributes:emptyRoot.attributes,
  text:emptyRoot.textContent, children:emptyRoot.childNodes.map(child => ({tag:child.tagName,
    attributes:child.attributes, text:child.textContent, children:child.childNodes?.length}))} : null;
const renderedFixture = {length:box.innerText.length,
  newlineRuns:[...box.innerText.matchAll(/\n+/g)].map(match => [match.index, match[0].length])};
function changeAfterReady() {
  if (scenario.endsWith('_draft')) {
    const child = box.childNodes[0].childNodes[0];
    (literal ? child.childNodes[0] : child).textContent += ' edit';
  }
  if (scenario.endsWith('_send')) {
    const replacement = Object.assign(element(), {disabled:false, click:send.click});
    controls = [replacement];
  }
  if (scenario.endsWith('_form')) {
    box.parentElement = {tagName:'FORM', parentElement:null, contains:form.contains,
      querySelectorAll:form.querySelectorAll};
  }
  if (scenario.endsWith('_challenge')) proof = {...proof, challenge:'ff'.repeat(32)};
}
if (variant.startsWith('async_before_prove_')) changeAfterReady();
const revalidated = proof ? api.prove(proof) : false;
if (variant.startsWith('async_before_click_')) changeAfterReady();
const sameScopedControl = controls.length === 1 && controls[0] === send && box.parentElement === form;
function applySubmittedState(state) {
  // Mutate only after the consumed click, keeping all pre-submit fixtures exact.
  const root = paragraph(''); root.childNodes = [br(true)];
  const child = root.childNodes[0];
  box.innerText = '\n'; box.textContent = ''; box.childNodes = [root];
  if (state === 'fully_empty') box.innerText = '';
  if (state === 'fully_empty_no_nodes') {box.innerText = ''; box.childNodes = [];}
  if (state === 'marked_root') root.attributes = [{name:'data-empty-paragraph', value:'true'}];
  if (state === 'attributed_root') root.attributes = [
    {name:'class', value:'editor-empty'}, {name:'data-empty-paragraph', value:'false'}];
  if (state === 'root_div') root.tagName = 'DIV';
  if (state === 'root_span') root.tagName = 'SPAN';
  if (state === 'root_text') box.childNodes = [text('')];
  if (state === 'root_comment') box.childNodes = [{nodeType:8, textContent:''}];
  if (state === 'roots_absent') box.childNodes = [];
  if (state === 'roots_multiple') box.childNodes.push(paragraph(''));
  if (state === 'root_extra_text') box.childNodes.push(text(''));
  if (state === 'children_absent') root.childNodes = [];
  if (state === 'children_multiple') root.childNodes.push(br(true));
  if (state === 'child_extra_text') root.childNodes.push(text(''));
  if (state === 'child_text') root.childNodes = [text('')];
  if (state === 'child_comment') root.childNodes = [{nodeType:8, textContent:''}];
  if (state === 'child_span') child.tagName = 'SPAN';
  if (state === 'bare_br') child.attributes = [];
  if (state === 'br_wrong_attribute') child.attributes[0].name = 'data-class';
  if (state === 'br_wrong_class') child.attributes[0].value = 'other';
  if (state === 'br_extra_class') child.attributes[0].value += ' rich';
  if (state === 'br_class_whitespace') child.attributes[0].value += ' ';
  if (state === 'br_class_case') child.attributes[0].value = 'prosemirror-trailingBreak';
  if (state === 'br_decorated') child.attributes.push({name:'title', value:''});
  if (state === 'br_text_child') child.childNodes = [text('')];
  if (state === 'br_element_child') child.childNodes = [br(true)];
  if (state === 'br_comment_child') child.childNodes = [{nodeType:8, textContent:''}];
  if (state === 'root_content') root.textContent = box.textContent = 'content';
  if (state === 'br_content') child.textContent = root.textContent = box.textContent = 'content';
  const whitespace = {inner_space:' ', inner_tab:'\t', inner_cr:'\r', inner_crlf:'\r\n',
    inner_nbsp:'\u00a0', inner_two_lfs:'\n\n', inner_three_lfs:'\n\n\n',
    inner_lf_space:'\n ', inner_space_lf:' \n', inner_line_separator:'\u2028'};
  if (Object.hasOwn(whitespace, state)) box.innerText = whitespace[state];
  if (state === 'text_space') box.textContent = ' ';
  if (state === 'text_lf') box.textContent = '\n';
  if (state === 'text_content') box.textContent = 'content';
  for (const [prefix, node] of [['root_', root], ['br_', child]]) {
    if (state === prefix + 'hidden') node.hidden = true;
    if (state === prefix + 'aria_hidden') node.getAttribute = name => name === 'aria-hidden' ? 'true' : null;
    if (state === prefix + 'no_rects') node.getClientRects = () => [];
    if (state === prefix + 'invisible') node.css = {visibility:'hidden'};
    if (state === prefix + 'collapsed') node.css = {visibility:'collapse'};
    if (state === prefix + 'display_none') node.css = {display:'none'};
    if (state === prefix + 'transparent') node.css = {opacity:'0'};
  }
  if (state === 'page_replaced') global.document = {...document};
  if (state === 'route_changed') location.href = input.otherUrl;
  if (state === 'route_roundtrip') {
    history.pushState({}, '', input.otherUrl); history.replaceState({}, '', input.url);
  }
  if (state === 'main_absent') elements.main = [];
  if (state === 'main_multiple') elements.main.push(element());
  if (state === 'login') elements[args.login] = [element()];
  if (state === 'nonregular') elements[args.nonregular] = [element()];
  if (state === 'composer_absent') elements[args.composer] = [];
  if (state === 'composer_multiple') elements[args.composer].push(element());
  if (state === 'composer_replaced') elements[args.composer] = [{...box}];
  if (state === 'composer_hidden') box.css = {visibility:'hidden'};
  if (state === 'composer_no_rects') box.getClientRects = () => [];
  if (state === 'send_enabled') send.disabled = false;
  if (state === 'send_multiple') controls.push(Object.assign(element(), {disabled:true}));
  if (state === 'send_absent') controls = [];
  if (state === 'send_aria_disabled') {
    send.disabled = false; send.getAttribute = () => 'true';
  }
  if (state === 'no_form') box.parentElement = null;
  if (state === 'nested_forms') form.parentElement = {tagName:'FORM', parentElement:null};
  if (state === 'finished') api.finish();
}
const witnessBeforeSubmit = input.submittedState ? api.submitted() : null;
const submitted = ready ? api.submit(proof) : false;
const duplicate = proof ? api.submit(proof) : false;
if (input.submittedState) applySubmittedState(input.submittedState);
const submittedFixture = input.submittedState ? {
  textContent:box.textContent, innerText:box.innerText,
  roots:box.childNodes.map(node => ({type:node.nodeType, tag:node.tagName, text:node.textContent,
    attributes:node.attributes, children:node.childNodes?.map(child => ({type:child.nodeType,
      tag:child.tagName, text:child.textContent, attributes:child.attributes, children:child.childNodes?.length}))})),
} : null;
const witnessed = api.submitted();
const witnessedAgain = input.submittedState ? api.submitted() : null;
api.finish(); const cleared = api.inspect() === null;
elements[args.stop] = [];
const nextApi = eval('(' + input.install + ')')(args), rearmed = !!nextApi;
if (rearmed) button.listener({isTrusted:true, currentTarget:button});
const next = rearmed ? nextApi.inspect() : null;
process.stdout.write(JSON.stringify({installed, proved, inserted, ready, submitted, duplicate, witnessed,
  clicks, inserts, notifications, staged, cleared, rearmed, beforeReconciliation, diverged, revalidated, sameScopedControl,
  readyPolls, productionGrammar, emptyParagraphFixture, renderedFixture, focuses, operations, readyDiagnostics,
  witnessBeforeSubmit, witnessedAgain, submittedFixture,
  fresh:!!next && !!genuineProof && next.challenge !== genuineProof.challenge}));
})().catch(error => {process.stderr.write(String(error)); process.exitCode=1;});
"""


@pytest.mark.parametrize("scenario", [
    "valid", "blocks_exact", "forged_page_api", "no_main", "login", "nonregular", "busy", "disabled", "multiple_composers", "invalid_url",
    "untrusted", "copied_token", "wrong_challenge", "wrong_document", "expired", "route_change", "route_roundtrip",
    "page_replacement", "empty_draft", "rich_draft", "overbound_draft", "existing_envelope", "gesture_edit",
    "focus_route", "focus_edit", "selection_absent", "native_false", "native_edit", "input_edit", "input_route",
    "send_disabled", "send_aria_disabled", "send_absent", "send_multiple", "no_form", "nested_forms",
    "submit_edit", "submit_route", "submit_busy", "submit_composer",
])
def test_actual_page_gesture_edit_and_submit_scripts(scenario):
    result, metadata = run_dom(scenario)
    if scenario in {"valid", "blocks_exact"}:
        assert all(result[k] for k in ("installed", "proved", "inserted", "ready", "submitted", "witnessed"))
        assert result["clicks"] == result["inserts"] == result["notifications"] == 1
        expected = "Human authored draft\nsecond exact line  \n\n" + metadata
        assert result["staged"] == (expected.replace("\n", "") if scenario == "blocks_exact" else expected)
        assert result["duplicate"] is False and result["cleared"] is True
        assert result["rearmed"] is True and result["fresh"] is True
    else:
        assert result["clicks"] == 0
        if scenario in {"untrusted", "copied_token", "wrong_challenge", "wrong_document", "expired",
                        "route_change", "route_roundtrip", "page_replacement"}:
            assert result["proved"] is False and result["inserts"] == 0
        if scenario in {"focus_route", "focus_edit", "selection_absent", "empty_draft", "rich_draft",
                        "overbound_draft", "existing_envelope", "gesture_edit"}:
            assert result["inserts"] == 0
        if scenario in {"native_false", "input_route"}:
            assert result["inserted"] is False
        if scenario in {"native_edit", "input_edit"}:
            assert result["inserted"] is True and result["ready"] is False
        if scenario == "focus_edit":
            assert result["staged"] == "Human authored draft\nsecond exact line   Human edit"


def run_dom(scenario, draft=None, *, metadata=None, diagnostic=False, install=None, ready_state=None,
            submitted_state=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the isolated JavaScript DOM harness")
    if metadata is None:
        metadata = origin.envelope(origin.HANDLE_PREFIX + "d" * 64, 1)
    arguments = dict(slot=origin.SLOT, composer=wake.COMPOSER, send=wake.SEND,
                     stop=wake.STOP, login=wake.LOGIN, nonregular=wake.NONREGULAR,
                     ttl=origin.CHALLENGE_TTL_MS, maxDraft=origin.MAX_DRAFT_CHARS)
    process = subprocess.run([node, "-e", DOM_HARNESS], input=json.dumps(dict(
        scenario=scenario, draft=draft, args=arguments, install=install or origin.INSTALL, metadata=metadata,
        diagnostic=diagnostic, readyState=ready_state, submittedState=submitted_state,
        url=URL_A, otherUrl=URL_B)), capture_output=True, text=True, check=True, timeout=10)
    return json.loads(process.stdout), metadata


@pytest.mark.parametrize("diagnostic", [False, True])
@pytest.mark.parametrize("state", [
    "fully_empty", "fully_empty_no_nodes", "one_lf",
    "marked_root", "attributed_root", "send_absent", "send_aria_disabled",
])
def test_submitted_preserves_fully_empty_and_accepts_only_the_exact_one_lf_shape(state, diagnostic):
    result, _ = run_dom("async_literal_empty_exact", diagnostic=diagnostic, submitted_state=state)
    assert result["inserted"] and result["ready"] and result["revalidated"] and result["sameScopedControl"]
    assert result["readyPolls"] == [False, False, True]
    assert result["witnessBeforeSubmit"] is False
    assert result["submitted"] and result["witnessed"] and result["witnessedAgain"]
    assert result["clicks"] == result["inserts"] == result["notifications"] == 1
    assert result["duplicate"] is False and result["cleared"] is True
    fixture = result["submittedFixture"]
    assert fixture["textContent"] == ""
    assert fixture["innerText"] == ("" if state.startswith("fully_empty") else "\n")
    if not state.startswith("fully_empty"):
        root, = fixture["roots"]
        assert root["type"] == 1 and root["tag"] == "P" and root["text"] == ""
        assert root["children"] == [dict(type=1, tag="BR", text="",
                                       attributes=[dict(name="class", value="ProseMirror-trailingBreak")],
                                       children=0)]


@pytest.mark.parametrize("state", [
    "root_div", "root_span", "root_text", "root_comment", "roots_absent", "roots_multiple", "root_extra_text",
    "children_absent", "children_multiple", "child_extra_text", "child_text", "child_comment", "child_span",
    "bare_br", "br_wrong_attribute", "br_wrong_class", "br_extra_class", "br_class_whitespace", "br_class_case",
    "br_decorated", "br_text_child", "br_element_child", "br_comment_child", "root_content", "br_content",
    "root_hidden", "root_aria_hidden", "root_no_rects", "root_invisible", "root_collapsed", "root_display_none",
    "root_transparent", "br_hidden", "br_aria_hidden", "br_no_rects", "br_invisible", "br_collapsed",
    "br_display_none", "br_transparent", "inner_space", "inner_tab", "inner_cr", "inner_crlf", "inner_nbsp",
    "inner_two_lfs", "inner_three_lfs", "inner_lf_space", "inner_space_lf", "inner_line_separator",
    "text_space", "text_lf", "text_content",
])
def test_submitted_one_lf_shape_content_and_whitespace_near_misses_fail_closed(state):
    result, _ = run_dom("async_literal_empty_exact", submitted_state=state)
    assert result["inserted"] and result["ready"] and result["revalidated"] and result["submitted"]
    assert result["sameScopedControl"] and result["witnessBeforeSubmit"] is False
    assert result["witnessed"] is False and result["witnessedAgain"] is False
    assert result["clicks"] == result["inserts"] == result["notifications"] == 1
    assert result["duplicate"] is False and result["cleared"] is True


@pytest.mark.parametrize("state", [
    "page_replaced", "route_changed", "route_roundtrip", "main_absent", "main_multiple", "login", "nonregular",
    "composer_absent", "composer_multiple", "composer_replaced", "composer_hidden", "composer_no_rects",
    "send_enabled", "send_multiple", "no_form", "nested_forms", "finished",
])
def test_submitted_one_lf_keeps_page_composer_and_scoped_send_guards(state):
    result, _ = run_dom("async_literal_empty_exact", submitted_state=state)
    assert result["inserted"] and result["ready"] and result["revalidated"] and result["submitted"]
    assert result["sameScopedControl"] and result["witnessBeforeSubmit"] is False
    assert result["witnessed"] is False and result["witnessedAgain"] is False
    assert result["clicks"] == result["inserts"] == result["notifications"] == 1
    assert result["duplicate"] is False and result["cleared"] is True


@pytest.mark.parametrize("scenario", ["async_exact", "async_div_exact", "async_inline_exact", "async_trailing_exact", "async_wait_exact"])
@pytest.mark.parametrize("draft", [None, "  exact e\u0301 \u00a0\nsecond line  ",
                                  "Human CRLF draft\r\nsecond exact line  "])
def test_async_plain_editor_reconciliation_preserves_exact_logical_draft(scenario, draft):
    result, metadata = run_dom(scenario, draft)
    assert result["installed"] and result["proved"] and result["inserted"]
    assert result["beforeReconciliation"] is (scenario != "async_wait_exact")
    assert result["diverged"] is True
    assert result["ready"] and result["revalidated"] and result["submitted"] and result["witnessed"]
    assert result["sameScopedControl"] is True
    assert result["staged"] == (draft or "Human authored draft\nsecond exact line  ") + "\n\n" + metadata
    assert result["clicks"] == result["inserts"] == result["notifications"] == 1
    assert result["duplicate"] is False


def test_production_five_block_literal_paste_reconciles_only_during_ready_polling():
    result, metadata = run_dom("async_literal_exact")
    assert result["installed"] and result["proved"] and result["inserted"]
    assert result["beforeReconciliation"] is False
    assert result["readyPolls"] == [False, False, True]
    assert result["productionGrammar"] == {"blocks": 5, "literals": 4, "emptyTrailing": 1}
    assert result["staged"] == "Human exact draft  \n\n" + metadata
    assert result["diverged"] and result["ready"] and result["revalidated"]
    assert result["sameScopedControl"] and result["submitted"] and result["witnessed"]
    assert result["clicks"] == result["inserts"] == result["notifications"] == 1
    assert result["duplicate"] is False


@pytest.mark.parametrize("diagnostic", [False, True])
def test_production_marked_empty_paragraph_reaches_readiness_with_literal_paste(diagnostic):
    result, metadata = run_dom("async_literal_empty_exact", diagnostic=diagnostic)
    assert result["installed"] and result["proved"] and result["inserted"]
    assert result["beforeReconciliation"] is False
    assert result["readyPolls"] == [False, False, True]
    assert result["productionGrammar"] == {"blocks": 4, "literals": 4, "emptyTrailing": 1}
    assert result["emptyParagraphFixture"] == dict(
        tag="P", attributes=[dict(name="data-empty-paragraph", value="true")], text="",
        children=[dict(tag="BR", attributes=[dict(name="class", value="ProseMirror-trailingBreak")],
                       text="", children=0)])
    assert result["staged"] == "Human exact draft  \n\n" + metadata
    assert result["diverged"] and result["ready"] and result["revalidated"]
    assert result["sameScopedControl"] and result["submitted"] and result["witnessed"]
    assert result["clicks"] == result["inserts"] == result["notifications"] == 1
    assert result["duplicate"] is False
    if diagnostic:
        assert result["readyDiagnostics"][-1] == dict(ready=True, category=None, proof_category=None)


@pytest.mark.parametrize("variant", [
    "root_div", "root_wrong_tag", "marker_wrong_name", "marker_wrong_value", "marker_missing_value",
    "marker_case", "marker_whitespace", "root_extra_attribute", "marked_nonempty",
    "empty_child_plain_br", "empty_child_wrong_tag", "empty_child_text", "empty_child_comment",
    "empty_children_multiple", "empty_children_absent", "br_wrong_class", "br_extra_class",
    "br_multiple_attrs", "br_child", "hidden_br", "empty_root_hidden", "empty_root_no_rects",
    "empty_root_invisible", "empty_root_collapsed", "empty_root_display_none", "empty_root_transparent",
])
def test_marked_empty_paragraph_shape_and_visibility_deviations_fail_closed(variant):
    result, _ = run_dom("async_literal_empty_" + variant, diagnostic=True)
    assert result["inserted"] and result["beforeReconciliation"] is False
    assert not any(result["readyPolls"])
    assert result["readyDiagnostics"][-1] == dict(ready=False, category="EXACT_TEXT", proof_category=None)
    assert result["ready"] is False and result["revalidated"] is False
    assert result["submitted"] is False and result["duplicate"] is False and result["clicks"] == 0
    assert result["inserts"] == result["notifications"] == 1


@pytest.mark.parametrize("scenario", ["async_literal_exact", "async_literal_div_exact", "async_literal_wait_exact",
                                      "async_literal_empty_exact", "async_literal_empty_wait_exact"])
@pytest.mark.parametrize("draft", ["  exact e\u0301 \u00a0\nsecond line  ",
                                  "Human CRLF draft\r\nsecond exact line  ",
                                  "\nHuman exact draft\n\nsecond line  \n"])
def test_literal_paste_preserves_exact_unicode_whitespace_and_logical_separators(scenario, draft):
    result, metadata = run_dom(scenario, draft)
    assert result["inserted"] and result["beforeReconciliation"] is False
    assert result["staged"] == draft + "\n\n" + metadata
    assert result["ready"] and result["revalidated"] and result["sameScopedControl"]
    assert result["submitted"] and result["clicks"] == 1


@pytest.mark.parametrize("variant", [
    "attr_missing", "attr_wrong", "attr_case", "attr_multiple", "value_nonempty", "value_whitespace",
    "span_class", "span_style", "span_aria_hidden", "span_hidden_attr", "span_hidden", "span_no_rects",
    "span_invisible", "span_collapsed", "span_display_none", "span_transparent",
    "children_zero", "children_multiple", "children_split", "child_nested", "child_rich", "child_comment",
    "child_br", "span_wrong_tag", "root_span", "leaf_duplicate", "leaf_altered",
    "br_wrong_class", "br_extra_class", "br_multiple_attrs", "br_child", "decorated_br", "hidden_br",
    "missing_separator", "extra_separator", "reordered_separator", "reordered_lines", "altered_draft",
    "altered_bootstrap", "trimmed_draft", "duplicated_content", "hidden_duplicate", "hidden_block",
    "decorated_block", "nested_rich", "rich_equal_text", "unknown_node", "invalid_trailing_break",
    "overbound_nodes", "extra_rendered_separator", "altered_rendered_text",
    "send_disabled", "send_hidden", "send_aria_disabled", "send_absent", "send_multiple",
    "no_form", "nested_forms", "multiple_composers", "composer_replaced", "page_replaced",
    "route_change", "route_roundtrip", "expired", "challenge_changed", "nonce_changed",
    "busy", "nonregular", "login", "never_reconciled",
])
@pytest.mark.parametrize("representation", ["async_literal_", "async_literal_empty_"])
def test_literal_paste_deviations_or_unproved_readiness_block_submit(variant, representation):
    result, _ = run_dom(representation + variant)
    assert result["inserted"] and result["beforeReconciliation"] is False
    assert not any(result["readyPolls"])
    assert result["ready"] is False and result["revalidated"] is False
    assert result["submitted"] is False and result["duplicate"] is False and result["clicks"] == 0
    assert result["inserts"] == result["notifications"] == 1


@pytest.mark.parametrize("variant,draft", [
    ("unicode_normalized", "Human e\u0301 draft"),
    ("crlf_collapsed", "Human CRLF draft\r\nsecond exact line  "),
])
@pytest.mark.parametrize("representation", ["async_literal_", "async_literal_empty_"])
def test_literal_paste_never_normalizes_human_draft_bytes(variant, draft, representation):
    result, _ = run_dom(representation + variant, draft)
    assert result["inserted"] and result["ready"] is False and result["clicks"] == 0


@pytest.mark.parametrize("draft", ["\nHuman exact draft\n\nsecond line  \n", "Human\n\n\n  \n"])
def test_async_empty_blocks_preserve_human_leading_trailing_and_repeated_newlines(draft):
    result, metadata = run_dom("async_exact", draft)
    assert result["staged"] == draft + "\n\n" + metadata
    assert result["inserted"] and result["ready"] and result["sameScopedControl"]
    assert result["submitted"] and result["clicks"] == 1


@pytest.mark.parametrize("scenario", [
    "async_missing_separator", "async_extra_separator", "async_reordered_separator", "async_reordered_lines",
    "async_missing_inline_break", "async_extra_inline_break", "async_altered_draft", "async_altered_bootstrap",
    "async_trimmed_draft", "async_duplicated_content", "async_hidden_block", "async_hidden_br",
    "async_hidden_duplicate", "async_transparent_block", "async_collapsed_block", "async_nested_rich",
    "async_rich_equal_text", "async_unknown_node", "async_decorated_block", "async_decorated_br",
    "async_invalid_trailing_break", "async_overbound_nodes", "async_extra_rendered_separator",
    "async_altered_rendered_text", "async_send_disabled", "async_send_hidden", "async_send_aria_disabled",
    "async_send_multiple", "async_send_replaced", "async_form_replaced", "async_no_form", "async_nested_forms", "async_multiple_composers",
    "async_composer_replaced", "async_page_replaced", "async_route_change", "async_route_roundtrip",
    "async_expired", "async_challenge_changed", "async_nonce_changed",
])
def test_async_nonexact_or_ambiguous_editor_reconciliation_blocks_submit(scenario):
    result, _ = run_dom(scenario)
    assert result["inserted"] and result["beforeReconciliation"] is True
    assert result["ready"] is False and result["revalidated"] is False
    assert result["submitted"] is False and result["clicks"] == 0
    assert result["inserts"] == result["notifications"] == 1


@pytest.mark.parametrize("scenario,draft", [
    ("async_unicode_normalized", "Human e\u0301 draft\nsecond exact line  "),
    ("async_crlf_collapsed", "Human CRLF draft\r\nsecond exact line  "),
])
def test_async_reconciliation_does_not_canonicalize_draft_bytes(scenario, draft):
    result, _ = run_dom(scenario, draft)
    assert result["inserted"] and result["ready"] is False and result["clicks"] == 0


@pytest.mark.parametrize("boundary", ["prove", "click"])
@pytest.mark.parametrize("change", ["draft", "send", "form", "challenge"])
@pytest.mark.parametrize("representation", ["async_", "async_literal_", "async_literal_empty_"])
def test_reconciled_editor_and_same_scoped_control_revalidate_before_intent_and_click(boundary, change, representation):
    result, _ = run_dom(representation + "before_" + boundary + "_" + change)
    assert result["inserted"] and result["ready"]
    assert result["revalidated"] is (boundary == "click")
    assert result["submitted"] is False and result["clicks"] == 0


@pytest.mark.parametrize("boundary", [2, 3])
@pytest.mark.parametrize("field", ["generation", "handle", "cdp_endpoint"])
@pytest.mark.parametrize("representation", ["async_literal_exact", "async_literal_empty_exact"])
def test_binding_race_after_reconciled_readiness_still_prevents_attempt_click(registry, boundary, field, representation):
    class ReconciledAdapter(Adapter):
        def ready(self, proof):
            super().ready(proof)
            result, _ = run_dom(representation)
            assert result["inserted"] and result["ready"] and result["revalidated"]

    def race(stage, adapter):
        if stage == "revalidate" and adapter.revalidations == boundary:
            data = registry.read()
            data["routes"][URL_A][field] = {
                "generation": 2, "handle": origin.HANDLE_PREFIX + "c" * 64,
                "cdp_endpoint": "http://127.0.0.1:9223",
            }[field]
            registry.write(data)

    adapter = ReconciledAdapter(registry, race=race)
    result = origin.bootstrap(registry, adapter)
    assert result.status == "UNPROVED" and adapter.submits == 0
    assert result.reason == ("BINDING_GENERATION_CHANGED" if field == "generation" else "REGISTRY_CONFLICT")
    attempt = registry.read()["routes"][URL_A]["attempt"]
    assert (attempt is None) is (boundary == 2)
    if boundary == 3:
        assert attempt["status"] == "ATTEMPTING"


@pytest.mark.parametrize("reconciles", [True, False])
@pytest.mark.parametrize("diagnostic", [False, True])
@pytest.mark.parametrize("representation", ["async_literal_", "async_literal_empty_"])
def test_literal_reconciliation_bounded_ready_gate_precedes_attempt_intent(
        registry, monkeypatch, reconciles, diagnostic, representation):
    clock = [0.0]
    monkeypatch.setattr(origin.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(origin.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    class DelayedAdapter(Adapter):
        def __init__(self, registry):
            super().__init__(registry)
            self.diagnostic = origin.BootstrapDiagnostics() if diagnostic else None

        def insert(self, proof, metadata):
            super().insert(proof, metadata)
            scenario = representation + ("exact" if reconciles else "never_reconciled")
            self.dom, _ = run_dom(scenario, metadata=metadata, diagnostic=diagnostic)
            assert self.dom["inserted"] and self.dom["beforeReconciliation"] is False
            self.polls, self.ready_returned = [], False

        def call(self, proof, method, value=None):
            assert proof is self.proof and method == ("readyDiagnostic" if diagnostic else "ready") and value is None
            assert registry.read()["routes"][URL_A]["attempt"] is None and self.submits == 0
            self.polls.append(clock[0])
            states = (self.dom["readyDiagnostics"][-len(self.dom["readyPolls"]):]
                      if diagnostic else self.dom["readyPolls"])
            return states[min(len(self.polls) - 1, len(states) - 1)]

        def ready(self, proof):
            super().ready(proof)
            origin.PageBootstrapBrowser.ready(self, proof)
            self.ready_returned = True

        def revalidate(self, proof):
            super().revalidate(proof)
            if self.revalidations >= 2:
                assert self.ready_returned and self.dom["revalidated"]

    adapter = DelayedAdapter(registry)
    result = origin.bootstrap(registry, adapter)
    attempt = registry.read()["routes"][URL_A]["attempt"]
    if reconciles:
        assert adapter.polls == [0.0, 0.05, 0.1]
        assert result.status == "SUBMITTED" and adapter.submits == 1
        assert adapter.revalidations == 3 and attempt["status"] == "SUBMITTED"
    else:
        assert result.status == "UNPROVED" and result.reason == "INSERT_BLOCKED"
        assert adapter.submits == 0 and "submit" not in adapter.trace and attempt is None
        assert all(0 <= value < 3 for value in adapter.polls) and 3 <= clock[0] < 3.1
    if diagnostic:
        assert adapter.diagnostic.as_dict(result)["diagnostic"] == dict(
            phase="READY", category=None if reconciles else "EXACT_TEXT", proof_category=None,
            ready=reconciles, exhausted=not reconciles, observed_categories=["EXACT_TEXT"])


def test_readiness_wait_remains_bounded_and_rechecks_the_same_proof(monkeypatch):
    adapter = browser([Page(observation=observation())])
    proof = adapter.capture()
    clock, calls = [0.0], []
    monkeypatch.setattr(origin.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(origin.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def pending(selected, method, value=None):
        assert selected is proof and method == "ready" and value is None
        calls.append(clock[0])
        return False

    monkeypatch.setattr(adapter, "call", pending)
    with pytest.raises(origin.BootstrapBlocked, match="INSERT_BLOCKED"):
        adapter.ready(proof)
    assert calls and all(0 <= value < 3 for value in calls)
    assert 3 <= clock[0] < 3.1


# Frozen TASK-297 predicates provide an independent short-circuit oracle for the
# TASK-298 observation refactor. All other page code/grammar is shared unchanged.
PUBLISHED_PREDICATES = r"""  const proves = p => !spent && !invalidated && doc === document && button.isConnected &&
    window[slot] === nonce &&
    pending && p.challenge === pending.challenge && p.document_nonce === nonce &&
    p.chat_url === pending.chat_url && normalize(location.href) === pending.chat_url &&
    performance.now() < pending.deadline && surface();
  const ready = p => {
    if (!proves(p) || !staged || surface() !== staged.box || !exactText(staged.box, staged.expected)) return null;
    const controls = sendControls(staged.box);
    if (!controls || controls.length !== 1 || !enabled(controls[0])) return null;
    let form = null;
    for (let parent = staged.box.parentElement; parent; parent = parent.parentElement)
      if (parent.tagName === 'FORM') form = parent;
    if (staged.control && (staged.control !== controls[0] || staged.form !== form)) return null;
    staged.control = controls[0]; staged.form = form;
    return controls[0];
  };
"""

# Test-only lexical injection represents individual predicate inputs, including
# otherwise inaccessible sticky state. It is never part of the production API.
READY_STATE_INJECTION = r"""
  globalThis.applyReadyState = (flags, p) => {
    if (flags.includes('spent')) spent = true;
    if (flags.includes('invalidated')) invalidated = true;
    if (flags.includes('document')) global.document = {...document};
    if (flags.includes('button')) button.isConnected = false;
    if (flags.includes('slot')) delete window[slot];
    if (flags.includes('pending')) pending = null;
    if (flags.includes('challenge')) p = {...p, challenge:'ff'.repeat(32)};
    if (flags.includes('nonce')) p = {...p, document_nonce:'ff'.repeat(32)};
    if (flags.includes('proof_route')) p = {...p, chat_url:input.otherUrl};
    if (flags.includes('location_route')) location.href = input.otherUrl;
    if (flags.includes('deadline')) clock = args.ttl;
    if (flags.includes('surface_proof')) elements[args.stop] = [element()];
    if (flags.includes('unstaged')) staged = null;
    if (flags.includes('composer_identity')) elements[args.composer] = [element()];
    if (flags.includes('exact')) box.innerText += ' edit';
    if (flags.includes('scope')) box.parentElement = null;
    if (flags.includes('count')) controls = [];
    if (flags.includes('multiple')) controls.push(element());
    if (flags.includes('enabled')) send.disabled = true;
    if (flags.includes('aria_enabled')) send.getAttribute = () => 'true';
    if (flags.includes('retained_control') || flags.includes('retained_form')) {
      staged.control = send; staged.form = form;
      if (flags.includes('retained_control'))
        controls = [Object.assign(element(), {disabled:false, click:send.click})];
      if (flags.includes('retained_form')) box.parentElement = {tagName:'FORM', parentElement:null,
        contains:form.contains, querySelectorAll:form.querySelectorAll};
    }
    return p;
  };
"""


def predicate_install(*, published=False, injected=False):
    script = origin.INSTALL
    if published:
        start = script.index("  // Observe only operands")
        end = script.index("  button.addEventListener('click'")
        script = script[:start] + PUBLISHED_PREDICATES + script[end:]
    if injected:
        script = script.replace("  const api = Object.freeze({", READY_STATE_INJECTION + "  const api = Object.freeze({")
    return script


READY_TRUTH_CASES = [
    ([], None, None),
    (["spent"], "PROOF", "SPENT"),
    (["invalidated"], "PROOF", "INVALIDATED"),
    (["document"], "PROOF", "DOCUMENT_IDENTITY"),
    (["button"], "PROOF", "HELPER_BUTTON_CONNECTIVITY"),
    (["slot"], "PROOF", "SLOT_OR_NONCE_BINDING"),
    (["pending"], "PROOF", "PENDING_OR_CHALLENGE_BINDING"),
    (["challenge"], "PROOF", "PENDING_OR_CHALLENGE_BINDING"),
    (["nonce"], "PROOF", "SLOT_OR_NONCE_BINDING"),
    (["proof_route"], "PROOF", "ROUTE_EQUALITY"),
    (["location_route"], "PROOF", "ROUTE_EQUALITY"),
    (["deadline"], "PROOF", "DEADLINE"),
    (["surface_proof"], "PROOF", "SURFACE_PROOF"),
    (["unstaged"], "SURFACE_OR_COMPOSER_IDENTITY", None),
    (["composer_identity"], "SURFACE_OR_COMPOSER_IDENTITY", None),
    (["exact"], "EXACT_TEXT", None),
    (["scope"], "SEND_SCOPE_OR_COUNT", None),
    (["count"], "SEND_SCOPE_OR_COUNT", None),
    (["multiple"], "SEND_SCOPE_OR_COUNT", None),
    (["enabled"], "SEND_ENABLED", None),
    (["aria_enabled"], "SEND_ENABLED", None),
    (["retained_control"], "RETAINED_FORM_OR_CONTROL_IDENTITY", None),
    (["retained_form"], "RETAINED_FORM_OR_CONTROL_IDENTITY", None),
    # Simultaneous failures establish first-blocking-gate attribution/order.
    (["invalidated", "deadline", "exact"], "PROOF", "INVALIDATED"),
    (["composer_identity", "exact", "enabled"], "SURFACE_OR_COMPOSER_IDENTITY", None),
    (["exact", "count", "enabled"], "EXACT_TEXT", None),
    (["count", "enabled"], "SEND_SCOPE_OR_COUNT", None),
    (["enabled", "retained_form"], "SEND_ENABLED", None),
]


@pytest.mark.parametrize("flags,category,proof_category", READY_TRUTH_CASES)
def test_diagnostic_ready_truth_table_and_predicate_order_match_published(flags, category, proof_category):
    published, _ = run_dom("valid", install=predicate_install(published=True, injected=True), ready_state=flags)
    ordinary, _ = run_dom("valid", install=predicate_install(injected=True), ready_state=flags)
    diagnostic, _ = run_dom("valid", diagnostic=True, install=predicate_install(injected=True), ready_state=flags)
    assert ordinary == published
    observations = diagnostic.pop("readyDiagnostics")
    assert diagnostic == {key: value for key, value in ordinary.items() if key != "readyDiagnostics"}
    assert observations == [dict(ready=category is None, category=category, proof_category=proof_category)]
    assert ordinary["ready"] is (category is None)
    assert ordinary["clicks"] == (1 if category is None else 0)
    assert ordinary["inserts"] == ordinary["notifications"] == ordinary["focuses"] == 1


@pytest.mark.parametrize("scenario", [
    "valid", "gesture_edit", "empty_draft", "rich_draft", "native_false",
    "async_literal_exact", "async_literal_wait_exact", "async_literal_never_reconciled",
    "async_literal_empty_exact", "async_literal_empty_marker_missing_value", "async_literal_empty_root_div",
    "async_literal_attr_multiple", "async_literal_children_split", "async_literal_span_hidden",
    "async_literal_br_child", "async_literal_extra_rendered_separator", "async_literal_altered_bootstrap",
    "async_literal_route_roundtrip", "async_literal_composer_replaced", "async_literal_send_multiple",
    "async_literal_send_disabled", "async_literal_before_prove_form", "async_literal_before_click_send",
])
def test_diagnostic_mode_preserves_insert_grammar_reconciliation_and_single_click(scenario):
    published, _ = run_dom(scenario, install=predicate_install(published=True))
    ordinary, _ = run_dom(scenario)
    diagnostic, _ = run_dom(scenario, diagnostic=True)
    assert ordinary == published
    observations = diagnostic.pop("readyDiagnostics")
    assert diagnostic == {key: value for key, value in ordinary.items() if key != "readyDiagnostics"}
    for value in observations:
        assert set(value) == {"ready", "category", "proof_category"}
        assert value["category"] is None or value["category"] in origin.READY_CATEGORIES
        assert value["proof_category"] is None or value["proof_category"] in origin.PROOF_CATEGORIES
    assert ordinary["clicks"] <= 1 and ordinary["duplicate"] is False


def test_observed_271_character_literal_br_render_projection_is_still_ready_success():
    # Synthetic 47-character draft reproduces the observed positions without
    # recording any live handoff bytes or route handle.
    draft = "Synthetic Human draft " + "x" * 25
    assert len(draft) == 47
    ordinary, _ = run_dom("async_literal_exact", draft)
    diagnostic, _ = run_dom("async_literal_exact", draft, diagnostic=True)
    assert ordinary["renderedFixture"] == dict(length=271, newlineRuns=[[47, 5], [75, 2], [245, 2]])
    assert ordinary["productionGrammar"] == dict(blocks=5, literals=4, emptyTrailing=1)
    assert ordinary["readyPolls"] == [False, False, True]
    assert ordinary["ready"] and ordinary["submitted"] and ordinary["clicks"] == 1
    assert diagnostic["readyPolls"] == ordinary["readyPolls"]
    assert diagnostic["readyDiagnostics"][-1] == dict(ready=True, category=None, proof_category=None)


@pytest.mark.parametrize("flags,category,proof_category", READY_TRUTH_CASES)
def test_diagnostic_attempt_result_waits_and_registry_semantics_match_default(
        tmp_path, monkeypatch, flags, category, proof_category):
    clock, sleeps = [0.0], []
    monkeypatch.setattr(origin.time, "monotonic", lambda: clock[0])

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(origin.time, "sleep", sleep)
    monkeypatch.setattr(origin.secrets, "token_hex", lambda size: "d" * 64)
    receipts = []
    for opt_in in (False, True):
        clock[0], sleeps[:] = 0.0, []
        registry = origin.OriginRegistry(tmp_path / ("diagnostic.json" if opt_in else "ordinary.json"))

        class PollingAdapter(Adapter):
            proof_value = staticmethod(origin.PageBootstrapBrowser.proof_value)

            def __init__(self):
                super().__init__(registry)
                self.diagnostic = origin.BootstrapDiagnostics() if opt_in else None
                self.polls, self.writes = [], []

            def insert(self, proof, metadata):
                self.step("insert")
                origin.PageBootstrapBrowser.insert(self, proof, metadata)

            def call(self, proof, method, value=None):
                assert proof is self.proof
                if method == "insert":
                    self.metadata = value["metadata"]
                    self.dom, _ = run_dom("valid", metadata=self.metadata, diagnostic=opt_in,
                                          install=predicate_install(injected=True), ready_state=flags)
                    return self.dom["inserted"]
                assert method == ("readyDiagnostic" if opt_in else "ready") and value is None
                assert registry.read()["routes"][URL_A]["attempt"] is None
                self.polls.append(clock[0])
                return self.dom["readyDiagnostics"][-1] if opt_in else self.dom["ready"]

            def ready(self, proof):
                self.step("ready")
                origin.PageBootstrapBrowser.ready(self, proof)

        adapter = PollingAdapter()
        write = registry.write

        def record_write(data):
            adapter.writes.append(json.loads(json.dumps(data)))
            write(data)

        monkeypatch.setattr(registry, "write", record_write)
        result = origin.bootstrap(registry, adapter)
        receipts.append((result.as_dict(), registry.read(), adapter.trace, adapter.polls,
                         list(sleeps), clock[0], adapter.writes, adapter.submits,
                         adapter.dom["inserts"], adapter.dom["notifications"], adapter.dom["clicks"]))
        if opt_in:
            attribution = adapter.diagnostic.as_dict(result)["diagnostic"]
            assert attribution == dict(phase="READY", category=category, proof_category=proof_category,
                                       ready=category is None, exhausted=category is not None,
                                       observed_categories=[] if category is None else [category])
    assert receipts[0] == receipts[1]
    result, state, trace, polls, waits, elapsed, writes, submits, inserts, notifications, clicks = receipts[0]
    assert inserts == notifications == 1
    if category is None:
        assert result["status"] == "SUBMITTED" and result["reason"] == "ACCEPTED"
        assert polls == [0.0] and waits == [] and elapsed == 0
        assert submits == clicks == 1 and len(writes) == 3
        assert state["routes"][URL_A]["attempt"]["status"] == "SUBMITTED"
    else:
        assert result["status"] == "UNPROVED" and result["reason"] == "INSERT_BLOCKED"
        assert submits == clicks == 0 and len(writes) == 1
        assert state["routes"][URL_A]["attempt"] is None
        assert len(waits) == len(polls) and set(waits) == {0.05}
        assert all(0 <= poll < 3 for poll in polls) and 3 <= elapsed < 3.1


def test_diagnostic_insert_rejection_has_no_authorized_edit_or_ready_poll(tmp_path, monkeypatch):
    monkeypatch.setattr(origin.secrets, "token_hex", lambda size: "d" * 64)
    receipts = []
    for opt_in in (False, True):
        registry = origin.OriginRegistry(tmp_path / ("diagnostic.json" if opt_in else "ordinary.json"))

        class RejectedAdapter(Adapter):
            proof_value = staticmethod(origin.PageBootstrapBrowser.proof_value)

            def insert(self, proof, metadata):
                self.step("insert")
                origin.PageBootstrapBrowser.insert(self, proof, metadata)

            def call(self, proof, method, value=None):
                assert method == "insert"  # No READY poll or alternate edit.
                self.dom, _ = run_dom("gesture_edit", metadata=value["metadata"], diagnostic=opt_in)
                return self.dom["inserted"]

        adapter = RejectedAdapter(registry)
        adapter.diagnostic = origin.BootstrapDiagnostics() if opt_in else None
        result = origin.bootstrap(registry, adapter)
        receipts.append((result.as_dict(), registry.read(), adapter.trace, adapter.submits, adapter.dom["inserts"],
                         adapter.dom["notifications"], adapter.dom["clicks"], adapter.dom["focuses"]))
        if opt_in:
            assert adapter.diagnostic.as_dict(result)["diagnostic"] == dict(
                phase="INSERT", category="INSERT_REJECTED", proof_category=None,
                ready=False, exhausted=False, observed_categories=[])
    assert receipts[0] == receipts[1]
    assert receipts[0][0]["status"] == "UNPROVED" and receipts[0][0]["reason"] == "INSERT_BLOCKED"
    assert receipts[0][3:] == (0, 0, 0, 0, 0)
    assert "ready" not in receipts[0][2]


def test_diagnostic_observed_set_is_finite_and_retains_only_closed_enums():
    diagnostic = origin.BootstrapDiagnostics()
    diagnostic.ready_started()
    for _ in range(100):
        for category in sorted(origin.READY_CATEGORIES):
            diagnostic.ready_observed(dict(ready=False, category=category,
                                           proof_category="INVALIDATED" if category == "PROOF" else None))
    diagnostic.ready_observed(dict(ready=True, category=None, proof_category=None))
    result = diagnostic.as_dict(origin.BootstrapResult("AMBIGUOUS", "SUBMISSION_UNPROVEN"))
    assert result["diagnostic"]["observed_categories"] == sorted(origin.READY_CATEGORIES)
    assert len(result["diagnostic"]["observed_categories"]) == 7
    assert result["result"]["route_handle"] is None and result["result"]["generation"] is None
    assert set(vars(diagnostic)) == {"phase", "category", "proof_category", "ready", "exhausted", "observed_categories"}


@pytest.mark.parametrize("value", [
    True, None, dict(ready=True, category="EXACT_TEXT", proof_category=None),
    dict(ready=False, category="PROOF", proof_category=None),
    dict(ready=False, category=URL_A, proof_category=None),
    dict(ready=False, category="PROOF", proof_category="a" * 64),
    dict(ready=False, category="EXACT_TEXT", proof_category="DEADLINE"),
    dict(ready=False, category=["EXACT_TEXT"], proof_category=None),
    dict(ready=True, category=None, proof_category=None, raw="secret draft"),
])
def test_malformed_diagnostic_output_cannot_authorize_submission_or_export_raw_values(value, monkeypatch):
    diagnostic = origin.BootstrapDiagnostics()
    adapter = browser([])
    adapter.diagnostic = diagnostic
    monkeypatch.setattr(adapter, "call", lambda *args: value)
    with pytest.raises(origin.BootstrapBlocked, match="LOCAL_FAILURE"):
        adapter.ready(object())
    output = diagnostic.as_dict(origin.BootstrapResult("UNPROVED", "LOCAL_FAILURE"))
    assert output["diagnostic"] == dict(phase="READY", category="UNKNOWN", proof_category=None,
                                       ready=False, exhausted=False, observed_categories=["UNKNOWN"])
    assert URL_A not in json.dumps(output) and "secret draft" not in json.dumps(output)


@pytest.mark.parametrize("status,reason", [("SUBMITTED", "ACCEPTED"), ("UNPROVED", "INSERT_BLOCKED"),
                                          ("AMBIGUOUS", "SUBMISSION_UNPROVEN")])
def test_cli_diagnostic_is_explicit_one_result_wrapper_with_identical_exit_status(
        registry, monkeypatch, capsys, status, reason):
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.delenv("PWDEBUG", raising=False)
    receipt = origin.BootstrapResult(status, reason,
                                    origin.HANDLE_PREFIX + "d" * 64 if status == "SUBMITTED" else None,
                                    1 if status == "SUBMITTED" else None)
    calls = []

    class Session:
        def __init__(self, endpoint, path, **options):
            calls.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def observations(self):
            return [object()]

    monkeypatch.setattr(origin, "PageBootstrapBrowser", Session)
    monkeypatch.setattr(origin, "bootstrap", lambda *args: receipt)
    arguments = ["--state", str(registry.path), "--endpoint", ENDPOINT]
    assert origin.main(arguments) == (0 if status == "SUBMITTED" else 1)
    ordinary = capsys.readouterr()
    assert ordinary.err == "" and json.loads(ordinary.out) == receipt.as_dict()
    assert calls[-1] == {}
    assert origin.main(arguments + ["--diagnostic"]) == (0 if status == "SUBMITTED" else 1)
    diagnostic = capsys.readouterr()
    assert diagnostic.err == "" and len(diagnostic.out.splitlines()) == 1
    value = json.loads(diagnostic.out)
    assert set(value) == {"contract", "result", "diagnostic"}
    assert value["contract"] == origin.DIAGNOSTIC_CONTRACT and value["result"] == receipt.as_dict()
    assert isinstance(calls[-1]["diagnostic"], origin.BootstrapDiagnostics)
    assert URL_A not in diagnostic.out and ENDPOINT not in diagnostic.out and str(registry.path) not in diagnostic.out


def test_cli_diagnostic_argument_failure_still_emits_only_the_bounded_contract(capsys):
    assert origin.main(["--diagnostic", "--unknown", URL_A]) == 1
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert output.err == "" and value["contract"] == origin.DIAGNOSTIC_CONTRACT
    assert value["result"] == origin.BootstrapResult("UNPROVED", "INVALID_INPUT").as_dict()
    assert value["diagnostic"]["phase"] == "NOT_REACHED" and URL_A not in output.out


def test_diagnostic_successful_ready_never_blind_resends_an_ambiguous_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(origin.secrets, "token_hex", lambda size: "d" * 64)
    outcomes = []
    for opt_in in (False, True):
        registry = origin.OriginRegistry(tmp_path / ("diagnostic.json" if opt_in else "ordinary.json"))

        class AmbiguousAdapter(Adapter):
            def __init__(self):
                super().__init__(registry, blocked="prove_submission")
                self.diagnostic = origin.BootstrapDiagnostics() if opt_in else None
                self.calls = []

            def ready(self, proof):
                self.step("ready")
                origin.PageBootstrapBrowser.ready(self, proof)

            def call(self, proof, method, value=None):
                self.calls.append(method)
                return dict(ready=True, category=None, proof_category=None) if opt_in else True

        first, second = AmbiguousAdapter(), AmbiguousAdapter()
        receipt = origin.bootstrap(registry, first)
        rejected = origin.bootstrap(registry, second)
        outcomes.append((receipt.as_dict(), rejected.as_dict(), registry.read(), first.trace,
                         second.trace, first.submits, second.submits))
        assert len(first.calls) == 1 and second.calls == []
        if opt_in:
            assert first.diagnostic.ready and not first.diagnostic.exhausted
            assert first.diagnostic.as_dict(receipt)["result"]["route_handle"] is None
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0]["status"] == "AMBIGUOUS" and outcomes[0][0]["reason"] == "SUBMISSION_UNPROVEN"
    assert outcomes[0][1]["reason"] == "ATTEMPT_REQUIRES_HUMAN" and outcomes[0][-2:] == (1, 0)
    assert outcomes[0][2]["routes"][URL_A]["attempt"]["status"] == "AMBIGUOUS"


def test_diagnostic_internal_failure_is_unknown_and_preserves_the_ordinary_result(registry):
    class BrokenAdapter(Adapter):
        def ready(self, proof):
            self.step("ready")
            origin.PageBootstrapBrowser.ready(self, proof)

        def call(self, *args):
            raise RuntimeError(URL_A + " secret draft " + ENDPOINT)

    ordinary = BrokenAdapter(registry)
    receipt = origin.bootstrap(registry, ordinary)
    diagnostic = BrokenAdapter(registry)
    diagnostic.diagnostic = origin.BootstrapDiagnostics()
    result = origin.bootstrap(registry, diagnostic)
    assert result == receipt == origin.BootstrapResult("UNPROVED", "LOCAL_FAILURE")
    assert ordinary.trace == diagnostic.trace and ordinary.submits == diagnostic.submits == 0
    output = diagnostic.diagnostic.as_dict(result)
    assert output["diagnostic"] == dict(phase="READY", category="UNKNOWN", proof_category=None,
                                       ready=False, exhausted=False, observed_categories=["UNKNOWN"])
    assert URL_A not in json.dumps(output) and ENDPOINT not in json.dumps(output)
