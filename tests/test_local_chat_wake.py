"""Deterministic synthetic browser/state fixtures; no live ChatGPT access."""

import json
import shutil
import subprocess
from types import SimpleNamespace
from uuid import UUID

import pytest

from aios_renew import local_chat_wake as wake

EVENT = "terminal:RESULT:RUN-fixture-001:" + "a" * 40


def synthetic_url(number=1):
    # Generated fixture identity, never an operational conversation binding.
    return "https://chatgpt.com" + "/c/" + str(UUID(int=number))


def synthetic_project_url(number=1, project="g-fixture-project-1"):
    return "https://chatgpt.com/g/" + project + "/c/" + str(UUID(int=number))


@pytest.fixture(params=[synthetic_url(), synthetic_project_url()], ids=["standalone", "project"])
def binding(tmp_path, request):
    return wake.Binding(request.param, "http://" + "127.0.0.1:9222", tmp_path / "state.json")


@pytest.mark.parametrize("url", [
    synthetic_url(), synthetic_project_url(),
    synthetic_project_url(project="g-Fixture-ABC-123"),
    synthetic_project_url(project="g-"),
])
@pytest.mark.parametrize("suffix", ["", "/"])
def test_normalize_chat_preserves_full_identity_except_optional_trailing_slash(url, suffix):
    assert wake.normalize_chat(url + suffix) == url
    assert wake.normalize_chat(wake.normalize_chat(url + suffix)) == url


@pytest.mark.parametrize("path", [
    "/g//c/", "/g/fixture-project/c/", "/g/G-fixture-project/c/",
    "/g/g-fixture_project/c/", "/g/g-fixture.project/c/", "/g/g-fixture\u00e9/c/",
    "/g/g-fixture project/c/", "/g/g-fixture/project/c/",
    "/g/g-fixture-project/extra/c/", "/g/g-fixture-project/",
    "/g/g-fixture-project/c/c/", "/g/g-fixture-project//c/",
    "/g/%67-fixture-project/c/", "/g/g-fixture%2Dproject/c/",
    "/g/g-fixture%2Fproject/c/", "/g/g-fixture-project/%63/",
])
def test_malformed_project_paths_fail_closed(path):
    url = "https://chatgpt.com" + path + str(UUID(int=1))
    with pytest.raises(wake.WakeBlocked, match="INVALID_CHAT_BINDING"):
        wake.normalize_chat(url)


@pytest.mark.parametrize("base", [synthetic_url(), synthetic_project_url()])
@pytest.mark.parametrize("suffix", ["//", "/extra", "?fixture=value", "?", "#fixture", "#"])
def test_extra_components_queries_and_fragments_fail_closed(base, suffix):
    with pytest.raises(wake.WakeBlocked, match="INVALID_CHAT_BINDING"):
        wake.normalize_chat(base + suffix)


@pytest.mark.parametrize("origin", [
    "http://chatgpt.com", "HTTPS://chatgpt.com", "https://CHATGPT.COM",
    "https://www.chatgpt.com", "https://chatgpt.com.example.invalid",
    "https://chatgpt.com:443", "https://fixture@chatgpt.com", "https://example.invalid",
])
def test_project_wrong_origin_fails_closed(origin):
    path = "/g/g-fixture-project-1/c/" + str(UUID(int=1))
    with pytest.raises(wake.WakeBlocked, match="INVALID_CHAT_BINDING"):
        wake.normalize_chat(origin + path)


@pytest.mark.parametrize("conversation", [
    "", "fixture", str(UUID(int=1)).replace("-", ""),
    str(UUID(int=10)).upper(), "%30" + str(UUID(int=1))[1:],
])
def test_project_invalid_or_encoded_conversation_fails_closed(conversation):
    with pytest.raises(wake.WakeBlocked, match="INVALID_CHAT_BINDING"):
        wake.normalize_chat("https://chatgpt.com/g/g-fixture-project-1/c/" + conversation)


