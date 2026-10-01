"""Synthetic pages and temporary machine-local fixtures; no live chat access."""

import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from aios_renew import local_chat_wake as wake

EVENT = "terminal:RESULT:RUN-fixture-001:" + "a" * 40


def synthetic_url(number=1):
    # Generated fixture identity, never a real operational conversation binding.
    return "https://chatgpt.com" + "/c/" + str(UUID(int=number))


@pytest.fixture
def binding(tmp_path):
    return wake.Binding(synthetic_url(), "http://" + "127.0.0.1:9222", tmp_path / "state.json")


class FakeAdapter:
    def __init__(self, binding, failure=None):
        self.binding = binding
        self.failure = failure
        self.submits = 0
        self.proofs = 0

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


def test_valid_delivery_persists_attempt_before_submit_and_exact_proof(binding):
    adapter = FakeAdapter(binding)
    receipt = wake.deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)
    assert receipt == {"event_id": EVENT, "status": "SUBMITTED", "reason": "EXACT_USER_TURN_PROVEN"}
    assert adapter.submits == adapter.proofs == 1
    assert json.loads(binding.state_path.read_text()) == {"version": 1, "events": {EVENT: "SUBMITTED"}}
    def forbidden(_):
        pytest.fail("duplicate delivery must not even connect to the browser")
    assert wake.deliver(EVENT, wake.REPOSITORY, binding, forbidden)["status"] == "NOOP"


@pytest.mark.parametrize("failure", ["submit", "proof"])
def test_ambiguous_post_attempt_failure_never_resends(binding, failure):
    adapter = FakeAdapter(binding, failure)
    first = wake.deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)
    assert first["status"] == "BLOCKED"
    assert binding.chat_url not in json.dumps(first)
    assert json.loads(binding.state_path.read_text())["events"][EVENT] == "ATTEMPTING"
    assert wake.deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)["reason"] == "ATTEMPT_REQUIRES_HUMAN"
    assert adapter.submits == 1


def test_pre_submit_failure_has_no_attempt_or_composer_mutation(binding):
    adapter = FakeAdapter(binding, "check")
    assert wake.deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)["reason"] == "DRAFT_PRESENT"
    assert not binding.state_path.exists()
    assert adapter.submits == 0


@pytest.mark.parametrize("data", [
    {"version": 1, "events": {EVENT: "UNKNOWN"}},
    {"version": 1, "events": []},
    {"version": True, "events": {}},
    {"version": 1, "events": {}, "private": "untrusted"},
])
def test_ambiguous_state_fails_closed(binding, data):
    binding.state_path.write_text(json.dumps(data))
    assert wake.deliver(EVENT, wake.REPOSITORY, binding, lambda _: pytest.fail("must not attach"))["reason"] == "STATE_AMBIGUOUS"


def test_corrupt_state_and_stale_lock_block(binding):
    binding.state_path.write_text("not-json")
    assert wake.deliver(EVENT, wake.REPOSITORY, binding)["reason"] == "LOCAL_METADATA_INVALID"
    binding.state_path.with_name("state.json.lock").touch()
    assert wake.deliver(EVENT, wake.REPOSITORY, binding)["reason"] == "STATE_LOCKED_OR_UNAVAILABLE"


def test_bounded_state_never_evicts_or_resends_old_events(binding):
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
    ("chat_url", "https://example.invalid/"),
    ("chat_url", None),
    ("cdp_endpoint", "http://example.invalid:9222"),
    ("cdp_endpoint", "http://localhost:9222/?private=secret"),
    ("cdp_endpoint", "http://user:secret@localhost:9222"),
    ("state_path", "relative.json"),
])
def test_malformed_binding_never_attaches(monkeypatch, binding, field, value):
    data = {"chat_url": binding.chat_url, "cdp_endpoint": binding.cdp_endpoint, "state_path": str(binding.state_path)}
    data[field] = value
    write_config(monkeypatch, binding, data)
    with pytest.raises(wake.WakeBlocked):
        wake.load_binding()


def test_binding_and_state_cannot_be_repository_owned(monkeypatch, binding, tmp_path):
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


def test_unknown_and_duplicate_config_keys_block(monkeypatch, binding):
    write_config(monkeypatch, binding, {"unexpected": "value"})
    with pytest.raises(wake.WakeBlocked, match="BINDING_MALFORMED"):
        wake.load_binding()
    path = write_config(monkeypatch, binding)
    path.write_text('{"chat_url": "one", "chat_url": "two"}')
    with pytest.raises(wake.WakeBlocked, match="LOCAL_METADATA_INVALID"):
        wake.load_binding()


