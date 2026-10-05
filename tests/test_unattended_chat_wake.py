"""Inert CDP/OS fixtures for H4D; no browser process or ChatGPT connection."""

import asyncio
from dataclasses import replace
import errno
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest
from playwright._impl._errors import Error as BridgeError, TimeoutError as BridgeTimeout

from aios_renew import local_chat_wake as wake
from aios_renew import unattended_chat_wake as unattended
from test_local_chat_wake import EVENT, Page, AffineProjection, affine_setup

LOCAL_ENVIRONMENT = unattended.LocalEnvironment
BOUNDED_CALL = unattended._bounded_call

class AcquiredPage(Page):
    def __init__(self, harness, url):
        super().__init__(url)
        self.harness = harness

    def set_default_timeout(self, timeout):
        self.harness.timeouts.append(timeout)

    def goto(self, url, *, wait_until, timeout):
        assert url == self.harness.binding.chat_url
        assert wait_until == "domcontentloaded" and 0 < timeout <= 8000
        self.harness.navigations.append(url)
        self.url = url
        self.harness.hook("navigate")

    def wait_for_function(self, script, *, arg, timeout):
        if script == wake.WAIT_USER_TURN and self.harness.ambiguous:
            raise RuntimeError("private post-send uncertainty")
        super().wait_for_function(script, arg=arg, timeout=timeout)
        if script == wake.ACCEPT_INSERT:
            self.harness.hook("inserted")

    def evaluate(self, script, arguments):
        if script == wake.CLICK:
            bucket = self.harness.stored()
            assert bucket["events"][EVENT] == wake.record(
                "AMBIGUOUS", self.harness.binding.generation, "ATTEMPT_REQUIRES_HUMAN")
        return super().evaluate(script, arguments)


class Context:
    def __init__(self, harness, pages=()):
        self.harness, self.pages = harness, list(pages)

    def set_default_timeout(self, timeout):
        assert 0 < timeout <= 3000

    def new_page(self):
        self.harness.created += 1
        page = AcquiredPage(self.harness, "about:blank")
        page.context = self
        self.pages.append(page)
        self.harness.page = page
        self.harness.hook("created")
        return page


@pytest.fixture
def harness(tmp_path, monkeypatch):
    registry, selectors, bindings = affine_setup(tmp_path, monkeypatch)
    binding = bindings[0]
    executable = tmp_path / "authorized-browser.exe"
    executable.touch()
    directory = tmp_path / "authorized-user-data"
    (directory / "Default").mkdir(parents=True)
    config = tmp_path / "acquisition.json"
    document = dict(version=1, environments=[dict(
        cdp_endpoint=binding.cdp_endpoint, executable=str(executable),
        user_data_dir=str(directory), profile_directory="Default",
        allow_launch=True, exclusive_user_data=True)])
    config.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv(unattended.CONFIG_ENV, str(config))
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-private-freshness-token")
    h = SimpleNamespace(binding=binding, registry=registry, selector=selectors[0],
                        projection=AffineProjection(selectors[0]), config=config, document=document,
                        available=True, owned=True, ambiguous=False, connect_failure=False,
                        connect_error=RuntimeError("private CDP connection detail"), listener_queries=[],
                        created=0, navigations=[], launches=[], connects=[], timeouts=[],
                        checks=[], stops=[], processes=[], sleeps=[], timeout_after=None,
                        hook=lambda stage: None, page=None, moved=None, nondefault_contexts=[])
    h.browser = SimpleNamespace(contexts=[Context(h)])
    h.process = SimpleNamespace(pid=123, poll=lambda: None)
    h.stored = lambda: json.loads(binding.state_path.read_text())["repositories"][wake.REPOSITORY]
    h.provider = lambda: h.moved or wake.load_route_binding(wake.REPOSITORY, h.selector)
    port = unattended.load_environment(binding).port
    h.listener_observation = lambda: ([dict(LocalAddress="127.0.0.1",
                                           LocalPort=port,
                                           OwningProcess=h.process.pid)] if h.available else [])
    h.deliver = lambda: wake.deliver(EVENT, binding.repository, binding,
                                    projection=h.projection, binding_provider=h.provider)

    def connect(endpoint, *, timeout):
        assert endpoint == binding.cdp_endpoint and 0 < timeout <= 10000
        h.connects.append(timeout)
        h.hook("connect")
        if not h.available or h.connect_failure:
            raise h.connect_error
        return h.browser

    driver = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect),
                             stop=lambda: h.stops.append("disconnect"))
    h.driver = driver
    module = SimpleNamespace(sync_playwright=lambda: SimpleNamespace(start=lambda: driver))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)

    class SyntheticLocal(unattended.LocalEnvironment):
        def remaining_ms(self, ceiling=3000):
            if h.timeout_after is not None and len(h.connects) >= h.timeout_after:
                raise wake.WakeBlocked("ACQUISITION_TIMED_OUT")
            return super().remaining_ms(ceiling)

        def _query(self, script):
            if "Get-NetTCPConnection" in script:
                h.listener_queries.append(script)
                self.remaining_ms()
                return h.listener_observation()
            h.checks.append("profile")
            return h.processes

        def prove_owner(self):
            h.checks.append("owner")
            if not h.owned:
                raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            if self.process is not None and self.process.poll() is not None:
                raise wake.WakeBlocked("LAUNCH_UNCERTAIN")
            h.hook("owner")
            self.remaining_ms()

    def popen(command, **kwargs):
        assert command == unattended.load_environment(binding).command()
        assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == unattended.subprocess.DEVNULL
        assert "GITHUB_TOKEN" not in kwargs["env"]
        assert not any(key.startswith("AIOS_") for key in kwargs["env"])
        assert "shell" not in kwargs and kwargs["close_fds"] is True
        h.launches.append(command)
        h.available = True
        h.hook("launched")
        return h.process

    monkeypatch.setattr(unattended, "LocalEnvironment", SyntheticLocal)
    monkeypatch.setattr(unattended.subprocess, "Popen", popen)
    monkeypatch.setattr(unattended.time, "sleep", lambda delay: h.sleeps.append(delay))
    def bounded(api, method, local, *args):
        local.remaining_ms()
        if method == "new_page": return api.new_page()
        if method == "new_browser_cdp_session": return SimpleNamespace()
        if method == "send":
            assert args == ("Target.getBrowserContexts",)
            return {"browserContextIds": h.nondefault_contexts}
        if method == "detach": return None
        pytest.fail("Unexpected bounded CDP operation")
    monkeypatch.setattr(unattended, "_bounded_call", bounded)
    return h


