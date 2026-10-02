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


def test_existing_attempting_entry_is_preserved_without_browser_or_recovery(binding):
    data = {"version": 1, "events": {EVENT: "ATTEMPTING",
            "terminal:RESULT:RUN-fixture-other:" + "b" * 40: "SUBMITTED"}}
    binding.state_path.write_text(json.dumps(data))
    assert wake.deliver(EVENT, wake.REPOSITORY, binding,
                        lambda _: pytest.fail("ATTEMPTING must not attach or resend")) == {
        "event_id": EVENT, "status": "BLOCKED", "reason": "ATTEMPT_REQUIRES_HUMAN",
    }
    assert json.loads(binding.state_path.read_text()) == data


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

    def count(self):
        return self.page.counts.get(self.selector, 0)

    def text_content(self):
        # Outbound text stays inside the bounded browser resolver.
        assert self.selector == wake.COMPOSER
        return self.page.draft

    def get_attribute(self, name):
        assert self.selector == wake.COMPOSER and name == "aria-disabled"
        return "true" if self.page.disabled else None


class Page:
    def __init__(self, url):
        self.url, self.draft = url, ""
        self.payload = wake.doorbell(EVENT, wake.REPOSITORY)
        self.outbound = self.payload
        self.send_enabled = self.disabled = False
        self.counts = {"main": 1, wake.COMPOSER: 1, wake.ACCOUNT: 1}
        self.evaluations = []
        self.resolutions = []
        self.race = None

    def locator(self, selector):
        assert selector in {"main", wake.COMPOSER, wake.ACCOUNT, wake.STOP, wake.NONREGULAR, wake.LOGIN}
        return Locator(self, selector)

    def wait_for_function(self, script, *, arg, timeout):
        if script == wake.WAIT_USER_TURN:
            assert arg["text"] == self.payload and timeout == 5000
            if self.evaluate(wake.RESOLVE_USER_TURN, arg) != "EXACT":
                raise RuntimeError("bounded outbound proof absent")
            return
        assert script == wake.ACCEPT_INSERT and arg["text"] == self.payload and timeout == 3000
        if self.counts.get(wake.SEND, 0) != 1 or not self.send_enabled or self.draft != self.payload:
            raise RuntimeError("scoped readiness absent")

    def evaluate(self, script, args):
        assert args["text"] == self.payload
        assert args["composer"] == wake.COMPOSER and args["account"] == wake.ACCOUNT
        if script == wake.RESOLVE_USER_TURN:
            self.resolutions.append(script)
            count = self.counts.get("outbound", 0)
            if not count or self.outbound.replace("\r\n", "\n") != args["text"]:
                return "ABSENT"
            return "EXACT" if count == 1 else "AMBIGUOUS"
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
        elif script == wake.ACCEPT_INSERT:
            return self.draft == args["text"] and self.counts.get(wake.SEND, 0) == 1 and self.send_enabled
        elif script == wake.PROVE_SEND:
            count = self.counts.get(wake.SEND, 0)
            return count == 0 or (count == 1 and not self.send_enabled)
        else:
            pytest.fail("unexpected browser script")
        return True


def adapter_for(binding, *pages):
    adapter = wake.BrowserAdapter(binding)
    adapter.browser = SimpleNamespace(contexts=[SimpleNamespace(pages=list(pages))])
    adapter.page = pages[0] if pages else None
    return adapter


# A selector double for the three bounded account and two composer branches.
# It models CSS union identity and visibility without any live browser content.
LEGACY_COMPOSER = '#prompt-textarea[contenteditable="true"]'
STRUCTURAL_COMPOSER = 'main form [contenteditable="true"][role="textbox"][aria-multiline="true"]'
PROFILE_ARIA = 'button[aria-label*="profile" i]'


def surface_element(tag="div", ancestors=(), visible=True, **attrs):
    return SimpleNamespace(tag=tag, ancestors=ancestors, visible=visible, attrs=attrs)


def surface_matches(element, branch):
    attrs = element.attrs
    if branch in ('[data-testid="profile-button"]', '[data-testid="accounts-profile-button"]'):
        return attrs.get("data-testid") == branch.split('"')[1]
    if branch == PROFILE_ARIA:
        return element.tag == "button" and "profile" in attrs.get("aria-label", "").lower()
    if branch == LEGACY_COMPOSER:
        return attrs.get("id") == "prompt-textarea" and attrs.get("contenteditable") == "true"
    if branch == STRUCTURAL_COMPOSER:
        ancestors = element.ancestors
        scoped = "main" in ancestors and "form" in ancestors[ancestors.index("main") + 1:]
        return scoped and all(attrs.get(key) == value for key, value in (
            ("contenteditable", "true"), ("role", "textbox"), ("aria-multiline", "true")))
    pytest.fail("unexpected selector branch")


class SurfaceLocator(Locator):
    def count(self):
        if self.selector in {wake.ACCOUNT, wake.COMPOSER}:
            # Select each element once, rather than summing branch counts.
            return sum(element.visible and any(surface_matches(element, branch.strip())
                       for branch in self.selector.split(",")) for element in self.page.elements)
        return super().count()


class SurfacePage(Page):
    def __init__(self, url, elements):
        super().__init__(url)
        self.elements = elements

    def locator(self, selector):
        super().locator(selector)  # Preserve the fixture's read boundary.
        return SurfaceLocator(self, selector)