def test_doorbell_and_receipt_have_only_bounded_fields(monkeypatch, binding, capsys):
    assert wake.doorbell(EVENT, wake.REPOSITORY).splitlines() == [
        "[AIOS LOCAL CHAT WAKE]", f"event_id: {EVENT}",
        "repository: trung-via/AIOS-renew", "fresh_brain_sync_required: true",
    ]
    with pytest.raises(wake.WakeBlocked):
        wake.doorbell(EVENT + "\ncommand: private", wake.REPOSITORY)
    with pytest.raises(wake.WakeBlocked):
        wake.doorbell(EVENT, "other/repository")
    monkeypatch.delenv("AIOS_LOCAL_CHAT_WAKE_CONFIG", raising=False)
    assert wake.main(["--event-id", EVENT, "--repository", wake.REPOSITORY]) == 1
    receipt = json.loads(capsys.readouterr().out)
    assert receipt == {"event_id": EVENT, "status": "BLOCKED", "reason": "BINDING_MISSING"}


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
        # Any attempt to extract other conversation content fails the fixture.
        assert self.selector == wake.COMPOSER
        return self.page.draft

    def is_enabled(self):
        assert self.selector == wake.SEND
        return self.page.send_enabled

    def wait_for(self, *, state, timeout):
        assert state == "visible" and timeout <= 5000
        if self.count() != 1:
            raise RuntimeError("not visible")


class Page:
    def __init__(self, url):
        self.url = url
        self.draft = ""
        self.payload = wake.doorbell(EVENT, wake.REPOSITORY)
        self.send_enabled = False
        self.counts = {"main": 1, wake.COMPOSER: 1, wake.ACCOUNT: 1}
        self.evaluations = []
        self.race = False

    def locator(self, selector):
        assert selector in {"main", wake.COMPOSER, wake.ACCOUNT, wake.STOP, wake.NONREGULAR, wake.LOGIN, wake.SEND, wake.USER_TURN}
        return Locator(self, selector)

    def evaluate(self, script, args):
        assert args["text"] == self.payload
        self.evaluations.append(script)
        if script == wake.INSERT:
            if self.race:
                self.draft = "Human draft"
                return False
            self.draft = args["text"]
            self.counts[wake.SEND] = 1
            self.send_enabled = True
        elif script == wake.CLICK:
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


def test_unrelated_pages_are_ignored_and_never_navigated(binding):
    target = Page(binding.chat_url + "/")
    adapter = adapter_for(binding, target, Page(synthetic_url(2)), Page("https://example.invalid/"))
    assert adapter.select_page(adapter.browser, binding.chat_url) is target
    adapter.check(target.payload)
    assert not target.evaluations


@pytest.mark.parametrize("condition,reason", [
    ("logged_out", "SURFACE_UNPROVEN"), ("login", "SURFACE_UNPROVEN"),
    ("wrong_surface", "SURFACE_UNPROVEN"), ("missing_composer", "SURFACE_UNPROVEN"),
    ("draft", "DRAFT_PRESENT"), ("generating", "GENERATION_ACTIVE"),
    ("prior_outbound", "OUTBOUND_ALREADY_PRESENT"),
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
    elif condition == "draft":
        page.draft = "Human draft"
    elif condition == "generating":
        page.counts[wake.STOP] = 1
    else:
        page.counts["outbound"] = 1
    with pytest.raises(wake.WakeBlocked, match=reason):
        adapter_for(binding, page).submit(page.payload)
    assert page.evaluations == []


def test_exact_user_submission_proof_reads_only_outbound_and_composer(binding):
    page = Page(binding.chat_url)
    adapter = adapter_for(binding, page)
    adapter.check(page.payload)
    adapter.submit(page.payload)
    # Generation after a send is permitted, without reading its output.
    page.counts[wake.STOP] = 1
    adapter.prove(page.payload)
    assert page.evaluations == [wake.INSERT, wake.CLICK]


@pytest.mark.parametrize("condition", ["missing_turn", "duplicate_turn", "draft", "send_enabled", "wrong_chat"])
def test_submission_proof_fails_closed(binding, condition):
    page = Page(binding.chat_url)
    adapter = adapter_for(binding, page)
    adapter.submit(page.payload)
    if condition == "missing_turn":
        page.counts["outbound"] = 0
    elif condition == "duplicate_turn":
        page.counts["outbound"] = 2
    elif condition == "draft":
        page.draft = "Human draft"
    elif condition == "send_enabled":
        page.send_enabled = True
    else:
        page.url = synthetic_url(2)
    with pytest.raises((wake.WakeBlocked, RuntimeError)):
        adapter.prove(page.payload)


def test_human_draft_race_blocks_send_without_clearing_draft(binding):
    page = Page(binding.chat_url)
    page.race = True
    adapter = adapter_for(binding, page)
    with pytest.raises(wake.WakeBlocked, match="INSERT_BLOCKED"):
        adapter.submit(page.payload)
    assert page.draft == "Human draft"
    assert page.evaluations == [wake.INSERT]


def test_client_only_attaches_and_disconnects(monkeypatch, binding):
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


def test_scripts_guard_draft_and_identity_in_same_turn():
    for script in (wake.INSERT, wake.CLICK):
        assert "location.href.replace" in script
        assert "visible(account).length !== 1" in script
        assert "visible(stop).length" in script
        assert "visible(nonregular).length" in script
        assert "visible(login).length" in script
    assert "box.textContent !== ''" in wake.INSERT
    assert "boxes[0].innerText.replace" in wake.CLICK
    assert "buttons[0].click()" in wake.CLICK