@pytest.fixture
def production_bridge(harness, monkeypatch):
    """Use the real bounded bridge/mapping with inert async implementations."""
    h = harness
    h.context_response = {"browserContextIds": [], "defaultBrowserContextId": "opaque-fixture-metadata"}
    h.bridge_calls, h.bridge_failures = [], {}
    context = h.browser.contexts[0]

    def observe(method):
        h.bridge_calls.append(method)
        if method in h.bridge_failures:
            raise h.bridge_failures[method]
        h.hook(method)

    async def send(command):
        assert command == "Target.getBrowserContexts"
        observe("send")
        return h.context_response

    async def detach():
        observe("detach")

    session = SimpleNamespace(_impl_obj=SimpleNamespace(send=send, detach=detach), _sync=asyncio.run)

    async def new_browser_cdp_session():
        observe("new_browser_cdp_session")
        return session

    async def new_page():
        observe("new_page")
        page = context.new_page()
        if "new_page_result" in h.bridge_failures:
            raise h.bridge_failures["new_page_result"]
        return page

    h.browser._impl_obj = SimpleNamespace(new_browser_cdp_session=new_browser_cdp_session)
    h.browser._sync = asyncio.run
    context._impl_obj = SimpleNamespace(new_page=new_page)
    context._sync = asyncio.run
    monkeypatch.setattr(unattended, "_bounded_call", BOUNDED_CALL)

    # Exercise the exact production owner classifier before creation and again
    # after navigation, using only synthetic OS observations and arguments.
    environment = unattended.load_environment(h.binding)
    h.owner = dict(ProcessId=h.process.pid, ExecutablePath=str(environment.executable),
                   CommandLine="private-fixture-owner-command")
    synthetic_query = unattended.LocalEnvironment._query

    def query(local, script):
        if "Win32_Process -Filter" in script:
            h.checks.append("owner")
            h.hook("owner")
            return h.owner
        return synthetic_query(local, script)

    monkeypatch.setattr(unattended.LocalEnvironment, "_query", query)
    monkeypatch.setattr(unattended.LocalEnvironment, "prove_owner", LOCAL_ENVIRONMENT.prove_owner)
    monkeypatch.setattr(unattended, "_windows_arguments", lambda command: environment.command())
    return h


def existing_page(h):
    page = AcquiredPage(h, h.binding.chat_url)
    page.context = h.browser.contexts[0]
    h.browser.contexts[0].pages.append(page)
    h.page = page
    return page


def test_attach_existing_never_reads_acquisition_config_or_owns_launch(harness, monkeypatch):
    h = harness
    page = existing_page(h)
    monkeypatch.delenv(unattended.CONFIG_ENV)
    assert h.deliver()["status"] == "SUBMITTED"
    assert page.evaluations.count(wake.INSERT) == page.evaluations.count(wake.CLICK) == 1
    assert not h.launches and not h.navigations and not h.created and not h.checks and not h.listener_queries
    assert h.connects == [10000] and h.stops == ["disconnect"]
    assert h.deliver()["status"] == "NOOP"
    assert h.connects == [10000]


def test_available_owned_endpoint_creates_one_exact_page_and_submits(harness):
    h = harness
    # An unrelated page is never chosen or navigated, even in the proved context.
    other = AcquiredPage(h, "https://chatgpt.com/")
    h.browser.contexts[0].pages.append(other)
    assert h.deliver()["status"] == "SUBMITTED"
    assert h.created == 1 and h.navigations == [h.binding.chat_url] and not h.launches
    assert h.checks == ["owner", "owner"]
    assert other.url == "https://chatgpt.com/" and not other.evaluations
    assert h.page.evaluations.count(wake.INSERT) == h.page.evaluations.count(wake.CLICK) == 1
    assert h.stored()["events"][EVENT] == wake.record("SUBMITTED", h.binding.generation)
    serialized = json.dumps(h.deliver())
    for sensitive in (h.binding.chat_url, h.binding.cdp_endpoint,
                      str(h.config), str(unattended.load_environment(h.binding).executable),
                      "fixture-private-freshness-token"):
        assert sensitive not in serialized


def test_unavailable_endpoint_launches_only_configured_environment_once(harness):
    h = harness
    h.available = False
    assert h.deliver()["status"] == "SUBMITTED"
    assert len(h.launches) == 1 and h.created == 1 and h.navigations == [h.binding.chat_url]
    assert h.launches[0][-1] == "--no-startup-window" and h.binding.chat_url not in h.launches[0]
    assert h.checks == ["profile", "owner", "owner"]
    assert len(h.listener_queries) == 2
    assert h.connects == [10000, 1000]
    assert h.stops == ["disconnect"]


def test_launch_can_attach_exact_page_restored_by_the_authorized_environment(harness):
    h = harness
    h.available = False
    h.hook = lambda stage: existing_page(h) if stage == "launched" else None
    assert h.deliver()["status"] == "SUBMITTED"
    assert len(h.launches) == 1 and not h.created and not h.navigations


@pytest.mark.parametrize("count", [0, 2])
def test_missing_target_with_unproved_context_fails_without_page_creation(harness, count):
    h = harness
    h.browser.contexts = [Context(h) for _ in range(count)]
    assert h.deliver()["reason"] == "BROWSER_CONTEXT_UNPROVEN"
    assert not h.created and not h.navigations and not h.launches


def test_multiple_exact_pages_fail_before_configuration_or_selection(harness, monkeypatch):
    h = harness
    existing_page(h)
    existing_page(h)
    monkeypatch.delenv(unattended.CONFIG_ENV)
    assert h.deliver()["reason"] == "TARGET_PAGE_NOT_UNIQUE"
    assert not h.created and not h.launches and not h.checks
    assert all(not page.evaluations for page in h.browser.contexts[0].pages)


@pytest.mark.parametrize("drift", ["duplicate", "context", "redirect", "new-page-target"])
def test_acquisition_target_or_context_races_fail_without_editing(harness, drift):
    h = harness
    def race(stage):
        if stage == "created" and drift == "duplicate":
            existing_page(h)
        if stage == "created" and drift == "context":
            h.browser.contexts.append(Context(h))
        if stage == "created" and drift == "new-page-target":
            h.page.url = "https://chatgpt.com/"
        if stage == "navigate" and drift == "redirect":
            h.page.url = "https://chatgpt.com/auth/login"
    h.hook = race
    assert h.deliver()["status"] == "DEFERRED"
    assert h.created == 1 and len(h.navigations) <= 1
    assert not any(page.evaluations for context in h.browser.contexts for page in context.pages)