def account_element(shape="fallback", **overrides):
    attrs = {"aria-label": "Open FiXtUrE PROFILE menu"} if shape != "legacy" else {}
    if shape in {"legacy", "both"}:
        attrs["data-testid"] = "profile-button"
    if shape == "legacy_accounts":
        attrs = {"data-testid": "accounts-profile-button"}
    attrs.update(overrides)
    return surface_element(tag="button", **attrs)


def composer_element(shape="fallback", ancestors=("main", "form"), **overrides):
    attrs = {"contenteditable": "true"}
    if shape != "legacy":
        attrs.update({"role": "textbox", "aria-multiline": "true"})
    if shape in {"legacy", "both"}:
        attrs["id"] = "prompt-textarea"
    attrs.update(overrides)
    return surface_element(ancestors=ancestors, **attrs)


def test_bounded_selector_contracts_and_unchanged_stop():
    assert wake.ACCOUNT == ('[data-testid="profile-button"], '
                            '[data-testid="accounts-profile-button"], ' + PROFILE_ARIA)
    assert wake.COMPOSER == LEGACY_COMPOSER + ", " + STRUCTURAL_COMPOSER
    assert wake.SEND == '[data-testid="send-button"], button[type="submit"][aria-label="Send"]'
    assert wake.STOP == '[data-testid="stop-button"]'


@pytest.mark.parametrize("account_shape", ["legacy", "legacy_accounts", "fallback", "both"])
@pytest.mark.parametrize("composer_shape", ["legacy", "fallback", "both"])
def test_bounded_surface_compatibility_and_union_identity(binding, account_shape, composer_shape):
    page = SurfacePage(binding.chat_url, [account_element(account_shape), composer_element(composer_shape)])
    adapter = adapter_for(binding, page)
    assert adapter.visible(wake.ACCOUNT).count() == adapter.visible(wake.COMPOSER).count() == 1
    adapter.check(page.payload)
    adapter.submit(page.payload)
    adapter.prove(page.payload)
    assert page.evaluations == [wake.INSERT, wake.ACCEPT_INSERT, wake.CLICK, wake.PROVE_SEND]


@pytest.mark.parametrize("kind", ["account", "composer"])
@pytest.mark.parametrize("number", [0, 2])
def test_bounded_surface_zero_or_ambiguous_matches_block_check_and_prove(binding, kind, number):
    accounts = [account_element() for _ in range(number if kind == "account" else 1)]
    composers = [composer_element() for _ in range(number if kind == "composer" else 1)]
    # Distinct legacy/fallback elements also constitute ambiguity.
    if number == 2:
        (accounts if kind == "account" else composers)[0] = (
            account_element("legacy") if kind == "account" else composer_element("legacy"))
    page = SurfacePage(binding.chat_url, accounts + composers)
    adapter = adapter_for(binding, page)
    with pytest.raises(wake.WakeBlocked, match="SURFACE_UNPROVEN"):
        adapter.submit(page.payload)
    assert page.evaluations == []
    page.counts["outbound"] = 1
    with pytest.raises(wake.WakeBlocked, match="SUBMISSION_UNPROVEN"):
        adapter.prove(page.payload)


@pytest.mark.parametrize("ancestors", [(), ("form",), ("main",), ("form", "main")])
def test_structural_composer_lookalikes_outside_main_form_fail_closed(binding, ancestors):
    page = SurfacePage(binding.chat_url, [account_element(), composer_element(ancestors=ancestors)])
    with pytest.raises(wake.WakeBlocked, match="SURFACE_UNPROVEN"):
        adapter_for(binding, page).submit(page.payload)
    assert page.evaluations == []


@pytest.mark.parametrize("attrs", [
    {"contenteditable": "false"}, {"role": "searchbox"}, {"aria-multiline": "false"},
    {"contenteditable": None}, {"role": None}, {"aria-multiline": None},
])
def test_structural_composer_requires_every_attribute(binding, attrs):
    page = SurfacePage(binding.chat_url, [account_element(), composer_element(**attrs)])
    with pytest.raises(wake.WakeBlocked, match="SURFACE_UNPROVEN"):
        adapter_for(binding, page).check(page.payload)


@pytest.mark.parametrize("tag,label", [("div", "fixture profile"), ("button", "fixture settings")])
def test_profile_fallback_requires_button_and_profile_aria(binding, tag, label):
    page = SurfacePage(binding.chat_url, [
        surface_element(tag=tag, **{"aria-label": label}), composer_element()])
    with pytest.raises(wake.WakeBlocked, match="SURFACE_UNPROVEN"):
        adapter_for(binding, page).check(page.payload)


def test_hidden_bounded_matches_do_not_create_ambiguity(binding):
    hidden_account, hidden_composer = account_element("legacy"), composer_element("legacy")
    hidden_account.visible = hidden_composer.visible = False
    page = SurfacePage(binding.chat_url, [account_element(), composer_element(), hidden_account, hidden_composer])
    adapter_for(binding, page).check(page.payload)


