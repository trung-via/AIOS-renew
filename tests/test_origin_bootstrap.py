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
let clock = 0, randomCount = 0, clicks = 0, inserts = 0, notifications = 0, button;
global.performance = {now: () => clock};
global.crypto = {getRandomValues: bytes => {bytes.fill(++randomCount); return bytes;}};
global.location = {href: input.url}; global.window = {addEventListener() {}, removeEventListener() {}};
global.history = {pushState(state, title, url) {location.href=url;},
  replaceState(state, title, url) {location.href=url;}};
global.getComputedStyle = element => ({visibility:'visible', display:'block', opacity:'1', ...element.css});
const element = () => ({attributes:[], childNodes:[], getClientRects: () => [1], getAttribute: () => null});
const text = value => ({nodeType:3, textContent:value});
const br = trailing => Object.assign(element(), {nodeType:1, tagName:'BR', textContent:'',
  attributes:trailing ? [{name:'class', value:'ProseMirror-trailingBreak'}] : []});
const paragraph = (line, tagName='P') => Object.assign(element(), {nodeType:1, tagName,
  textContent:line, childNodes:line === '' ? [br(false)] : [text(line)]});
const humanDraft = input.draft ?? 'Human authored draft\nsecond exact line  ';
let reconciliation;
const box = Object.assign(element(), {innerText:humanDraft, textContent:humanDraft, childNodes:[text(humanDraft)],
  focus() {document.activeElement = box;
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
      send.disabled = scenario === 'async_wait_exact';
      reconciliation = Promise.resolve().then(reconcile);
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
  querySelectorAll: selector => {if (selector !== args.send) throw Error('bad form selector'); return controls;}};
box.parentElement = form;
global.document = {
  activeElement:null,
  body:{appendChild(node) {node.isConnected = true; button = node;}},
  querySelectorAll: selector => {if (selector === args.send) throw Error('global Send query');
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
const beforeReconciliation = scenario.startsWith('async_') ? api.ready(proof) : null;
const expected = humanDraft + '\n\n' + input.metadata;
function reconcile() {
  let lines = expected.split('\n');
  if (scenario === 'async_missing_separator') lines.splice(2, 1);
  if (scenario === 'async_extra_separator') lines.splice(2, 0, '');
  if (scenario === 'async_reordered_separator') lines.push(lines.splice(2, 1)[0]);
  if (scenario === 'async_reordered_lines') [lines[0], lines[1]] = [lines[1], lines[0]];
  if (scenario === 'async_altered_draft') lines[0] = 'h' + lines[0].slice(1);
  if (scenario === 'async_altered_bootstrap') lines[lines.length - 2] = lines[lines.length - 2].replace('d'.repeat(64), 'e'.repeat(64));
  if (scenario === 'async_trimmed_draft') lines[1] = lines[1].trimEnd();
  if (scenario === 'async_unicode_normalized') lines = lines.map(line => line.normalize('NFC'));
  if (scenario === 'async_crlf_collapsed') lines = lines.map(line => line.replaceAll('\r', ''));
  if (scenario === 'async_duplicated_content' || scenario === 'async_hidden_duplicate') lines.push(lines[0]);
  const tag = scenario === 'async_div_exact' ? 'DIV' : 'P';
  box.childNodes = lines.map(line => paragraph(line, tag));
  let rendered = lines.map(line => line === '' ? '\n' : line);
  if (['async_inline_exact', 'async_trailing_exact', 'async_missing_inline_break', 'async_extra_inline_break'].includes(scenario)) {
    const trailing = scenario === 'async_trailing_exact';
    const count = trailing ? 3 : 2, group = lines.slice(0, count);
    const children = [];
    group.forEach((line, i) => {if (i) children.push(br(false)); if (line) children.push(text(line));});
    if (trailing) children.push(br(true));
    if (scenario === 'async_missing_inline_break') children.splice(1, 1);
    if (scenario === 'async_extra_inline_break') children.splice(1, 0, br(false));
    const combined = paragraph(group.join('\n'), tag);
    combined.childNodes = children;
    combined.textContent = children.map(child => child.textContent).join('');
    box.childNodes.splice(0, count, combined);
    rendered.splice(0, count, group.join('\n'));
  }
  if (scenario === 'async_hidden_block') box.childNodes[0].hidden = true;
  if (scenario === 'async_hidden_br') box.childNodes[2].childNodes[0].css = {visibility:'hidden'};
  if (scenario === 'async_hidden_duplicate') box.childNodes.at(-1).getClientRects = () => [];
  if (scenario === 'async_transparent_block') box.childNodes[0].css = {opacity:'0'};
  if (scenario === 'async_collapsed_block') box.childNodes[0].css = {display:'none'};
  if (scenario === 'async_nested_rich') {
    box.childNodes[0].childNodes = [Object.assign(element(), {nodeType:1, tagName:'SPAN',
      textContent:lines[0], childNodes:[text(lines[0])]})];
  }
  if (scenario === 'async_unknown_node') box.childNodes[0].childNodes.push({nodeType:8, textContent:''});
  if (scenario === 'async_decorated_block') box.childNodes[0].attributes = [{name:'class', value:'rich'}];
  if (scenario === 'async_decorated_br') box.childNodes[2].childNodes[0].attributes = [{name:'data-rich', value:'true'}];
  if (scenario === 'async_invalid_trailing_break') box.childNodes[0].childNodes.push(br(true));
  if (scenario === 'async_overbound_nodes') box.childNodes[0].childNodes.push(...Array.from(
    {length:2 * expected.length + 2}, () => text('')));
  if (scenario === 'async_rich_equal_text') {
    const rich = Object.assign(element(), {nodeType:1, tagName:'SPAN',
      textContent:expected, childNodes:[text(expected)]});
    const richBlock = paragraph(expected); richBlock.childNodes = [rich];
    box.childNodes = [richBlock];
  }
  if (scenario === 'async_hidden_duplicate') rendered.pop();
  box.textContent = box.childNodes.map(node => node.textContent).join('');
  box.innerText = scenario === 'async_rich_equal_text' ? expected : rendered.join('\n\n');
  if (scenario === 'async_extra_rendered_separator') box.innerText += '\n';
  if (scenario === 'async_altered_rendered_text') box.innerText += ' altered';
  send.disabled = scenario === 'async_send_disabled';
  if (scenario === 'async_send_hidden') send.getClientRects = () => [];
  if (scenario === 'async_send_aria_disabled') send.getAttribute = () => 'true';
  if (scenario === 'async_send_multiple') controls.push(element());
  if (scenario === 'async_send_replaced') controls = [Object.assign(element(), {disabled:false, click:send.click})];
  if (scenario === 'async_form_replaced') box.parentElement = {tagName:'FORM', parentElement:null,
    contains:form.contains, querySelectorAll:form.querySelectorAll};
  if (scenario === 'async_no_form') box.parentElement = null;
  if (scenario === 'async_nested_forms') form.parentElement = {tagName:'FORM', parentElement:null};
  if (scenario === 'async_multiple_composers') elements[args.composer].push(element());
  if (scenario === 'async_composer_replaced') elements[args.composer] = [element()];
  if (scenario === 'async_page_replaced') global.document = {...document};
  if (scenario === 'async_route_change') location.href = input.otherUrl;
  if (scenario === 'async_route_roundtrip') {
    history.pushState({}, '', input.otherUrl); history.replaceState({}, '', input.url);
  }
  if (scenario === 'async_expired') clock = args.ttl;
  if (scenario === 'async_challenge_changed') proof = {...proof, challenge:'ff'.repeat(32)};
  if (scenario === 'async_nonce_changed') proof = {...proof, document_nonce:'ff'.repeat(32)};
}
// Await only the scheduled application update, never use elapsed time as proof.
if (reconciliation) await reconciliation;
const diverged = box.innerText !== box.textContent;
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
const ready = proof ? api.ready(proof) : false;
function changeAfterReady() {
  if (scenario.endsWith('_draft')) box.childNodes[0].childNodes[0].textContent += ' edit';
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
if (scenario.startsWith('async_before_prove_')) changeAfterReady();
const revalidated = proof ? api.prove(proof) : false;
if (scenario.startsWith('async_before_click_')) changeAfterReady();
const sameScopedControl = controls.length === 1 && controls[0] === send && box.parentElement === form;
const submitted = ready ? api.submit(proof) : false;
const duplicate = proof ? api.submit(proof) : false;
const witnessed = api.submitted();
api.finish(); const cleared = api.inspect() === null;
elements[args.stop] = [];
const nextApi = eval('(' + input.install + ')')(args), rearmed = !!nextApi;
if (rearmed) button.listener({isTrusted:true, currentTarget:button});
const next = rearmed ? nextApi.inspect() : null;
process.stdout.write(JSON.stringify({installed, proved, inserted, ready, submitted, duplicate, witnessed,
  clicks, inserts, notifications, staged, cleared, rearmed, beforeReconciliation, diverged, revalidated, sameScopedControl,
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
        if scenario in {"native_false", "native_edit", "input_edit", "input_route"}:
            assert result["inserted"] is False
        if scenario == "focus_edit":
            assert result["staged"] == "Human authored draft\nsecond exact line   Human edit"


def run_dom(scenario, draft=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the isolated JavaScript DOM harness")
    metadata = origin.envelope(origin.HANDLE_PREFIX + "d" * 64, 1)
    arguments = dict(slot=origin.SLOT, composer=wake.COMPOSER, send=wake.SEND,
                     stop=wake.STOP, login=wake.LOGIN, nonregular=wake.NONREGULAR,
                     ttl=origin.CHALLENGE_TTL_MS, maxDraft=origin.MAX_DRAFT_CHARS)
    process = subprocess.run([node, "-e", DOM_HARNESS], input=json.dumps(dict(
        scenario=scenario, draft=draft, args=arguments, install=origin.INSTALL, metadata=metadata,
        url=URL_A, otherUrl=URL_B)), capture_output=True, text=True, check=True, timeout=10)
    return json.loads(process.stdout), metadata


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
def test_reconciled_editor_and_same_scoped_control_revalidate_before_intent_and_click(boundary, change):
    result, _ = run_dom("async_before_" + boundary + "_" + change)
    assert result["inserted"] and result["ready"]
    assert result["revalidated"] is (boundary == "click")
    assert result["submitted"] is False and result["clicks"] == 0


@pytest.mark.parametrize("boundary", [2, 3])
@pytest.mark.parametrize("field", ["generation", "handle", "cdp_endpoint"])
def test_binding_race_after_reconciled_readiness_still_prevents_attempt_click(registry, boundary, field):
    class ReconciledAdapter(Adapter):
        def ready(self, proof):
            super().ready(proof)
            result, _ = run_dom("async_inline_exact")
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