@pytest.mark.parametrize("defect", ["missing", "malformed", "extra", "path", "profile", "endpoint", "duplicate", "exclusive"])
def test_invalid_configuration_never_discovers_launches_or_navigates(harness, monkeypatch, defect):
    h = harness
    h.available = False
    item = h.document["environments"][0]
    if defect == "missing":
        monkeypatch.delenv(unattended.CONFIG_ENV)
    elif defect == "malformed":
        h.config.write_text('{"version":1,"version":1}', encoding="utf-8")
    else:
        if defect == "extra": item["arguments"] = ["--some-inferred-account"]
        if defect == "path": item["executable"] = "browser-from-PATH.exe"
        if defect == "profile": item["profile_directory"] = "../other"
        if defect == "endpoint": item["cdp_endpoint"] = "http://127.0.0.1:9333"
        if defect == "duplicate": h.document["environments"].append(dict(item))
        if defect == "exclusive": item["exclusive_user_data"] = False
        h.config.write_text(json.dumps(h.document), encoding="utf-8")
    assert h.deliver()["status"] == "DEFERRED"
    assert not h.launches and not h.created and not h.navigations and not h.checks


@pytest.mark.parametrize("marker", ["SingletonLock", "SingletonSocket", "SingletonCookie", "lockfile", "DevToolsActivePort", ".aios-unattended-acquisition.lock"])
def test_locked_or_stale_profile_is_preserved_without_launch(harness, marker):
    h = harness
    h.available = False
    h.processes = [None]  # Directory markers are authoritative before classification.
    path = Path(h.document["environments"][0]["user_data_dir"]) / marker
    path.write_text("Human-owned or uncertain", encoding="utf-8")
    assert h.deliver()["reason"] == "PROFILE_LOCKED"
    assert path.read_text() == "Human-owned or uncertain"
    assert len(h.listener_queries) == (0 if marker == ".aios-unattended-acquisition.lock" else 1)
    assert not h.launches and not h.navigations and not h.created and not h.checks


def test_launch_permission_can_only_be_disabled_by_local_configuration(harness):
    h = harness
    h.available = False
    h.document["environments"][0]["allow_launch"] = False
    h.config.write_text(json.dumps(h.document), encoding="utf-8")
    assert h.deliver()["reason"] == "LAUNCH_NOT_AUTHORIZED"
    assert not h.launches and not h.created


@pytest.mark.parametrize("failure", ["ownership", "connect-to-live-endpoint", "launch-exit", "timeout"])
def test_uncertain_browser_fails_without_kill_rebind_or_alternate_endpoint(harness, failure):
    h = harness
    if failure == "ownership": h.owned = False
    if failure == "connect-to-live-endpoint": h.connect_failure = True
    if failure == "launch-exit":
        h.available = False
        h.process.poll = lambda: 1
    if failure == "timeout":
        h.available = False
        h.connect_failure = True
        h.timeout_after = 3
    receipt = h.deliver()
    assert receipt["reason"] in {"BROWSER_OWNERSHIP_UNPROVEN", "LAUNCH_UNCERTAIN", "ACQUISITION_TIMED_OUT"}
    assert len(h.launches) <= 1 and not h.created and not h.navigations
    assert h.stops == ["disconnect"]


@pytest.mark.parametrize("freshness", ["UNKNOWN", "RESOLVED"])
def test_unproven_or_resolved_canonical_subject_cannot_begin_acquisition(harness, freshness):
    h = harness
    h.available = False
    h.projection.freshness = freshness
    assert h.deliver()["reason"] == ("CANONICAL_UNKNOWN" if freshness == "UNKNOWN" else "CANONICALLY_RESOLVED")
    assert not h.launches and not h.created and not h.navigations and not h.checks


def test_resolution_between_attach_and_acquisition_is_a_noop(harness):
    h = harness
    def resolved(stage):
        if stage == "connect": h.projection.freshness = "RESOLVED"
    h.hook = resolved
    assert h.deliver()["reason"] == "CANONICALLY_RESOLVED"
    assert h.stored()["events"][EVENT]["status"] == "RESOLVED_NOOP"
    assert not h.created and not h.launches and not h.checks


@pytest.mark.parametrize("race", ["resolved", "unknown", "affinity", "generation", "url", "endpoint", "state-path", "route-handle"])
def test_post_acquisition_revalidation_blocks_canonical_and_binding_races(harness, race):
    h = harness
    def changed(stage):
        if stage != "navigate": return
        if race == "resolved": h.projection.freshness = "RESOLVED"
        elif race == "unknown": h.projection.freshness = "UNKNOWN"
        elif race == "affinity": h.projection.selector = replace(h.selector, generation=2)
        elif race == "generation":
            document = json.loads(h.registry.read_text())
            next(item for item in document["routes"].values() if item["handle"] == h.selector.route_handle)["generation"] = 2
            h.registry.write_text(json.dumps(document), encoding="utf-8")
        else:
            values = {"url": dict(chat_url=h.binding.chat_url + "/"),
                      "endpoint": dict(cdp_endpoint="http://127.0.0.1:9333"),
                      "state-path": dict(state_path=h.binding.state_path.with_name("moved.json")),
                      "route-handle": dict(route_handle="page-origin-v1:" + "b" * 64)}
            h.moved = replace(h.binding, **values[race])
    h.hook = changed
    receipt = h.deliver()
    assert receipt["reason"] in {"CANONICALLY_RESOLVED", "CANONICAL_UNKNOWN", "AFFINITY_UNPROVEN", "BINDING_GENERATION_CHANGED"}
    assert h.created == 1 and not h.page.evaluations
    assert h.stored()["events"][EVENT]["status"] == ("RESOLVED_NOOP" if race == "resolved" else "DEFERRED")


@pytest.mark.parametrize("guard,reason", [("draft", "DRAFT_PRESENT"), ("generation", "GENERATION_ACTIVE"),
                                         ("login", "SURFACE_UNPROVEN"), ("nonregular", "SURFACE_UNPROVEN")])
def test_acquired_page_retains_human_and_surface_guards(harness, guard, reason):
    h = harness
    def prepare(stage):
        if stage != "created": return
        if guard == "draft": h.page.draft = "Human draft bytes"
        else: h.page.counts[{"generation": wake.STOP, "login": wake.LOGIN, "nonregular": wake.NONREGULAR}[guard]] = 1
    h.hook = prepare
    assert h.deliver()["reason"] == reason
    assert not h.page.evaluations
    if guard == "draft": assert h.page.draft == "Human draft bytes"