@pytest.mark.parametrize("condition,reason", [
    ("draft", "DRAFT_PRESENT"), ("generation", "GENERATION_ACTIVE"),
    ("login", "SURFACE_UNPROVEN"), ("nonregular", "SURFACE_UNPROVEN"),
    ("outbound", "OUTBOUND_ALREADY_PRESENT"),
])
def test_fallback_surface_retains_preflight_safety(binding, condition, reason):
    page = SurfacePage(binding.chat_url, [account_element(), composer_element()])
    if condition == "draft":
        page.draft = "Synthetic Human draft"
    else:
        selector = {"generation": wake.STOP, "login": wake.LOGIN,
                    "nonregular": wake.NONREGULAR, "outbound": "outbound"}[condition]
        page.counts[selector] = 1
    with pytest.raises(wake.WakeBlocked, match=reason):
        adapter_for(binding, page).submit(page.payload)
    assert page.evaluations == []


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
    assert page.evaluations == [wake.INSERT, wake.ACCEPT_INSERT, wake.CLICK, wake.PROVE_SEND]
    assert len(page.resolutions) >= 4
    assert set(page.resolutions) == {wake.RESOLVE_USER_TURN}


@pytest.mark.parametrize("scenario,expected", [
    ("legacy", "EXACT"), ("legacy_child", "EXACT"),
    ("bubble", "EXACT"), ("both", "EXACT"), ("crlf", "EXACT"),
    ("other_bubble", "EXACT"),
    ("zero", "ABSENT"), ("page_only", "ABSENT"), ("assistant_only", "ABSENT"),
    ("partial", "ABSENT"), ("prefix", "ABSENT"), ("suffix", "ABSENT"),
    ("spaces", "ABSENT"), ("double_newline", "ABSENT"), ("case", "ABSENT"),
    ("cr_only", "ABSENT"), ("hidden", "AMBIGUOUS"), ("no_rects", "AMBIGUOUS"),
    ("hidden_legacy", "AMBIGUOUS"), ("no_turn", "AMBIGUOUS"),
    ("nested_turns", "AMBIGUOUS"), ("duplicate_bubbles", "AMBIGUOUS"),
    ("duplicate_same_turn", "AMBIGUOUS"), ("hidden_duplicate", "AMBIGUOUS"),
    ("duplicate_legacy", "AMBIGUOUS"), ("mixed_distinct", "AMBIGUOUS"),
    ("nested_markers", "AMBIGUOUS"), ("nested_mismatched_marker", "AMBIGUOUS"),
    ("other_bubble_same_turn", "AMBIGUOUS"), ("assistant_ancestor", "AMBIGUOUS"),
    ("both_no_turn", "AMBIGUOUS"), ("assistant_descendant", "AMBIGUOUS"),
])
def test_bounded_outbound_resolver_and_shared_pre_post_send_contract(binding, scenario, expected):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the isolated JavaScript DOM harness")
    args = wake.BrowserAdapter(binding).arguments(wake.doorbell(EVENT, wake.REPOSITORY))
    harness = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8')), args = input.args;
const nodes = [];
function element(attrs = {}, parentElement = null, body = '') {
  const e = {attrs, parentElement, body, hidden: false, noRects: false,
    getAttribute(name) {return this.attrs[name] ?? null;},
    getClientRects() {return this.noRects ? [] : [1];},
    matches(selector) {
      if (selector === args.userTurn) return this.attrs['data-message-author-role'] === 'user';
      if (selector === args.userBubble) return 'data-user-message-bubble' in this.attrs;
      if (selector === args.turnContainer) return 'data-turn-key' in this.attrs;
      throw Error('unbounded selector');
    },
    contains(child) {
      for (let p = child; p; p = p.parentElement) if (p === this) return true;
      return false;
    },
    querySelector(selector) {
      if (selector !== '[data-message-author-role]:not([data-message-author-role="user"])')
        throw Error('unbounded descendant query');
      return nodes.find(e => e !== this && this.contains(e) &&
        'data-message-author-role' in e.attrs && e.attrs['data-message-author-role'] !== 'user') ?? null;
    },
    get textContent() {
      if (this.unreadable) throw Error('unbounded text read');
      return this.body + nodes.filter(n => n.parentElement === this).map(n => n.textContent).join('');
    }
  };
  nodes.push(e); return e;
}
const root = element(), turn = element({'data-turn-key': 'fixture'}, root);
const bubble = parent => element({'data-user-message-bubble': ''}, parent, args.text);
const legacy = parent => element({'data-message-author-role': 'user'}, parent, args.text);
// Unmarked page text and assistant output must never be read, even if exact.
const pageText = element({}, root, args.text), assistant = element(
  {'data-message-author-role': 'assistant'}, root, args.text);