class FakeAdapter:
    def __init__(self, binding, failure=None):
        self.binding, self.failure = binding, failure
        self.submits = self.proofs = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def check(self, text):
        if self.failure == "check":
            raise wake.WakeBlocked("DRAFT_PRESENT")

    def submit(self, text):
        assert json.loads(self.binding.state_path.read_text())["events"][EVENT] == "ATTEMPTING"
        self.submits += 1
        if self.failure == "submit":
            raise RuntimeError("private browser exception " + self.binding.chat_url)

    def prove(self, text):
        self.proofs += 1
        if self.failure == "proof":
            raise wake.WakeBlocked("SUBMISSION_UNPROVEN")


def test_delivery_persists_attempt_before_submit_and_submitted_after_proof(binding):
    adapter = FakeAdapter(binding)
    assert wake.deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter) == {
        "event_id": EVENT, "status": "SUBMITTED", "reason": "EXACT_USER_TURN_PROVEN",
    }
    assert adapter.submits == adapter.proofs == 1
    assert json.loads(binding.state_path.read_text()) == {"version": 1, "events": {EVENT: "SUBMITTED"}}

    def forbidden(_):
        pytest.fail("duplicate must not connect to the browser")

    assert wake.deliver(EVENT, wake.REPOSITORY, binding, forbidden) == {
        "event_id": EVENT, "status": "NOOP", "reason": "ALREADY_SUBMITTED",
    }


@pytest.mark.parametrize("failure", ["submit", "proof"])
def test_ambiguous_attempt_never_resends_or_exposes_private_browser_data(binding, failure):
    adapter = FakeAdapter(binding, failure)
    receipt = wake.deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)
    assert receipt["status"] == "BLOCKED"
    assert set(receipt) == {"event_id", "status", "reason"}
    assert binding.chat_url not in json.dumps(receipt)
    assert binding.cdp_endpoint not in json.dumps(receipt)
    assert json.loads(binding.state_path.read_text())["events"][EVENT] == "ATTEMPTING"
    assert wake.deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)["reason"] == "ATTEMPT_REQUIRES_HUMAN"
    assert adapter.submits == 1


def test_pre_submit_failure_has_no_attempt_or_composer_modification(binding):
    adapter = FakeAdapter(binding, "check")
    assert wake.deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)["reason"] == "DRAFT_PRESENT"
    assert not binding.state_path.exists()
    assert adapter.submits == adapter.proofs == 0


@pytest.mark.parametrize("data", [
    {"version": 1, "events": {EVENT: "UNKNOWN"}},
    {"version": 1, "events": []},
    {"version": True, "events": {}},
    {"version": 1, "events": {}, "private": "untrusted"},
])
def test_ambiguous_state_fails_before_browser_connection(binding, data):
    binding.state_path.write_text(json.dumps(data))
    assert wake.deliver(EVENT, wake.REPOSITORY, binding, lambda _: pytest.fail("must not attach"))["reason"] == "STATE_AMBIGUOUS"


def test_corrupt_state_and_stale_lock_block(binding):
    binding.state_path.write_text("not-json")
    assert wake.deliver(EVENT, wake.REPOSITORY, binding)["reason"] == "LOCAL_METADATA_INVALID"
    binding.state_path.with_name("state.json.lock").touch()
    assert wake.deliver(EVENT, wake.REPOSITORY, binding)["reason"] == "STATE_LOCKED_OR_UNAVAILABLE"


def test_interrupted_pending_write_blocks_before_browser_connection(binding):
    binding.state_path.with_name("state.json.pending").write_text("interrupted")
    assert wake.deliver(EVENT, wake.REPOSITORY, binding, lambda _: pytest.fail("must not attach"))["reason"] == "STATE_WRITE_UNCERTAIN"