@pytest.mark.parametrize("race", ["resolved", "unknown", "binding"])
def test_pre_click_barrier_still_blocks_races_after_successful_insertion(harness, race):
    h = harness
    def changed(stage):
        if stage != "inserted": return
        if race == "binding": h.moved = replace(h.binding, generation=2)
        else: h.projection.freshness = "RESOLVED" if race == "resolved" else "UNKNOWN"
    h.hook = changed
    assert h.deliver()["reason"] in {"CANONICALLY_RESOLVED", "CANONICAL_UNKNOWN", "BINDING_GENERATION_CHANGED"}
    assert wake.INSERT in h.page.evaluations and wake.CLICK not in h.page.evaluations
    assert h.page.draft == wake.doorbell(EVENT, wake.REPOSITORY)


def test_ambiguous_submission_remains_bound_and_never_acquires_or_resends(harness):
    h = harness
    h.ambiguous = True
    assert h.deliver()["reason"] == "SUBMISSION_UNPROVEN"
    page = h.page
    assert h.stored()["events"][EVENT]["status"] == "AMBIGUOUS"
    assert page.evaluations.count(wake.CLICK) == 1
    assert h.deliver()["reason"] == "ATTEMPT_REQUIRES_HUMAN"
    h.browser.contexts[0].pages.clear()
    assert h.deliver()["reason"] == "ATTEMPT_REQUIRES_HUMAN"
    assert h.created == 1 and h.navigations == [h.binding.chat_url]
    assert not h.launches and page.evaluations.count(wake.CLICK) == 1
    assert h.stored()["events"][EVENT]["generation"] == h.binding.generation


def test_one_lane_pass_can_acquire_and_submit_only_once(harness):
    h = harness
    state = wake.RouteState(h.binding.state_path, wake.REPOSITORY, h.binding.route_handle)
    second = EVENT.replace("001", "002")
    with state.locked() as data:
        for event in (EVENT, second): data["events"][event] = wake.record()
        state.write(data)
    receipts = wake.operate(wake.REPOSITORY, h.binding, projection=h.projection, binding_provider=h.provider)
    assert [item["reason"] for item in receipts] == ["EXACT_USER_TURN_PROVEN", "INVOCATION_LIMIT_REACHED"]
    assert h.created == 1 and h.page.evaluations.count(wake.CLICK) == 1


def test_failed_acquisition_cannot_repeat_for_another_pending_event(harness, monkeypatch):
    h = harness
    monkeypatch.delenv(unattended.CONFIG_ENV)
    state = wake.RouteState(h.binding.state_path, wake.REPOSITORY, h.binding.route_handle)
    with state.locked() as data:
        for event in (EVENT, EVENT.replace("001", "002")): data["events"][event] = wake.record()
        state.write(data)
    receipts = wake.operate(wake.REPOSITORY, h.binding, projection=h.projection, binding_provider=h.provider)
    assert [item["reason"] for item in receipts] == ["ACQUISITION_CONFIG_INVALID", "ACQUISITION_LIMIT_REACHED"]
    assert not h.created and not h.launches


def test_finite_rechecks_share_budget_and_stop_after_an_acquisition_attempt(harness, monkeypatch):
    h = harness
    state = wake.RouteState(h.binding.state_path, wake.REPOSITORY, h.binding.route_handle)
    wake._admit_inbox(state, EVENT)
    monkeypatch.delenv(unattended.CONFIG_ENV)
    monkeypatch.setattr(wake.CanonicalFreshness, "observe", lambda self, event: "UNRESOLVED")
    monkeypatch.setattr(wake.CanonicalFreshness, "affinity", lambda self, event: h.selector)
    receipts = wake.recheck_lane(wake.REPOSITORY, h.selector, rechecks=8)
    assert len(receipts) == 1 and receipts[0]["reason"] == "ACQUISITION_CONFIG_INVALID"
    assert h.connects == [10000] and not h.sleeps


def test_configuration_rejects_worktrees_and_bare_stores(harness, tmp_path, monkeypatch):
    h = harness
    for marker in ("working", "bare"):
        root = tmp_path / marker
        root.mkdir()
        if marker == "working": (root / ".git").mkdir()
        else:
            (root / "HEAD").touch()
            (root / "config").touch()
            (root / "objects").mkdir()
        config = root / "acquisition.json"
        config.write_text(json.dumps(h.document), encoding="utf-8")
        monkeypatch.setenv(unattended.CONFIG_ENV, str(config))
        with pytest.raises(wake.WakeBlocked, match="CONFIG_OR_STATE_IN_REPOSITORY"):
            unattended.load_environment(h.binding)


@pytest.mark.parametrize("outcome", ["timeout", "refusal", "would-block", "error", "accepting-unusable-cdp"])
@pytest.mark.parametrize("observation", ["zero", "occupied", "query-failure", "malformed"])
def test_listener_table_alone_controls_launch_after_a_connection_failure(harness, monkeypatch, outcome, observation):
    h = harness
    h.available = False
    h.connect_error = {"timeout": TimeoutError("private TCP timeout"),
                       "refusal": ConnectionRefusedError(errno.ECONNREFUSED, "private TCP refusal"),
                       "would-block": OSError(10035, "private socket would-block"),
                       "error": OSError("private connection uncertainty"),
                       "accepting-unusable-cdp": RuntimeError("private CDP failure on an accepting socket")}[outcome]
    # Even explicit refusal is not consulted as launch permission. These socket
    # outcomes cannot bypass an occupied, failed or malformed OS observation.
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("Socket proof consulted"))
    if observation == "occupied":
        port = unattended.load_environment(h.binding).port
        h.listener_observation = lambda: [dict(LocalAddress="127.0.0.1", LocalPort=port, OwningProcess=456)]
    if observation == "query-failure":
        def failed_query(): raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        h.listener_observation = failed_query
    if observation == "malformed": h.listener_observation = lambda: None
    receipt = h.deliver()
    if observation == "zero":
        assert receipt["status"] == "SUBMITTED"
        assert len(h.launches) == h.created == 1 and len(h.listener_queries) == 2
        assert h.page.evaluations.count(wake.INSERT) == h.page.evaluations.count(wake.CLICK) == 1
        assert h.deliver()["status"] == "NOOP" and len(h.launches) == 1
    else:
        assert receipt["reason"] == "BROWSER_OWNERSHIP_UNPROVEN"
        assert not h.launches and not h.created and not h.navigations
    assert "private" not in json.dumps(receipt)