pageText.unreadable = assistant.unreadable = true;
const name = input.scenario;
let candidate;
if (['zero', 'page_only', 'assistant_only'].includes(name)) {
  // No user-specific candidates, despite page-global exact text.
} else if (['legacy', 'legacy_child', 'hidden_legacy', 'duplicate_legacy'].includes(name)) {
  candidate = legacy(root); // Historical fixtures need no turn-key ancestor.
  if (name === 'legacy_child') {candidate.body = ''; element({}, candidate, args.text);}
  if (name === 'hidden_legacy') candidate.hidden = true;
  if (name === 'duplicate_legacy') legacy(root);
} else {
  candidate = bubble(turn);
  if (['both', 'both_no_turn'].includes(name)) candidate.attrs['data-message-author-role'] = 'user';
  if (name === 'crlf') candidate.body = args.text.replace(/\n/g, '\r\n');
  if (name === 'partial') candidate.body = args.text.split('\n')[1];
  if (name === 'prefix') candidate.body = ' ' + args.text;
  if (name === 'suffix') candidate.body = args.text + '\n';
  if (name === 'spaces') candidate.body = args.text.replace(/\n/g, ' ');
  if (name === 'double_newline') candidate.body = args.text.replace(/\n/g, '\n\n');
  if (name === 'case') candidate.body = args.text.toLowerCase();
  if (name === 'cr_only') candidate.body = args.text.replace(/\n/g, '\r');
  if (name === 'hidden') candidate.hidden = true;
  if (name === 'no_rects') candidate.noRects = true;
  if (['no_turn', 'both_no_turn'].includes(name)) candidate.parentElement = root;
  if (name === 'nested_turns') candidate.parentElement = element({'data-turn-key': 'nested'}, turn);
  if (['duplicate_bubbles', 'hidden_duplicate'].includes(name)) {
    const duplicate = bubble(element({'data-turn-key': 'second'}, root));
    duplicate.hidden = name === 'hidden_duplicate';
  }
  if (name === 'duplicate_same_turn') bubble(turn);
  if (name === 'mixed_distinct') legacy(root);
  if (['nested_markers', 'nested_mismatched_marker'].includes(name)) {
    const wrapper = element({'data-message-author-role': 'user'}, turn,
      name === 'nested_mismatched_marker' ? 'extra' : '');
    candidate.parentElement = wrapper;
  }
  if (['other_bubble', 'other_bubble_same_turn'].includes(name)) {
    bubble(name === 'other_bubble' ? element({'data-turn-key': 'other'}, root) : turn).body =
      'Unrelated synthetic user message';
  }
  if (name === 'assistant_ancestor') candidate.parentElement = assistant;
  if (name === 'assistant_descendant') {
    const child = element({'data-message-author-role': 'assistant'}, candidate, args.text);
    child.unreadable = true;
  }
}
global.getComputedStyle = e => ({visibility: e.hidden ? 'hidden' : 'visible'});
global.document = {querySelectorAll(selector) {
  if (selector !== args.userTurn + ', ' + args.userBubble) throw Error('page-global discovery');
  return nodes.filter(e => e.matches(args.userTurn) || e.matches(args.userBubble));
}};
const resolved = eval('(' + input.resolve + ')')(args);
let waited = false, waitRejected = false;
try {waited = eval('(' + input.wait + ')')(args);}
catch (error) {
  if (error.message !== 'SUBMISSION_UNPROVEN') throw error;
  waitRejected = true;
}
process.stdout.write(JSON.stringify({resolved, waited, waitRejected}));
"""
    process = subprocess.run(
        [node, "-e", harness], input=json.dumps({"args": args, "scenario": scenario,
                                               "resolve": wake.RESOLVE_USER_TURN,
                                               "wait": wake.WAIT_USER_TURN}),
        capture_output=True, text=True, check=True, timeout=10,
    )
    result = json.loads(process.stdout)
    assert result == {"resolved": expected, "waited": expected == "EXACT",
                      "waitRejected": expected == "AMBIGUOUS"}

    class ResolvedPage(Page):
        def evaluate(self, script, arguments):
            if script == wake.RESOLVE_USER_TURN:
                assert arguments == args
                self.resolutions.append(script)
                return result["resolved"]
            return super().evaluate(script, arguments)

    page = ResolvedPage(binding.chat_url)
    adapter = adapter_for(binding, page)
    if expected == "ABSENT":
        adapter.check(page.payload)
    else:
        with pytest.raises(wake.WakeBlocked, match="OUTBOUND_ALREADY_PRESENT"):
            adapter.check(page.payload)
    if expected == "EXACT":
        adapter.prove(page.payload)
        assert page.evaluations == [wake.PROVE_SEND]
    else:
        with pytest.raises(wake.WakeBlocked, match="SUBMISSION_UNPROVEN"):
            adapter.prove(page.payload)
        assert not page.evaluations
    assert set(page.resolutions) == {wake.RESOLVE_USER_TURN}


@pytest.mark.parametrize("condition", ["missing_turn", "duplicate_turn", "wrong_text", "draft", "send_enabled", "send_multiple", "wrong_chat", "second_target"])
def test_submission_proof_fails_closed(binding, condition):
    page = Page(binding.chat_url)
    adapter = adapter_for(binding, page)
    adapter.submit(page.payload)
    if condition == "missing_turn":
        page.counts["outbound"] = 0
    elif condition == "duplicate_turn":
        page.counts["outbound"] = 2
    elif condition == "wrong_text":
        # No whitespace-normalized substitute is eligible for bounded proof.
        page.outbound = page.payload.replace("\n", " ")
    elif condition == "draft":
        page.draft = "Human draft"
    elif condition == "send_enabled":
        page.send_enabled = True
    elif condition == "send_multiple":
        page.counts[wake.SEND] = 2
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


@pytest.mark.parametrize("send_count,enabled", [(0, False), (2, True), (1, False)])
def test_application_acceptance_blocks_before_click(binding, send_count, enabled):
    class UnacceptedPage(Page):
        def evaluate(self, script, args):
            result = super().evaluate(script, args)
            if script == wake.INSERT:
                self.counts[wake.SEND] = send_count
                self.send_enabled = enabled
            return result

    page = UnacceptedPage(binding.chat_url)
    with pytest.raises(wake.WakeBlocked, match="INSERT_BLOCKED"):
        adapter_for(binding, page).submit(page.payload)
    assert wake.CLICK not in page.evaluations
    assert page.draft == page.payload and page.counts.get("outbound", 0) == 0


def test_target_uniqueness_rechecked_after_staging(binding):
    page = Page(binding.chat_url)
    adapter = adapter_for(binding, page)
    evaluate = page.evaluate

    def duplicate_after_insert(script, args):
        result = evaluate(script, args)
        if script == wake.INSERT:
            adapter.browser.contexts[0].pages.append(Page(binding.chat_url))
        return result

    page.evaluate = duplicate_after_insert
    with pytest.raises(wake.WakeBlocked, match="TARGET_PAGE_NOT_UNIQUE"):
        adapter.submit(page.payload)
    assert page.evaluations == [wake.INSERT] and page.draft == page.payload


def test_human_edit_during_application_wait_is_preserved(binding, monkeypatch):
    page = Page(binding.chat_url)
    wait = Page.wait_for_function

    def edit_while_waiting(target, script, **kwargs):
        wait(target, script, **kwargs)
        page.draft += " Human edit"

    monkeypatch.setattr(Page, "wait_for_function", edit_while_waiting)
    with pytest.raises(wake.WakeBlocked, match="INSERT_BLOCKED"):
        adapter_for(binding, page).submit(page.payload)
    assert page.draft == page.payload + " Human edit"
    assert wake.CLICK not in page.evaluations and page.counts.get("outbound", 0) == 0


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
    for script in (wake.INSERT, wake.ACCEPT_INSERT, wake.CLICK):
        for guard in ("location.href.replace", "visible(account).length === 1", "visible(stop).length",
                      "visible(nonregular).length", "visible(login).length"):
            assert guard in script
    assert "box.textContent === ''" in wake.INSERT
    assert "visible(composer)[0] === box" in wake.INSERT
    assert wake.INSERT.index("box.focus()") < wake.INSERT.index("if (!empty() ||") < wake.INSERT.index("document.execCommand")
    assert "equivalent(boxes[0])" in wake.CLICK
    assert "const button = ready();" in wake.CLICK and "button.click()" in wake.CLICK
    for script in (wake.ACCEPT_INSERT, wake.CLICK, wake.PROVE_SEND):
        assert wake._SEND_GUARDS in script
        assert "forms[0].querySelectorAll(send)" in script
        assert "visible(send)" not in script
        assert "document.querySelectorAll(send)" not in script


@pytest.mark.parametrize("shape", ["legacy", "live", "both"])
@pytest.mark.parametrize("phase", ["ready", "click", "proof"])
@pytest.mark.parametrize("scenario,ready,proven", [
    ("single", True, False),
    ("absent", False, True),
    ("disabled", False, True),
    ("aria_disabled", False, True),
    ("multiple", False, False),
    ("multiple_disabled", False, False),
    ("mixed_shapes", False, False),
    ("hidden_extra", True, False),
    ("hidden_only", False, True),
    ("outside_only", False, True),
    ("other_form_only", False, True),
    ("outside_extra", True, False),
    ("unrelated_inside", False, True),
    ("no_form", False, False),
    ("nested_forms", False, False),
    ("no_composer", False, False),
    ("multiple_composers", False, False),
])
def test_exact_form_send_resolver_on_synthetic_dom(binding, shape, phase, scenario, ready, proven):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the isolated JavaScript DOM harness")
    args = wake.BrowserAdapter(binding).arguments(wake.doorbell(EVENT, wake.REPOSITORY))
    harness = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const args = input.args;
global.location = {href: args.url};
global.getComputedStyle = e => ({visibility: e.hidden ? 'hidden' : 'visible'});
let clicks = 0, formQueries = 0;
const element = (tagName, attrs = {}, parentElement = null) => ({
  tagName, attrs, parentElement, disabled: false,
  getAttribute(name) {return this.attrs[name] ?? null;},
  getClientRects() {return this.noRects ? [] : [1];},
  click() {clicks++;}
});
const main = element('MAIN'), form = element('FORM', {}, main);
const otherForm = element('FORM', {}, main), nested = element('FORM', {}, form);
const box = Object.assign(element('DIV', {}, form), {
  innerText: args.text, textContent: args.text, childNodes: []
});
let boxes = [box], controls = [];
const legacy = '[data-testid="send-button"]';
const live = 'button[type="submit"][aria-label="Send"]';
function matches(e, branch) {
  if (branch === legacy) return e.attrs['data-testid'] === 'send-button';
  if (branch === live) return e.tagName === 'BUTTON' && e.attrs.type === 'submit' &&
    e.attrs['aria-label'] === 'Send';
  throw Error('unbounded selector');
}
function contains(root, e) {
  for (let parent = e.parentElement; parent; parent = parent.parentElement)
    if (parent === root) return true;
  return false;
}
for (const root of [form, otherForm, nested]) {
  root.contains = e => contains(root, e);
  root.querySelectorAll = selector => {
    if (root !== form || selector !== args.send) throw Error('wrong control surface');
    formQueries++;
    // CSS union deduplicates an element matching both exact branches.
    return controls.filter(e => contains(root, e) &&
      selector.split(',').some(branch => matches(e, branch.trim())));
  };
}
function control(shape = input.shape, parent = form) {
  const attrs = {};
  if (shape !== 'live') attrs['data-testid'] = 'send-button';
  if (shape !== 'legacy') Object.assign(attrs, {type: 'submit', 'aria-label': 'Send'});
  return element('BUTTON', attrs, parent);
}
function configure(name) {
  box.parentElement = form; boxes = [box]; controls = [control()];
  if (name === 'absent') controls = [];
  if (name === 'disabled') controls[0].disabled = true;
  if (name === 'aria_disabled') controls[0].attrs['aria-disabled'] = 'true';
  if (name === 'multiple') controls.push(control());
  if (name === 'multiple_disabled') {
    controls.push(control()); controls.forEach(e => e.disabled = true);
  }
  if (name === 'mixed_shapes') controls = [control('legacy'), control('live')];
  if (name === 'hidden_extra') {
    controls.push(Object.assign(control(), {hidden: true}));
    controls.push(Object.assign(control(), {noRects: true}));
  }
  if (name === 'hidden_only') controls[0].hidden = true;
  if (name === 'outside_only') controls = [control('legacy', main), control('live', main)];
  if (name === 'other_form_only') controls = [control('legacy', otherForm), control('live', otherForm)];
  if (name === 'outside_extra') controls.push(control('legacy', main), control('live', otherForm));
  if (name === 'unrelated_inside') controls = [
    element('BUTTON', {type: 'button', 'aria-label': 'Send'}, form),
    element('BUTTON', {type: 'submit', 'aria-label': 'send'}, form),
    element('BUTTON', {type: 'submit', 'aria-label': 'Send feedback'}, form),
    element('DIV', {type: 'submit', 'aria-label': 'Send'}, form),
    element('BUTTON', {type: 'submit', 'aria-label': 'Other'}, form)
  ];
  if (name === 'no_form') box.parentElement = main;
  if (name === 'nested_forms') box.parentElement = nested;
  if (name === 'no_composer') boxes = [];
  if (name === 'multiple_composers') boxes.push(element('DIV', {}, form));
}
global.document = {querySelectorAll(selector) {
  if (selector === args.send) throw Error('global Send discovery');
  if (selector === args.composer) return boxes;
  if (selector === args.account) return [element('BUTTON')];
  if (selector === 'main') return [main];
  if ([args.stop, args.login, args.nonregular].includes(selector)) return [];
  throw Error('unexpected page query');
}};
configure(input.phase === 'click' ? 'single' : input.scenario);
if (input.phase === 'click') {
  if (!eval('(' + input.accept + ')')(args)) throw Error('baseline readiness failed');
  configure(input.scenario); // Re-resolve after a change at the final click boundary.
}
if (input.phase === 'proof') box.innerText = box.textContent = '';
const script = input.phase === 'ready' ? input.accept : input.phase === 'click' ? input.click : input.proof;
const result = eval('(' + script + ')')(args);
process.stdout.write(JSON.stringify({result, clicks, formQueries}));
"""
    process = subprocess.run(
        [node, "-e", harness], input=json.dumps({"args": args, "shape": shape,
                                               "phase": phase, "scenario": scenario,
                                               "accept": wake.ACCEPT_INSERT, "click": wake.CLICK,
                                               "proof": wake.PROVE_SEND}),
        capture_output=True, text=True, check=True, timeout=10,
    )
    result = json.loads(process.stdout)
    assert result["result"] is (proven if phase == "proof" else ready)
    assert result["clicks"] == (1 if phase == "click" and ready else 0)
    if scenario not in {"no_form", "nested_forms", "no_composer", "multiple_composers"}:
        assert result["formQueries"] >= 1