def test_state_write_failure_prevents_submit(binding, monkeypatch):
    adapter = FakeAdapter(binding)
    def unavailable(self, data):
        raise wake.WakeBlocked("STATE_WRITE_UNCERTAIN")
    monkeypatch.setattr(wake.State, "write", unavailable)
    assert wake.deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)["reason"] == "STATE_WRITE_UNCERTAIN"
    assert adapter.submits == 0


def test_final_state_write_failure_keeps_attempting_and_prevents_resend(binding, monkeypatch):
    adapter = FakeAdapter(binding)
    original = wake.State.write
    def unavailable_after_proof(self, data):
        if data["events"][EVENT] == "SUBMITTED":
            raise wake.WakeBlocked("STATE_WRITE_UNCERTAIN")
        original(self, data)
    monkeypatch.setattr(wake.State, "write", unavailable_after_proof)
    assert wake.deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)["reason"] == "STATE_WRITE_UNCERTAIN"
    assert json.loads(binding.state_path.read_text())["events"][EVENT] == "ATTEMPTING"
    assert wake.deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)["reason"] == "ATTEMPT_REQUIRES_HUMAN"
    assert adapter.submits == adapter.proofs == 1


def test_bounded_state_never_evicts_old_events(binding):
    events = {f"terminal:RESULT:RUN-fixture-{i}:" + "a" * 40: "SUBMITTED" for i in range(wake.MAX_EVENTS)}
    binding.state_path.write_text(json.dumps({"version": 1, "events": events}))
    assert wake.deliver(EVENT, wake.REPOSITORY, binding)["reason"] == "STATE_CAPACITY_REQUIRES_HUMAN"
    assert json.loads(binding.state_path.read_text())["events"] == events


def write_config(monkeypatch, binding, data=None):
    path = binding.state_path.parent / "binding.json"
    path.write_text(json.dumps(data if data is not None else {
        "chat_url": binding.chat_url, "cdp_endpoint": binding.cdp_endpoint,
        "state_path": str(binding.state_path),
    }))
    monkeypatch.setenv("AIOS_LOCAL_CHAT_WAKE_CONFIG", str(path))
    return path


def test_missing_and_valid_machine_binding(monkeypatch, binding):
    monkeypatch.delenv("AIOS_LOCAL_CHAT_WAKE_CONFIG", raising=False)
    with pytest.raises(wake.WakeBlocked, match="BINDING_MISSING"):
        wake.load_binding()
    write_config(monkeypatch, binding)
    assert wake.load_binding() == binding


@pytest.mark.parametrize("field,value", [
    ("chat_url", "https://example.invalid/"), ("chat_url", None),
    ("chat_url", " " + synthetic_url()), ("chat_url", synthetic_url() + "?private=value"),
    ("chat_url", synthetic_url() + "#fragment"),
    ("cdp_endpoint", "http://example.invalid:9222"),
    ("cdp_endpoint", "http://localhost:9222/?private=value"),
    ("cdp_endpoint", "http://user:private@localhost:9222"),
    ("cdp_endpoint", " http://localhost:9222"), ("cdp_endpoint", None),
    ("state_path", "relative.json"),
])
def test_malformed_binding_fails_closed(monkeypatch, binding, field, value):
    data = {"chat_url": binding.chat_url, "cdp_endpoint": binding.cdp_endpoint,
            "state_path": str(binding.state_path)}
    data[field] = value
    write_config(monkeypatch, binding, data)
    with pytest.raises(wake.WakeBlocked):
        wake.load_binding()