@pytest.mark.parametrize("address,count", [("127.0.0.1", 1), ("0.0.0.0", 1), ("::", 1), ("::1", 1), ("127.0.0.1", 2)])
def test_any_listener_on_the_configured_port_blocks_launch(harness, address, count):
    h = harness
    h.available = False
    environment = unattended.load_environment(h.binding)
    h.listener_observation = lambda: [dict(LocalAddress=address, LocalPort=environment.port,
                                         OwningProcess=456)] * count
    assert h.deliver()["reason"] == "BROWSER_OWNERSHIP_UNPROVEN"
    assert len(h.listener_queries) == 1 and not h.launches and not h.created and not h.navigations


@pytest.mark.parametrize("observation", [None, False, 0, "[]", {}, {"listeners": []}, [None], [[]], [{"LocalPort": 1}]])
def test_malformed_or_ambiguous_listener_observations_never_launch(harness, observation):
    h = harness
    h.available = False
    h.listener_observation = lambda: observation
    assert h.deliver()["reason"] == "BROWSER_OWNERSHIP_UNPROVEN"
    assert not h.launches and not h.created and not h.navigations


@pytest.mark.parametrize("output", ["zero", "bom-zero", "exit-failure", "timeout", "os-error", "malformed-json", "multiple-json", "non-array", "oversized"])
def test_windows_listener_query_requires_a_successful_bounded_empty_array(harness, tmp_path, monkeypatch, output):
    environment = unattended.load_environment(harness.binding)
    local = LOCAL_ENVIRONMENT(environment, unattended.time.monotonic() + 20)
    # Exercise the real Windows helper/decoder with inert subprocess output.
    helper = tmp_path / "System32/WindowsPowerShell/v1.0/powershell.exe"
    helper.parent.mkdir(parents=True)
    helper.touch()
    monkeypatch.setattr(unattended, "os", SimpleNamespace(name="nt", environ={"SystemRoot": str(tmp_path)}))
    monkeypatch.setattr(unattended.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    calls = []
    def query(command, **kwargs):
        calls.append(command)
        assert command[:4] == [str(helper), "-NoProfile", "-NonInteractive", "-Command"]
        script = command[4]
        assert "$ErrorActionPreference='Stop'" in script
        assert "Get-NetTCPConnection -ErrorAction Stop | Where-Object" in script
        assert "$_.State -eq 'Listen'" in script
        assert "$_.LocalPort -eq ([int]$env:AIOS_ACQUISITION_PORT)" in script
        assert "-LocalPort" not in script  # Zero matches must not be a suppressed query error.
        assert kwargs["env"]["AIOS_ACQUISITION_PORT"] == str(environment.port)
        assert 0 < kwargs["timeout"] <= 3 and kwargs["capture_output"] is True
        assert kwargs["check"] is False and kwargs["creationflags"] == 0x08000000
        if output == "timeout": raise unattended.subprocess.TimeoutExpired(command, kwargs["timeout"])
        if output == "os-error": raise OSError("private OS query detail")
        raw = {"zero": b"[]", "bom-zero": b"\xef\xbb\xbf[]", "exit-failure": b"[]",
               "malformed-json": b"private invalid output", "multiple-json": b"[]\n[]",
               "non-array": b'{"listeners":[]}', "oversized": b" " * (wake.MAX_BYTES + 1)}[output]
        return SimpleNamespace(returncode=1 if output == "exit-failure" else 0, stdout=raw)
    monkeypatch.setattr(unattended.subprocess, "run", query)
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("Socket proof consulted"))
    if output in {"zero", "bom-zero"}:
        assert local.endpoint_absent() is True
    else:
        with pytest.raises(wake.WakeBlocked, match="^BROWSER_OWNERSHIP_UNPROVEN$"):
            local.endpoint_absent()
    assert len(calls) == 1


def test_listener_appearing_before_the_final_launch_check_blocks_launch(harness):
    h = harness
    h.available = False
    environment = unattended.load_environment(h.binding)
    h.listener_observation = lambda: ([] if len(h.listener_queries) == 1 else
                                     [dict(LocalAddress="127.0.0.1", LocalPort=environment.port, OwningProcess=456)])
    assert h.deliver()["reason"] == "BROWSER_OWNERSHIP_UNPROVEN"
    assert len(h.listener_queries) == 2 and h.checks == ["profile"]
    assert not h.launches and not h.created and not h.navigations


@pytest.mark.parametrize("stage", ["launched", "navigate"])
def test_post_observation_port_owner_race_cannot_acquire_or_edit_the_target(harness, monkeypatch, stage):
    h = harness
    h.available = False
    environment = unattended.load_environment(h.binding)
    owner = dict(ProcessId=h.process.pid, ExecutablePath=str(environment.executable), CommandLine="private-local-only")
    synthetic_query = unattended.LocalEnvironment._query
    def query(local, script):
        if "Win32_Process -Filter" in script:
            h.checks.append("owner")
            assert "-State Listen" in script and "-LocalPort ([int]$env:AIOS_ACQUISITION_PORT)" in script
            assert "$listeners.Count -ne 1" in script and "$listeners[0].LocalAddress -ne '127.0.0.1'" in script
            return owner
        return synthetic_query(local, script)
    # The raced owner even matches every configured field: launched PID identity
    # must still reject it using the actual production ownership method.
    monkeypatch.setattr(unattended.LocalEnvironment, "_query", query)
    monkeypatch.setattr(unattended.LocalEnvironment, "prove_owner", LOCAL_ENVIRONMENT.prove_owner)
    monkeypatch.setattr(unattended, "_windows_arguments", lambda command: environment.command())
    def changed(current):
        if current == stage: owner["ProcessId"] = 456
    h.hook = changed
    receipt = h.deliver()
    assert receipt["status"] == "DEFERRED" and receipt["reason"] == "LAUNCH_UNCERTAIN"
    assert len(h.launches) == 1 and len(h.listener_queries) == 2
    if stage == "launched":
        assert not h.created and not h.navigations
    else:
        assert h.created == 1 and h.navigations == [h.binding.chat_url]
    assert all(not page.evaluations for context in h.browser.contexts for page in context.pages)
    assert h.stored()["events"][EVENT]["status"] == "DEFERRED"
    for sensitive in (owner["CommandLine"], owner["ExecutablePath"], str(owner["ProcessId"])):
        assert sensitive not in json.dumps(receipt)