@pytest.mark.parametrize("scenario", [
    "valid", "draft", "focus_draft", "focus_navigation", "busy", "wrong_surface",
    "logged_out", "send_edit", "send_disabled", "wrong_project", "focus_project", "send_project",
    "fallback", "overlap", "legacy_accounts", "hidden_extras", "login", "missing_composer",
    "duplicate_account", "duplicate_composer", "focus_account_ambiguous", "focus_composer_ambiguous",
    "send_account_missing", "send_account_ambiguous", "send_composer_missing", "send_composer_ambiguous",
    "dom_only", "input_edit", "input_navigation", "input_busy", "input_login", "input_main_missing",
    "insert_disabled", "send_zero", "send_multiple", "send_aria_disabled", "send_busy", "send_login",
    "blocks_double", "blocks_missing_separator", "blocks_mixed", "blocks_root_text",
    "flat_double", "trailing_newline", "leading_space", "case_changed", "missing_line",
    "extra_line", "reordered_lines", "collapsed_spaces", "blocks_triple", "blocks_extra_node",
    "blocks_nested", "blocks_hidden", "blocks_trailing", "blocks_substituted",
    "send_flat_double", "send_trailing_newline", "send_blocks_substituted",
    "send_blocks_double", "send_blocks_missing_separator", "send_surface_disabled",
    "blocks_single", "flat_missing_separator", "flat_triple", "trailing_space", "crlf",
    "blocks_hidden_extra", "native_edit", "focus_lost", "selection_missing",
    "insert_multiple", "insert_aria_disabled",
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
global.getComputedStyle = e => ({visibility: e.hidden ? 'hidden' : 'visible'});
let clicks = 0, inserts = 0, notifications = 0, appText = '';
const element = () => ({getClientRects: () => [1], getAttribute: () => null});
const textNode = text => ({nodeType: 3, textContent: text});
const box = Object.assign(element(), {textContent: '', innerText: '', childNodes: [], focus() {
  document.activeElement = box;
  if (input.scenario === 'focus_draft') box.textContent = box.innerText = 'Human draft';
  if (input.scenario === 'focus_navigation') location.href = 'https://example.invalid/';
  if (input.scenario === 'focus_project') location.href = input.other_url;
  if (input.scenario === 'focus_account_ambiguous') elements[accountLegacy].push(element());
  if (input.scenario === 'focus_composer_ambiguous') elements[composerLegacy].push(element());
  if (input.scenario === 'focus_lost') document.activeElement = null;
}, dispatchEvent(event) {
  if (event.type !== 'input' || !event.bubbles || !event.composed ||
      event.inputType !== 'insertText' || event.data !== args.text) throw Error('bad notification');
  notifications++;
  if (input.scenario === 'input_edit') setFlat(args.text + ' Human edit');
  if (input.scenario === 'input_navigation') location.href = input.other_url;
  if (input.scenario === 'input_busy') elements[args.stop] = [element()];
  if (input.scenario === 'input_login') elements[args.login] = [element()];
  if (input.scenario === 'input_main_missing') elements.main = [];
  if (input.scenario !== 'dom_only') {
    appText = box.innerText;
    replace(args.send, [button]);
    button.disabled = input.scenario === 'insert_disabled';
    if (input.scenario === 'insert_multiple') replace(args.send, [button, element()]);
    if (input.scenario === 'insert_aria_disabled') button.getAttribute = () => 'true';
  }
  return true;
}});
const button = Object.assign(element(), {disabled: false, click() {clicks++;}});
const accountLegacy = '[data-testid="profile-button"]';
const accountOtherLegacy = '[data-testid="accounts-profile-button"]';
const accountFallback = 'button[aria-label*="profile" i]';
const composerLegacy = '#prompt-textarea[contenteditable="true"]';
const composerFallback = 'main form [contenteditable="true"][role="textbox"][aria-multiline="true"]';
const account = element();
const fallback = input.scenario === 'fallback', overlap = input.scenario === 'overlap';
const otherLegacy = input.scenario === 'legacy_accounts';
const elements = {
  [composerLegacy]: fallback ? [] : [box], [composerFallback]: fallback || overlap ? [box] : [],
  [accountLegacy]: fallback || otherLegacy ? [] : [account],
  [accountOtherLegacy]: otherLegacy ? [account] : [],
  [accountFallback]: fallback || overlap ? [account] : [],
  [args.send]: [], [args.stop]: [], [args.nonregular]: [], [args.login]: [], main: [element()]};
// CSS selector lists produce a union of element identities, not branch counts.
const select = selector => [...new Set(selector.split(',').flatMap(branch => elements[branch.trim()] || []))];
const replace = (selector, matches) => {
  for (const branch of selector.split(',')) elements[branch.trim()] = matches;
};
const form = {tagName: 'FORM', parentElement: null,
  contains: e => e === box || select(args.send).includes(e), querySelectorAll: select};
box.parentElement = form;
global.document = {
  querySelectorAll: selector => {
    if (selector === args.send) throw Error('page-level Send discovery');
    return select(selector);
  },
  createRange: () => ({selectNodeContents(node) {
    if (node !== box || box.textContent !== '') throw Error('unsafe selection');
  }, collapse(value) {if (value !== true) throw Error('noncollapsed selection');}}),
  execCommand: (command, unused, text) => {
    if (command !== 'insertText') throw Error('unexpected modification');
    inserts++; setRepresentation(input.scenario.startsWith('send_') ? 'valid' : input.scenario, text);
    if (input.scenario === 'native_edit') setFlat(text + ' Human edit');
    return true;
  },
};
global.window = {getSelection: () => input.scenario === 'selection_missing' ? null :
  ({removeAllRanges() {}, addRange() {}})};
global.InputEvent = class {constructor(type, options) {this.type = type; Object.assign(this, options);}};
function setFlat(text) {
  box.textContent = box.innerText = text; box.childNodes = [textNode(text)];
}
function setRepresentation(scenario, text) {
  const name = scenario.replace(/^send_/, '');
  const lines = text.split('\n');
  if (name.startsWith('blocks_')) {
    const contents = name === 'blocks_substituted' ? lines.map((s, i) => i === 2 ? s + ' ' : s) : lines;
    box.childNodes = contents.map((line, i) => Object.assign(element(), {
      nodeType: 1, tagName: name === 'blocks_mixed' && i % 2 ? 'DIV' : 'P',
      textContent: line, childNodes: [textNode(line)]}));
    if (name === 'blocks_root_text') box.childNodes[0] = textNode(contents[0]);
    box.textContent = contents.join('');
    box.innerText = contents.join(name === 'blocks_missing_separator' ? '' :
      name === 'blocks_single' ? '\n' : name === 'blocks_triple' ? '\n\n\n' : '\n\n');
    if (name === 'blocks_mixed') box.innerText = contents[0] + '\n' + contents[1] + '\n\n' + contents[2] + contents[3];
    if (name === 'blocks_extra_node') box.childNodes.push(textNode(''));
    if (name === 'blocks_nested') box.childNodes[1].childNodes = [{nodeType: 1}];
    if (name === 'blocks_hidden') box.childNodes[1].hidden = true;
    if (name === 'blocks_trailing') box.innerText += '\n';
    if (name === 'blocks_hidden_extra') {
      box.childNodes.push(Object.assign(textNode('extra'), {hidden: true}));
      box.textContent += 'extra';
      box.innerText = text;
    }
    return;
  }
  const substitutions = {
    flat_double: text.replace(/\n/g, '\n\n'), trailing_newline: text + '\n',
    leading_space: ' ' + text, case_changed: text.toLowerCase(),
    missing_line: lines.slice(1).join('\n'), extra_line: text + '\nextra',
    reordered_lines: [lines[1], lines[0], ...lines.slice(2)].join('\n'),
    collapsed_spaces: text.replace(/ /g, ''),
    flat_missing_separator: lines.join(''), flat_triple: lines.join('\n\n\n'),
    trailing_space: text + ' ', crlf: lines.join('\r\n'),
  };
  setFlat(substitutions[name] === undefined ? text : substitutions[name]);
}
if (input.scenario === 'draft') box.textContent = box.innerText = 'Human draft';
if (input.scenario === 'busy') elements[args.stop] = [element()];
if (input.scenario === 'wrong_surface') replace(args.nonregular, [element()]);
if (input.scenario === 'login') replace(args.login, [element()]);
if (input.scenario === 'logged_out') replace(args.account, []);
if (input.scenario === 'missing_composer') replace(args.composer, []);
if (input.scenario === 'duplicate_account') replace(args.account, [account, element()]);
if (input.scenario === 'duplicate_composer') replace(args.composer, [box, element()]);
if (input.scenario === 'hidden_extras') {
  elements[accountFallback].push(Object.assign(element(), {hidden: true}));
  elements[composerFallback].push(Object.assign(element(), {getClientRects: () => []}));
}
if (input.scenario === 'wrong_project') location.href = input.other_url;
const inserted = eval('(' + input.insert + ')')(args);
const accepted = inserted && eval('(' + input.accept + ')')(args);
if (input.scenario === 'send_edit') box.textContent = box.innerText = args.text + ' Human edit';
if (input.scenario === 'send_disabled') button.disabled = true;
if (input.scenario === 'send_zero') replace(args.send, []);
if (input.scenario === 'send_multiple') replace(args.send, [button, element()]);
if (input.scenario === 'send_aria_disabled') button.getAttribute = () => 'true';
if (input.scenario === 'send_surface_disabled') box.getAttribute = () => 'true';
if (input.scenario === 'send_busy') elements[args.stop] = [element()];
if (input.scenario === 'send_login') elements[args.login] = [element()];
if (['send_flat_double', 'send_trailing_newline', 'send_blocks_substituted',
     'send_blocks_double', 'send_blocks_missing_separator'].includes(input.scenario))
  setRepresentation(input.scenario, args.text);
if (input.scenario === 'send_project') location.href = input.other_url;
if (input.scenario === 'send_account_missing') replace(args.account, []);
if (input.scenario === 'send_account_ambiguous') replace(args.account, [account, element()]);
if (input.scenario === 'send_composer_missing') replace(args.composer, []);
if (input.scenario === 'send_composer_ambiguous') replace(args.composer, [box, element()]);
const sent = accepted ? eval('(' + input.click + ')')(args) : false;
process.stdout.write(JSON.stringify({inserted, accepted, sent, inserts, clicks,
                                   notifications, appText, draft: box.textContent}));
"""
    process = subprocess.run(
        [node, "-e", harness], input=json.dumps({"args": arguments, "scenario": scenario,
                                               "other_url": synthetic_project_url(project="g-fixture-project-2"),
                                               "insert": wake.INSERT, "accept": wake.ACCEPT_INSERT,
                                               "click": wake.CLICK}),
        capture_output=True, text=True, check=True, timeout=10,
    )
    result = json.loads(process.stdout)
    if scenario in {"valid", "fallback", "overlap", "legacy_accounts", "hidden_extras",
                    "blocks_double", "blocks_single", "blocks_missing_separator", "blocks_mixed", "blocks_root_text",
                    "send_blocks_double", "send_blocks_missing_separator"}:
        assert result["inserted"] is result["accepted"] is result["sent"] is True
        assert result["inserts"] == result["notifications"] == result["clicks"] == 1
        assert result["appText"]  # Application state was activated by the input notification.
        if not scenario.startswith("blocks_") and not scenario.startswith("send_blocks_"):
            assert result["draft"] == result["appText"] == payload
    else:
        assert result["sent"] is False and result["clicks"] == 0
        if scenario in {"draft", "focus_draft", "focus_navigation", "focus_project", "busy",
                        "wrong_surface", "logged_out", "wrong_project", "login", "missing_composer",
                        "duplicate_account", "duplicate_composer", "focus_account_ambiguous",
                        "focus_composer_ambiguous", "focus_lost", "selection_missing"}:
            assert result["inserted"] is False and result["inserts"] == 0
        if scenario in {"flat_double", "trailing_newline", "leading_space", "case_changed",
                        "missing_line", "extra_line", "reordered_lines", "collapsed_spaces",
                        "blocks_triple", "blocks_extra_node", "blocks_nested", "blocks_hidden",
                        "blocks_trailing", "blocks_substituted", "blocks_hidden_extra",
                        "flat_missing_separator", "flat_triple", "trailing_space", "crlf",
                        "native_edit", "input_edit", "input_navigation", "input_busy",
                        "input_login", "input_main_missing"}:
            assert result["inserted"] is False and result["accepted"] is False
        if scenario in {"send_flat_double", "send_trailing_newline", "send_blocks_substituted"}:
            assert result["accepted"] is True  # A later payload substitution is rejected at click.
        if scenario == "dom_only":
            assert result["inserted"] is True and result["accepted"] is False
            assert result["draft"] == payload and result["appText"] == ""
        if scenario in {"insert_disabled", "insert_multiple", "insert_aria_disabled"}:
            assert result["accepted"] is False
        if scenario in {"draft", "focus_draft"}:
            assert result["draft"] == "Human draft" and result["notifications"] == 0
        if scenario in {"send_edit", "input_edit", "native_edit"}:
            assert result["draft"] == payload + " Human edit"