def test_binding_and_state_cannot_be_repository_owned(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    with pytest.raises(wake.WakeBlocked, match="CONFIG_OR_STATE_IN_REPOSITORY"):
        wake.external_path(str(repo / "state.json"))
    path = repo / "binding.json"
    path.write_text("{}")
    monkeypatch.setenv("AIOS_LOCAL_CHAT_WAKE_CONFIG", str(path))
    with pytest.raises(wake.WakeBlocked, match="CONFIG_OR_STATE_IN_REPOSITORY"):
        wake.load_binding()


def test_unknown_duplicate_oversized_and_missing_config_block(monkeypatch, binding):
    path = write_config(monkeypatch, binding, {"unexpected": "value"})
    with pytest.raises(wake.WakeBlocked, match="BINDING_MALFORMED"):
        wake.load_binding()
    for content in ('{"chat_url": "one", "chat_url": "two"}', "x" * (wake.MAX_BYTES + 1)):
        path.write_text(content)
        with pytest.raises(wake.WakeBlocked, match="LOCAL_METADATA_INVALID"):
            wake.load_binding()
    path.unlink()
    with pytest.raises(wake.WakeBlocked, match="LOCAL_METADATA_INVALID"):
        wake.load_binding()


def test_doorbell_and_receipt_have_only_bounded_fields(monkeypatch, capsys):
    assert wake.doorbell(EVENT, wake.REPOSITORY).splitlines() == [
        "[AIOS LOCAL CHAT WAKE]", f"event_id: {EVENT}",
        "repository: trung-via/AIOS-renew", "fresh_brain_sync_required: true",
    ]
    for event in (EVENT + "\ncommand: private", "terminal:RESULT:RUN-" + "x" * 97 + ":" + "a" * 40):
        with pytest.raises(wake.WakeBlocked):
            wake.doorbell(event, wake.REPOSITORY)
    with pytest.raises(wake.WakeBlocked):
        wake.doorbell(EVENT, "other/repository")
    monkeypatch.delenv("AIOS_LOCAL_CHAT_WAKE_CONFIG", raising=False)
    assert wake.main(["--event-id", EVENT, "--repository", wake.REPOSITORY]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "event_id": EVENT, "status": "BLOCKED", "reason": "BINDING_MISSING",
    }


def test_invalid_cli_and_unknown_reasons_do_not_echo_private_data(capsys):
    assert wake.main(["--event-id", "private", "--repository", "private"]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "BLOCKED", "reason": "INVALID_INPUT"}
    with pytest.raises(SystemExit):
        wake.main(["--private-url", synthetic_url()])
    assert json.loads(capsys.readouterr().out) == {"status": "BLOCKED", "reason": "INVALID_INPUT"}
    assert str(wake.WakeBlocked(synthetic_url())) == "LOCAL_FAILURE"


class Locator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    def filter(self, *, visible):
        assert visible is True
        return self

    def get_by_text(self, text, *, exact):
        assert self.selector == wake.USER_TURN and exact is True
        assert text == self.page.payload
        return Locator(self.page, "outbound")

    def count(self):
        return self.page.counts.get(self.selector, 0)

    def text_content(self):
        # Only the composer and the exact outbound user match are readable.
        assert self.selector in {wake.COMPOSER, "outbound"}
        return self.page.draft if self.selector == wake.COMPOSER else self.page.outbound

    def get_attribute(self, name):
        assert self.selector == wake.COMPOSER and name == "aria-disabled"
        return "true" if self.page.disabled else None

    def is_enabled(self):
        assert self.selector == wake.SEND
        return self.page.send_enabled

    def wait_for(self, *, state, timeout):
        assert state == "visible" and timeout <= 5000
        if self.count() != 1:
            raise RuntimeError("not visible")


class Page:
    def __init__(self, url):
        self.url, self.draft = url, ""
        self.payload = wake.doorbell(EVENT, wake.REPOSITORY)
        self.outbound = self.payload
        self.send_enabled = self.disabled = False
        self.counts = {"main": 1, wake.COMPOSER: 1, wake.ACCOUNT: 1}
        self.evaluations = []
        self.race = None

    def locator(self, selector):
        assert selector in {"main", wake.COMPOSER, wake.ACCOUNT, wake.STOP, wake.NONREGULAR, wake.LOGIN, wake.SEND, wake.USER_TURN}
        return Locator(self, selector)

    def evaluate(self, script, args):
        assert args["text"] == self.payload
        self.evaluations.append(script)
        if script == wake.INSERT:
            if self.race == "insert":
                self.draft = "Human draft"
                return False
            self.draft = args["text"]
            self.counts[wake.SEND] = 1
            self.send_enabled = True
        elif script == wake.CLICK:
            if self.race == "send":
                self.draft += " Human edit"
                return False
            assert self.draft == args["text"]
            self.draft = ""
            self.counts["outbound"] = 1
            self.send_enabled = False
        else:
            pytest.fail("unexpected browser script")
        return True


def adapter_for(binding, *pages):
    adapter = wake.BrowserAdapter(binding)
    adapter.browser = SimpleNamespace(contexts=[SimpleNamespace(pages=list(pages))])
    adapter.page = pages[0] if pages else None
    return adapter


@pytest.mark.parametrize("number", [0, 2])
def test_zero_or_multiple_target_pages_block(binding, number):
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[Page(binding.chat_url) for _ in range(number)])])
    with pytest.raises(wake.WakeBlocked, match="TARGET_PAGE_NOT_UNIQUE"):
        wake.BrowserAdapter.select_page(browser, binding.chat_url)