@pytest.mark.parametrize("drift", ["executable", "profile", "user-data", "port", "address", "duplicate", "pid", "exit"])
def test_os_listener_owner_must_match_every_explicit_environment_field(harness, monkeypatch, drift):
    h = harness
    environment = unattended.load_environment(h.binding)
    local = unattended.LocalEnvironment(environment, unattended.time.monotonic() + 20)
    arguments = environment.command()
    owner = dict(ProcessId=123, ExecutablePath=str(environment.executable), CommandLine="private-local-only")
    if drift == "executable": owner["ExecutablePath"] = str(environment.executable.with_name("other.exe"))
    if drift == "profile": arguments[2] = "--profile-directory=Other"
    if drift == "user-data": arguments[1] += "-other"
    if drift == "port": arguments[4] = "--remote-debugging-port=9333"
    if drift == "address": arguments[3] = "--remote-debugging-address=0.0.0.0"
    if drift == "duplicate": arguments.append(arguments[1])
    if drift == "pid":
        local.process = h.process
        owner["ProcessId"] = 456
    if drift == "exit": local.process = SimpleNamespace(pid=123, poll=lambda: 1)
    monkeypatch.setattr(local, "_query", lambda script: owner)
    monkeypatch.setattr(unattended, "_windows_arguments", lambda command: arguments)
    with pytest.raises(wake.WakeBlocked):
        LOCAL_ENVIRONMENT.prove_owner(local)


def test_single_nondefault_context_is_not_an_authorized_profile_context(harness):
    h = harness
    h.nondefault_contexts = ["private-incognito-identifier"]
    assert h.deliver()["reason"] == "BROWSER_CONTEXT_UNPROVEN"
    assert not h.created and not h.navigations


@pytest.mark.parametrize("shape", ["legacy", "production"])
def test_production_shaped_launch_reaches_existing_presubmit_checks_without_edit_or_send(production_bridge, monkeypatch, shape):
    h = production_bridge
    h.available = False
    if shape == "legacy":
        h.context_response = {"browserContextIds": []}
    reached = []

    def stop_before_edit(adapter, text, **barriers):
        # Delivery has already run its unchanged check; repeat it before this
        # inert stop. No insertion, click, live connection, or Send is possible.
        adapter.check(text)
        reached.append(adapter.page)
        raise wake.WakeBlocked("INSERT_BLOCKED")

    monkeypatch.setattr(unattended.UnattendedBrowserAdapter, "submit", stop_before_edit)
    receipt = h.deliver()
    assert receipt["status"] == "DEFERRED" and receipt["reason"] == "INSERT_BLOCKED"
    assert len(h.launches) == h.created == 1
    assert h.connects == [10000, 1000] and len(h.listener_queries) == 2
    assert h.bridge_calls == ["new_browser_cdp_session", "send", "detach", "new_page"]
    assert h.checks == ["profile", "owner", "owner"]
    assert h.navigations == [h.binding.chat_url] and reached == [h.page]
    assert h.page.url == h.binding.chat_url and not h.page.evaluations
    assert h.page.resolutions == [wake.RESOLVE_USER_TURN, wake.RESOLVE_USER_TURN]
    assert h.stored()["events"][EVENT]["status"] == "DEFERRED"
    assert h.stops == ["disconnect"]
    for sensitive in (h.binding.chat_url, h.binding.cdp_endpoint, str(h.config),
                      h.owner["ExecutablePath"], h.owner["CommandLine"],
                      "opaque-fixture-metadata", "fixture-private-freshness-token"):
        assert sensitive not in json.dumps(receipt)


@pytest.mark.parametrize("response", [
    None, [], {}, {"defaultBrowserContextId": "opaque-fixture-metadata"},
    {"browserContextIds": None}, {"browserContextIds": ""},
    {"browserContextIds": ()}, {"browserContextIds": {}},
    {"browserContextIds": False}, {"browserContextIds": ["opaque-nondefault-context"]},
    {"browserContextIds": [], "unknown": None},
    {"browserContextIds": [], "defaultBrowserContextId": "opaque-fixture-metadata", "unknown": []},
    *[{"browserContextIds": [], "defaultBrowserContextId": value}
      for value in ("", None, False, 1, [], {}, b"opaque-fixture-metadata")],
])
def test_unsupported_context_response_cannot_authorize_creation(production_bridge, response):
    h = production_bridge
    h.context_response = response
    receipt = h.deliver()
    assert receipt["status"] == "DEFERRED" and receipt["reason"] == "BROWSER_CONTEXT_UNPROVEN"
    assert h.bridge_calls == ["new_browser_cdp_session", "send"]
    assert not h.created and not h.navigations and not h.launches
    assert h.stored()["events"][EVENT]["status"] == "DEFERRED"
    assert h.stops == ["disconnect"]
    assert "opaque" not in json.dumps(receipt)


@pytest.mark.parametrize("drift", ["removed", "added", "replaced"])
def test_context_list_must_remain_identical_after_bounded_detach(production_bridge, drift):
    h = production_bridge
    def changed(stage):
        if stage != "detach": return
        if drift == "removed": h.browser.contexts.clear()
        if drift == "added": h.browser.contexts.append(Context(h))
        if drift == "replaced": h.browser.contexts[:] = [Context(h)]
    h.hook = changed
    assert h.deliver()["reason"] == "BROWSER_CONTEXT_UNPROVEN"
    assert h.bridge_calls == ["new_browser_cdp_session", "send", "detach"]
    assert not h.created and not h.navigations


def test_context_snapshot_must_match_before_cdp_proof(production_bridge):
    h = production_bridge
    stale_contexts = [Context(h)]
    local = SimpleNamespace(remaining_ms=lambda: 1000)
    with pytest.raises(wake.WakeBlocked, match="^BROWSER_CONTEXT_UNPROVEN$"):
        unattended._prove_context(h.browser, stale_contexts, local)
    assert not h.bridge_calls and not h.created


@pytest.mark.parametrize("stage", ["new_browser_cdp_session", "send", "detach", "new_page", "new_page_result"])
@pytest.mark.parametrize("failure,reason", [
    (BridgeError, "BROWSER_CONTEXT_UNPROVEN"),
    (RuntimeError, "BROWSER_CONTEXT_UNPROVEN"),
    (OSError, "BROWSER_CONTEXT_UNPROVEN"),
    (TypeError, "BROWSER_CONTEXT_UNPROVEN"),
    (AttributeError, "BROWSER_CONTEXT_UNPROVEN"),
    (ValueError, "BROWSER_CONTEXT_UNPROVEN"),
    (asyncio.CancelledError, "BROWSER_CONTEXT_UNPROVEN"),
    (TimeoutError, "ACQUISITION_TIMED_OUT"),
    (BridgeTimeout, "ACQUISITION_TIMED_OUT"),
])
def test_operational_bridge_failures_stay_deterministic_without_retry(production_bridge, stage, failure, reason):
    h = production_bridge
    h.bridge_failures[stage] = failure("private-fixture-bridge-detail")
    state = wake.RouteState(h.binding.state_path, wake.REPOSITORY, h.binding.route_handle)
    with state.locked() as data:
        for event in (EVENT, EVENT.replace("001", "002")):
            data["events"][event] = wake.record()
        state.write(data)
    receipts = wake.operate(wake.REPOSITORY, h.binding, projection=h.projection, binding_provider=h.provider)
    receipt = receipts[0]
    assert receipt["status"] == "DEFERRED" and receipt["reason"] == reason
    assert [item["reason"] for item in receipts] == [reason, "ACQUISITION_LIMIT_REACHED"]
    assert h.created == (1 if stage == "new_page_result" else 0)
    assert h.bridge_calls.count("new_page") <= 1 and not h.navigations
    assert not h.sleeps and not h.launches
    assert h.stored()["events"][EVENT]["status"] == "DEFERRED"
    assert h.stops == ["disconnect"] and "private" not in json.dumps(receipt)
    assert not (unattended.load_environment(h.binding).user_data_dir / ".aios-unattended-acquisition.lock").exists()


@pytest.mark.parametrize("stage", ["created", "navigate"])
@pytest.mark.parametrize("failure,reason", [(BridgeError, "BROWSER_CONTEXT_UNPROVEN"),
                                          (BridgeTimeout, "ACQUISITION_TIMED_OUT")])
def test_page_bridge_failures_cannot_expose_a_page_for_editing(production_bridge, stage, failure, reason):
    h = production_bridge
    def failed(current):
        if current == stage: raise failure("private-fixture-page-detail")
    h.hook = failed
    assert h.deliver()["reason"] == reason
    assert h.created == 1 and h.bridge_calls.count("new_page") == 1
    assert len(h.navigations) == (1 if stage == "navigate" else 0)
    assert not h.page.evaluations


def test_mapping_failure_after_page_creation_is_uncertainty_without_a_second_request(production_bridge, monkeypatch):
    from playwright._impl._sync_base import mapping
    h = production_bridge
    original = mapping.from_maybe_impl
    def failed(result):
        if h.page is not None and result is h.page:
            raise RuntimeError("private-fixture-mapping-detail")
        return original(result)
    monkeypatch.setattr(mapping, "from_maybe_impl", failed)
    assert h.deliver()["reason"] == "BROWSER_CONTEXT_UNPROVEN"
    assert h.created == h.bridge_calls.count("new_page") == 1
    assert not h.navigations and not h.page.evaluations


def test_created_page_must_belong_to_the_proved_context(production_bridge):
    h = production_bridge
    def changed(stage):
        if stage == "created": h.page.context = Context(h)
    h.hook = changed
    assert h.deliver()["reason"] == "TARGET_PAGE_CHANGED"
    assert h.created == h.bridge_calls.count("new_page") == 1
    assert not h.navigations and not h.page.evaluations


def test_failed_disconnect_does_not_mask_the_fixed_acquisition_reason(production_bridge):
    h = production_bridge
    h.bridge_failures["detach"] = BridgeError("private-fixture-session-detail")
    def failed_stop():
        h.stops.append("disconnect")
        raise BridgeError("private-fixture-disconnect-detail")
    h.driver.stop = failed_stop
    assert h.deliver()["reason"] == "BROWSER_CONTEXT_UNPROVEN"
    assert h.stops == ["disconnect"] and not h.created and not h.navigations


@pytest.mark.parametrize("stage", ["launched", "navigate"])
def test_production_context_shape_keeps_exact_postlaunch_owner_races_closed(production_bridge, stage):
    h = production_bridge
    h.available = False
    def changed(current):
        if current == stage: h.owner["ProcessId"] = 456
    h.hook = changed
    assert h.deliver()["reason"] == "LAUNCH_UNCERTAIN"
    assert len(h.launches) == 1 and len(h.listener_queries) == 2
    assert h.created == (1 if stage == "navigate" else 0)
    assert all(not page.evaluations for context in h.browser.contexts for page in context.pages)


@pytest.mark.parametrize("stage", ["detach", "created", "navigate", "second-owner"])
@pytest.mark.parametrize("drift", ["exact-target", "other-page", "context"])
def test_production_bridge_rejects_page_and_context_races_before_editing(production_bridge, stage, drift):
    h = production_bridge
    def changed(current):
        if stage == "second-owner":
            if current != "owner" or h.checks.count("owner") != 2: return
        elif current != stage: return
        if drift == "context": h.browser.contexts.append(Context(h))
        elif drift == "exact-target": existing_page(h)
        else: h.browser.contexts[0].pages.append(AcquiredPage(h, "about:blank"))
    h.hook = changed
    assert h.deliver()["reason"] in {"BROWSER_CONTEXT_UNPROVEN", "TARGET_PAGE_CHANGED", "TARGET_PAGE_NOT_UNIQUE"}
    assert h.created == (0 if stage == "detach" else 1)
    assert h.bridge_calls.count("new_page") <= 1 and len(h.navigations) <= 1
    assert all(not page.evaluations for context in h.browser.contexts for page in context.pages)


def test_page_creation_bridge_times_out_once_without_an_unbounded_fallback():
    calls = []
    async def never_ready():
        calls.append("one creation request")
        await asyncio.sleep(60)
    api = SimpleNamespace(_impl_obj=SimpleNamespace(new_page=never_ready), _sync=asyncio.run)
    with pytest.raises(wake.WakeBlocked, match="ACQUISITION_TIMED_OUT"):
        BOUNDED_CALL(api, "new_page", SimpleNamespace(remaining_ms=lambda: 1))
    assert calls == ["one creation request"]


def test_unsupported_page_creation_bridge_fails_closed():
    with pytest.raises(wake.WakeBlocked, match="BROWSER_CONTEXT_UNPROVEN"):
        BOUNDED_CALL(SimpleNamespace(), "new_page", SimpleNamespace(remaining_ms=lambda: 1000))


def test_valid_os_listener_owner_uses_the_exact_configured_environment(harness, monkeypatch):
    h = harness
    environment = unattended.load_environment(h.binding)
    local = unattended.LocalEnvironment(environment, unattended.time.monotonic() + 20)
    local.process = h.process
    owner = dict(ProcessId=123, ExecutablePath=str(environment.executable), CommandLine="private-local-only")
    monkeypatch.setattr(local, "_query", lambda script: owner)
    monkeypatch.setattr(unattended, "_windows_arguments", lambda command: environment.command())
    LOCAL_ENVIRONMENT.prove_owner(local)