def test_duplicate_target_across_contexts_blocks(binding):
    browser = SimpleNamespace(contexts=[
        SimpleNamespace(pages=[Page(binding.chat_url + suffix)]) for suffix in ("", "/")
    ])
    with pytest.raises(wake.WakeBlocked, match="TARGET_PAGE_NOT_UNIQUE"):
        wake.BrowserAdapter.select_page(browser, binding.chat_url)


def test_unrelated_pages_are_ignored_without_navigation(binding):
    target = Page(binding.chat_url + "/")
    unrelated = [Page(synthetic_url(2)), Page("https://example.invalid/")]
    adapter = adapter_for(binding, target, *unrelated)
    assert adapter.select_page(adapter.browser, binding.chat_url) is target
    adapter.check(target.payload)
    assert not target.evaluations and all(not p.evaluations for p in unrelated)


@pytest.mark.parametrize("unrelated_url", [
    synthetic_project_url(project="g-fixture-project-2"),
    synthetic_project_url(project="g-Fixture-project-1"),
    synthetic_project_url(number=2), synthetic_url(),
    synthetic_project_url() + "/extra", synthetic_project_url() + "?fixture=value",
    synthetic_project_url() + "#fixture",
    synthetic_project_url(project="%67-fixture-project-1"),
])
def test_project_selection_requires_complete_identity_without_inference(unrelated_url):
    url = synthetic_project_url()
    unrelated = Page(unrelated_url)
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[unrelated])])
    with pytest.raises(wake.WakeBlocked, match="TARGET_PAGE_NOT_UNIQUE"):
        wake.BrowserAdapter.select_page(browser, url)
    target = Page(url + "/")
    browser.contexts.append(SimpleNamespace(pages=[target]))
    assert wake.BrowserAdapter.select_page(browser, url) is target
    assert not unrelated.evaluations and not target.evaluations


def test_project_change_with_same_conversation_blocks_before_insert_or_proof(tmp_path):
    url = synthetic_project_url()
    binding = wake.Binding(url, "http://127.0.0.1:9222", tmp_path / "state.json")
    page = Page(url)
    adapter = adapter_for(binding, page)
    page.url = synthetic_project_url(project="g-fixture-project-2")
    with pytest.raises(wake.WakeBlocked, match="TARGET_PAGE_NOT_UNIQUE"):
        adapter.submit(page.payload)
    assert page.evaluations == []
    page.counts["outbound"] = 1
    with pytest.raises(wake.WakeBlocked, match="TARGET_PAGE_NOT_UNIQUE"):
        adapter.prove(page.payload)


@pytest.mark.parametrize("condition,reason", [
    ("logged_out", "SURFACE_UNPROVEN"), ("login", "SURFACE_UNPROVEN"),
    ("wrong_surface", "SURFACE_UNPROVEN"), ("missing_composer", "SURFACE_UNPROVEN"),
    ("disabled", "SURFACE_UNPROVEN"), ("draft", "DRAFT_PRESENT"),
    ("generating", "GENERATION_ACTIVE"), ("prior_outbound", "OUTBOUND_ALREADY_PRESENT"),
])
def test_browser_preflight_blocks_before_composer_modification(binding, condition, reason):
    page = Page(binding.chat_url)
    if condition == "logged_out":
        page.counts[wake.ACCOUNT] = 0
    elif condition == "login":
        page.counts[wake.LOGIN] = 1
    elif condition == "wrong_surface":
        page.counts[wake.NONREGULAR] = 1
    elif condition == "missing_composer":
        page.counts[wake.COMPOSER] = 0
    elif condition == "disabled":
        page.disabled = True
    elif condition == "draft":
        page.draft = "Human draft"
    elif condition == "generating":
        page.counts[wake.STOP] = 1
    else:
        page.counts["outbound"] = 1
    with pytest.raises(wake.WakeBlocked, match=reason):
        adapter_for(binding, page).submit(page.payload)
    assert page.evaluations == []


def test_exact_submission_proof_reads_only_outbound_and_composer(binding):
    page = Page(binding.chat_url)
    adapter = adapter_for(binding, page)
    adapter.submit(page.payload)
    # Generation after send is permitted without extracting its output.
    page.counts[wake.STOP] = 1
    adapter.prove(page.payload)
    assert page.evaluations == [wake.INSERT, wake.CLICK]


@pytest.mark.parametrize("condition", ["missing_turn", "duplicate_turn", "wrong_text", "draft", "send_enabled", "wrong_chat", "second_target"])
def test_submission_proof_fails_closed(binding, condition):
    page = Page(binding.chat_url)
    adapter = adapter_for(binding, page)
    adapter.submit(page.payload)
    if condition == "missing_turn":
        page.counts["outbound"] = 0
    elif condition == "duplicate_turn":
        page.counts["outbound"] = 2
    elif condition == "wrong_text":
        # Locator text matching normalizes whitespace; proof requires exact text.
        page.outbound = page.payload.replace("\n", " ")
    elif condition == "draft":
        page.draft = "Human draft"
    elif condition == "send_enabled":
        page.send_enabled = True
    elif condition == "wrong_chat":
        page.url = synthetic_url(2)
    else:
        adapter.browser.contexts[0].pages.append(Page(binding.chat_url))
    with pytest.raises((wake.WakeBlocked, RuntimeError)):
        adapter.prove(page.payload)


@pytest.mark.parametrize("stage", ["insert", "send"])
def test_human_draft_race_blocks_without_clearing_human_text(binding, stage):
    page = Page(binding.chat_url)
    page.race = stage
    with pytest.raises(wake.WakeBlocked, match="INSERT_BLOCKED|SEND_BLOCKED"):
        adapter_for(binding, page).submit(page.payload)
    assert "Human" in page.draft
    assert page.counts.get("outbound", 0) == 0


def test_client_attaches_and_disconnects_without_browser_launch(monkeypatch, binding):
    page = Page(binding.chat_url)
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[page])])
    calls = []
    def connect(endpoint, *, timeout):
        calls.append((endpoint, timeout))
        return browser
    driver = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect), stop=lambda: calls.append("disconnect"))
    module = SimpleNamespace(sync_playwright=lambda: SimpleNamespace(start=lambda: driver))
    import sys
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    page.set_default_timeout = lambda timeout: calls.append(timeout)
    with wake.BrowserAdapter(binding) as adapter:
        assert adapter.page is page
    assert calls == [(binding.cdp_endpoint, 10000), 3000, "disconnect"]