@pytest.fixture
def process_rows(harness, monkeypatch):
    """Inert OS rows; use the native command-line parser on production Windows."""
    environment = unattended.load_environment(harness.binding)
    parsed = {}
    if unattended.os.name != "nt":
        monkeypatch.setattr(unattended, "_windows_arguments", lambda command: parsed[command])

    def add(arguments):
        command = unattended.subprocess.list2cmdline(arguments)
        parsed[command] = arguments
        row = dict(ExecutablePath=str(environment.executable), CommandLine=command)
        harness.processes.append(row)
        return row
    return add


@pytest.mark.parametrize("occupancy", ["no-switch", "other-directory", "production-pair",
                                     "production-pair-reversed", "configured-directory",
                                     "configured-after-no-switch", "configured-after-other"])
def test_dedicated_directory_occupancy_uses_only_explicit_claims(harness, process_rows, occupancy):
    h = harness
    h.available = False
    environment = unattended.load_environment(h.binding)
    executable = str(environment.executable)
    other = [executable, "--user-data-dir=" + str(environment.user_data_dir.with_name("other-user-data"))]
    no_switch = [executable, "--no-first-run"]
    configured = [executable, "--user-data-dir=" + str(environment.user_data_dir)]
    observations = {
        "no-switch": [no_switch], "other-directory": [other],
        "production-pair": [no_switch, other], "production-pair-reversed": [other, no_switch],
        "configured-directory": [configured], "configured-after-no-switch": [no_switch, configured],
        "configured-after-other": [other, configured],
    }
    for arguments in observations[occupancy]: process_rows(arguments)
    # SyntheticLocal inherits the real production profile_available classifier.
    receipt = h.deliver()
    if occupancy.startswith("configured"):
        assert receipt["reason"] == "PROFILE_LOCKED"
        assert len(h.listener_queries) == 1 and h.checks == ["profile"]
        assert not h.launches and not h.created and not h.navigations
    else:
        assert receipt["status"] == "SUBMITTED"
        assert len(h.launches) == h.created == 1 and h.navigations == [h.binding.chat_url]
        assert len(h.listener_queries) == 2 and h.checks == ["profile", "owner", "owner"]
        assert h.page.evaluations.count(wake.INSERT) == h.page.evaluations.count(wake.CLICK) == 1
    for row in h.processes:
        for sensitive in row.values(): assert sensitive not in json.dumps(receipt)


@pytest.mark.parametrize("defect", ["split", "duplicate", "empty", "relative", "malformed-token",
                                  "extra-malformed-token", "parse-failure", "empty-executable-argument"])
def test_explicit_or_unparseable_process_claims_fail_closed(harness, process_rows, monkeypatch, defect):
    h = harness
    h.available = False
    environment = unattended.load_environment(h.binding)
    executable, directory = str(environment.executable), str(environment.user_data_dir)
    explicit = "--user-data-dir=" + directory
    arguments = {
        "split": [executable, "--user-data-dir", directory],
        "duplicate": [executable, explicit, explicit],
        "empty": [executable, "--user-data-dir="],
        "relative": [executable, "--user-data-dir=relative-profile"],
        "malformed-token": [executable, "--user-data-directory=" + directory],
        "extra-malformed-token": [executable, explicit, "--user-data-dir-other=" + directory],
        "parse-failure": [executable], "empty-executable-argument": [""],
    }[defect]
    process_rows(arguments)
    if defect == "parse-failure":
        def failed_parse(command):
            raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        monkeypatch.setattr(unattended, "_windows_arguments", failed_parse)
    assert h.deliver()["reason"] == "BROWSER_OWNERSHIP_UNPROVEN"
    assert len(h.listener_queries) == 1
    assert not h.launches and not h.created and not h.navigations


@pytest.mark.parametrize("defect", ["missing-executable", "unreadable-executable", "relative-executable",
                                  "invalid-executable", "missing-commandline", "unreadable-commandline",
                                  "empty-commandline", "blank-commandline", "invalid-commandline"])
def test_required_process_metadata_cannot_be_replaced_by_no_switch_permission(harness, process_rows, defect):
    h = harness
    h.available = False
    environment = unattended.load_environment(h.binding)
    row = process_rows([str(environment.executable)])
    if defect == "missing-executable": row.pop("ExecutablePath")
    if defect == "unreadable-executable": row["ExecutablePath"] = None
    if defect == "relative-executable": row["ExecutablePath"] = "browser.exe"
    if defect == "invalid-executable": row["ExecutablePath"] += "\0"
    if defect == "missing-commandline": row.pop("CommandLine")
    if defect == "unreadable-commandline": row["CommandLine"] = None
    if defect == "empty-commandline": row["CommandLine"] = ""
    if defect == "blank-commandline": row["CommandLine"] = "   "
    if defect == "invalid-commandline": row["CommandLine"] += "\0--user-data-dir=hidden"
    assert h.deliver()["reason"] == "BROWSER_OWNERSHIP_UNPROVEN"
    assert len(h.listener_queries) == 1 and not h.launches and not h.created and not h.navigations


@pytest.mark.parametrize("observation", [None, False, 0, "[]", {}, [None], [[]], [{}],
                                       [{"ExecutablePath": 1, "CommandLine": 2}]])
def test_invalid_process_observations_cannot_authorize_launch(harness, observation):
    h = harness
    h.available = False
    h.processes = observation
    assert h.deliver()["reason"] == "BROWSER_OWNERSHIP_UNPROVEN"
    assert len(h.listener_queries) == 1 and not h.launches and not h.created and not h.navigations


def test_excessive_readable_no_switch_process_observations_cannot_authorize_launch(harness, process_rows):
    h = harness
    h.available = False
    row = process_rows([str(unattended.load_environment(h.binding).executable)])
    h.processes = [row] * 257
    assert h.deliver()["reason"] == "BROWSER_OWNERSHIP_UNPROVEN"
    assert len(h.listener_queries) == 1 and not h.launches and not h.created and not h.navigations


def test_exact_target_appearing_during_missing_page_acquisition_is_not_substituted(harness):
    h = harness
    def appeared(stage):
        if stage == "owner" and not h.browser.contexts[0].pages: existing_page(h)
    h.hook = appeared
    assert h.deliver()["reason"] == "TARGET_PAGE_CHANGED"
    assert not h.created and not h.navigations and not h.page.evaluations


def test_existing_exact_attach_keeps_the_original_page_enumeration_behavior(harness):
    h = harness
    page = existing_page(h)
    for _ in range(40): h.browser.contexts[0].pages.append(AcquiredPage(h, "about:blank"))
    assert h.deliver()["status"] == "SUBMITTED"
    assert not h.created and not h.checks and page.evaluations.count(wake.CLICK) == 1