def test_scripts_guard_identity_surface_and_human_edit_in_same_turn():
    for script in (wake.INSERT, wake.CLICK):
        for guard in ("location.href.replace", "visible(account).length !== 1", "visible(stop).length",
                      "visible(nonregular).length", "visible(login).length"):
            assert guard in script
    assert "box.textContent === ''" in wake.INSERT
    assert wake.INSERT.index("box.focus()") < wake.INSERT.index("if (!empty() ||") < wake.INSERT.index("document.execCommand")
    assert "boxes[0].innerText.replace" in wake.CLICK
    assert "buttons[0].click()" in wake.CLICK


@pytest.mark.parametrize("scenario", [
    "valid", "draft", "focus_draft", "focus_navigation", "busy", "wrong_surface",
    "logged_out", "send_edit", "send_disabled", "wrong_project", "focus_project", "send_project",
])
def test_real_browser_scripts_on_synthetic_dom(binding, scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the isolated JavaScript DOM harness")
    payload = wake.doorbell(EVENT, wake.REPOSITORY)
    arguments = wake.BrowserAdapter(binding).arguments(payload)
    # Execute the actual browser programs; no browser, network or history exists.
    harness = r"""
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const args = input.args;
global.location = {href: args.url};
global.getComputedStyle = () => ({visibility: 'visible'});
let clicks = 0, inserts = 0;
const element = () => ({getClientRects: () => [1], getAttribute: () => null});
const box = Object.assign(element(), {textContent: '', innerText: '', focus() {
  if (input.scenario === 'focus_draft') box.textContent = box.innerText = 'Human draft';
  if (input.scenario === 'focus_navigation') location.href = 'https://example.invalid/';
  if (input.scenario === 'focus_project') location.href = input.other_url;
}});
const button = Object.assign(element(), {disabled: false, click() {clicks++;}});
const elements = {[args.composer]: [box], [args.account]: [element()],
  [args.send]: [button], [args.stop]: [], [args.nonregular]: [], [args.login]: [], main: [element()]};
global.document = {
  querySelectorAll: selector => elements[selector] || [],
  execCommand: (command, unused, text) => {
    if (command !== 'insertText') throw Error('unexpected modification');
    inserts++; box.textContent = box.innerText = text; return true;
  },
};
if (input.scenario === 'draft') box.textContent = box.innerText = 'Human draft';
if (input.scenario === 'busy') elements[args.stop] = [element()];
if (input.scenario === 'wrong_surface') elements[args.nonregular] = [element()];
if (input.scenario === 'logged_out') elements[args.account] = [];
if (input.scenario === 'wrong_project') location.href = input.other_url;
const inserted = eval('(' + input.insert + ')')(args);
if (input.scenario === 'send_edit') box.textContent = box.innerText = args.text + ' Human edit';
if (input.scenario === 'send_disabled') button.disabled = true;
if (input.scenario === 'send_project') location.href = input.other_url;
const sent = inserted ? eval('(' + input.click + ')')(args) : false;
process.stdout.write(JSON.stringify({inserted, sent, inserts, clicks, draft: box.textContent}));
"""
    process = subprocess.run(
        [node, "-e", harness], input=json.dumps({"args": arguments, "scenario": scenario,
                                               "other_url": synthetic_project_url(project="g-fixture-project-2"),
                                               "insert": wake.INSERT, "click": wake.CLICK}),
        capture_output=True, text=True, check=True, timeout=10,
    )
    result = json.loads(process.stdout)
    if scenario == "valid":
        assert result == {"inserted": True, "sent": True, "inserts": 1, "clicks": 1, "draft": payload}
    else:
        assert result["sent"] is False and result["clicks"] == 0
        if scenario not in {"send_edit", "send_disabled", "send_project"}:
            assert result["inserted"] is False and result["inserts"] == 0
        if scenario in {"draft", "focus_draft", "send_edit"}:
            assert "Human" in result["draft"]
