"""Bounded, attach-only delivery of one local user doorbell.

AIOS_LOCAL_CHAT_WAKE_CONFIG names a machine-local JSON file outside Git with
exactly chat_url, cdp_endpoint and state_path fields. The Human owns that binding
and the workflow's enable variable. No browser configuration is a workflow input.
Operational state is bounded and never evicted: uncertain attempts, stale locks
and exhausted capacity require Human intervention, rather than automatic resend.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
from typing import Iterator
from urllib.parse import urlsplit

REPOSITORY = "trung-via/AIOS-renew"
EVENT_PATTERN = re.compile(r"terminal:(RESULT|FAILURE):RUN-[A-Za-z0-9][A-Za-z0-9._-]{0,95}:[0-9a-f]{40}")
CHAT_PATH = re.compile(
    r"(?:/g/g-[A-Za-z0-9-]*)?/c/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/?"
)
MAX_EVENTS = 256
MAX_BYTES = 65536
COMPOSER = '#prompt-textarea[contenteditable="true"]'
ACCOUNT = '[data-testid="profile-button"], [data-testid="accounts-profile-button"]'
SEND = '[data-testid="send-button"]'
STOP = '[data-testid="stop-button"]'
NONREGULAR = '[data-workspace-type="team"], [data-workspace-type="enterprise"], [data-workspace-type="business"], [data-testid="work-composer"]'
LOGIN = '[data-testid="login-button"], a[href^="/auth/login"]'
USER_TURN = '[data-message-author-role="user"]'
REASONS = frozenset({
    "INVALID_INPUT", "INVALID_CHAT_BINDING", "INVALID_LOCAL_PATH",
    "CONFIG_OR_STATE_IN_REPOSITORY", "LOCAL_METADATA_INVALID", "BINDING_MISSING",
    "BINDING_MALFORMED", "INVALID_LOCAL_ENDPOINT", "STATE_LOCKED_OR_UNAVAILABLE",
    "STATE_AMBIGUOUS", "STATE_WRITE_UNCERTAIN", "TARGET_PAGE_NOT_UNIQUE",
    "TARGET_PAGE_CHANGED", "SURFACE_UNPROVEN", "GENERATION_ACTIVE", "DRAFT_PRESENT",
    "OUTBOUND_ALREADY_PRESENT", "INSERT_BLOCKED", "SEND_BLOCKED",
    "SUBMISSION_UNPROVEN", "ATTEMPT_REQUIRES_HUMAN", "STATE_CAPACITY_REQUIRES_HUMAN",
    "LOCAL_FAILURE",
})


class WakeBlocked(Exception):
    """Only fixed reason codes may leave the local browser boundary."""

    def __init__(self, reason: str):
        super().__init__(reason if reason in REASONS else "LOCAL_FAILURE")


def doorbell(event_id: str, repository: str) -> str:
    if (repository != REPOSITORY or not isinstance(event_id, str)
            or not EVENT_PATTERN.fullmatch(event_id)):
        raise WakeBlocked("INVALID_INPUT")
    return (f"[AIOS LOCAL CHAT WAKE]\nevent_id: {event_id}\n"
            f"repository: {REPOSITORY}\nfresh_brain_sync_required: true")


def normalize_chat(url: str) -> str:
    try:
        if not isinstance(url, str):
            raise ValueError
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.netloc != "chatgpt.com"
                or parts.query or parts.fragment or not CHAT_PATH.fullmatch(parts.path)
                or url != "https://chatgpt.com" + parts.path):
            raise ValueError
        return "https://chatgpt.com" + parts.path.rstrip("/")
    except (ValueError, TypeError):
        raise WakeBlocked("INVALID_CHAT_BINDING") from None


def external_path(value: str) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise WakeBlocked("INVALID_LOCAL_PATH")
    path = Path(value).resolve()
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise WakeBlocked("CONFIG_OR_STATE_IN_REPOSITORY")
    return path


def read_json(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError
        result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (OSError, ValueError, UnicodeError):
        raise WakeBlocked("LOCAL_METADATA_INVALID") from None


@dataclass(frozen=True)
class Binding:
    chat_url: str
    cdp_endpoint: str
    state_path: Path


def load_binding() -> Binding:
    location = os.environ.get("AIOS_LOCAL_CHAT_WAKE_CONFIG")
    if not location:
        raise WakeBlocked("BINDING_MISSING")
    config_path = external_path(location)
    data = read_json(config_path)
    if set(data) != {"chat_url", "cdp_endpoint", "state_path"}:
        raise WakeBlocked("BINDING_MALFORMED")
    chat_url = normalize_chat(data["chat_url"])
    try:
        endpoint = data["cdp_endpoint"]
        if not isinstance(endpoint, str) or any(c.isspace() for c in endpoint):
            raise ValueError
        parts = urlsplit(endpoint)
        host = parts.hostname
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        if (parts.scheme != "http" or not loopback or not parts.port
                or parts.username or parts.password or parts.path not in ("", "/")
                or parts.query or parts.fragment):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise WakeBlocked("INVALID_LOCAL_ENDPOINT") from None
    state_path = external_path(data["state_path"])
    if state_path == config_path:
        raise WakeBlocked("INVALID_LOCAL_PATH")
    return Binding(chat_url, endpoint, state_path)


class State:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def locked(self) -> Iterator[dict]:
        # O_EXCL blocks concurrent processes and stale/uncertain locks.
        lock = self.path.with_name(self.path.name + ".lock")
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError:
            raise WakeBlocked("STATE_LOCKED_OR_UNAVAILABLE") from None
        try:
            os.close(fd)
            data = read_json(self.path) if self.path.exists() else {"version": 1, "events": {}}
            if (set(data) != {"version", "events"} or type(data["version"]) is not int
                    or data["version"] != 1 or not isinstance(data["events"], dict)
                    or len(data["events"]) > MAX_EVENTS
                    or any(not EVENT_PATTERN.fullmatch(key) or value not in ("ATTEMPTING", "SUBMITTED")
                           for key, value in data["events"].items())):
                raise WakeBlocked("STATE_AMBIGUOUS")
            if self.path.with_name(self.path.name + ".pending").exists():
                raise WakeBlocked("STATE_WRITE_UNCERTAIN")
            yield data
        finally:
            lock.unlink()

    def write(self, data: dict) -> None:
        temp = self.path.with_name(self.path.name + ".pending")
        try:
            # A left-over pending write requires Human intervention.
            with temp.open("x", encoding="utf-8") as stream:
                json.dump(data, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        except OSError:
            raise WakeBlocked("STATE_WRITE_UNCERTAIN") from None


# Inspection and insertion share one browser event-loop turn, so a delayed
# fill() cannot overwrite a draft typed after preflight. Focus handlers are
# synchronous; recheck the empty composer after focus before inserting.
INSERT = """({url, text, composer, account, stop, nonregular, login, send}) => {
  const visible = selector => [...document.querySelectorAll(selector)].filter(
    e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
  const boxes = visible(composer);
  if (location.href.replace(/\\/$/, '') !== url || boxes.length !== 1 ||
      visible(account).length !== 1 || visible(stop).length ||
      visible(nonregular).length || visible(login).length ||
      visible('main').length !== 1) return false;
  const box = boxes[0];
  const empty = () => box.textContent === '' && box.getAttribute('aria-disabled') !== 'true';
  if (!empty()) return false;
  box.focus();
  if (!empty() || location.href.replace(/\\/$/, '') !== url ||
      visible(stop).length || visible(login).length || visible(nonregular).length ||
      visible(account).length !== 1 || visible(composer)[0] !== box) return false;
  if (!document.execCommand('insertText', false, text)) return false;
  return box.innerText.replace(/\\r\\n/g, '\\n').replace(/\\n+$/, '') === text;
}"""

CLICK = """({url, text, composer, account, stop, nonregular, login, send}) => {
  const visible = selector => [...document.querySelectorAll(selector)].filter(
    e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
  const boxes = visible(composer), buttons = visible(send);
  if (location.href.replace(/\\/$/, '') !== url || boxes.length !== 1 ||
      boxes[0].innerText.replace(/\\r\\n/g, '\\n').replace(/\\n+$/, '') !== text ||
      boxes[0].getAttribute('aria-disabled') === 'true' ||
      visible(account).length !== 1 || visible(stop).length ||
      visible(nonregular).length || visible(login).length ||
      visible('main').length !== 1 || buttons.length !== 1 || buttons[0].disabled ||
      buttons[0].getAttribute('aria-disabled') === 'true') return false;
  buttons[0].click();
  return true;
}"""


class BrowserAdapter:
    """Attach only; never create pages, navigate, launch, or close a browser."""

    def __init__(self, binding: Binding):
        self.binding = binding
        self.driver = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self.driver = sync_playwright().start()
        try:
            self.browser = self.driver.chromium.connect_over_cdp(
                self.binding.cdp_endpoint, timeout=10000)
            self.page = self.select_page(self.browser, self.binding.chat_url)
            self.page.set_default_timeout(3000)
            return self
        except Exception:
            self.driver.stop()
            raise

    def __exit__(self, *args):
        # Disconnect the client transport; preserve the Human's browser.
        self.driver.stop()

    @staticmethod
    def select_page(browser, url):
        pages = []
        for context in browser.contexts:
            for page in context.pages:
                try:
                    if normalize_chat(page.url) == url:
                        pages.append(page)
                except WakeBlocked:
                    pass
        if len(pages) != 1:
            raise WakeBlocked("TARGET_PAGE_NOT_UNIQUE")
        return pages[0]

    def visible(self, selector):
        return self.page.locator(selector).filter(visible=True)

    def arguments(self, text):
        return dict(url=self.binding.chat_url, text=text, composer=COMPOSER,
                    account=ACCOUNT, stop=STOP, nonregular=NONREGULAR, login=LOGIN, send=SEND)

    def user_turn(self, text):
        # Compare only the exact outbound user doorbell, never assistant content.
        return self.page.locator(USER_TURN).get_by_text(text, exact=True)

    def check(self, text):
        if self.select_page(self.browser, self.binding.chat_url) is not self.page:
            raise WakeBlocked("TARGET_PAGE_CHANGED")
        if (normalize_chat(self.page.url) != self.binding.chat_url
                or self.visible("main").count() != 1 or self.visible(ACCOUNT).count() != 1
                or self.visible(LOGIN).count() or self.visible(NONREGULAR).count()
                or self.visible(COMPOSER).count() != 1
                or self.visible(COMPOSER).get_attribute("aria-disabled") == "true"):
            raise WakeBlocked("SURFACE_UNPROVEN")
        if self.visible(STOP).count():
            raise WakeBlocked("GENERATION_ACTIVE")
        if self.visible(COMPOSER).text_content() != "":
            raise WakeBlocked("DRAFT_PRESENT")
        if self.user_turn(text).count():
            raise WakeBlocked("OUTBOUND_ALREADY_PRESENT")

    def submit(self, text):
        self.check(text)
        if not self.page.evaluate(INSERT, self.arguments(text)):
            raise WakeBlocked("INSERT_BLOCKED")
        # Await only the send control, never response content.
        self.visible(SEND).wait_for(state="visible", timeout=3000)
        if self.select_page(self.browser, self.binding.chat_url) is not self.page:
            raise WakeBlocked("TARGET_PAGE_CHANGED")
        if not self.page.evaluate(CLICK, self.arguments(text)):
            raise WakeBlocked("SEND_BLOCKED")

    def prove(self, text):
        self.user_turn(text).wait_for(state="visible", timeout=5000)
        if (self.select_page(self.browser, self.binding.chat_url) is not self.page
                or self.user_turn(text).count() != 1 or self.visible(COMPOSER).count() != 1
                or self.user_turn(text).text_content().replace("\r\n", "\n") != text
                or self.visible("main").count() != 1 or self.visible(ACCOUNT).count() != 1
                or self.visible(LOGIN).count() or self.visible(NONREGULAR).count()
                or self.visible(COMPOSER).text_content() != ""):
            raise WakeBlocked("SUBMISSION_UNPROVEN")
        buttons = self.visible(SEND)
        if buttons.count() > 1 or (buttons.count() == 1 and buttons.is_enabled()):
            raise WakeBlocked("SUBMISSION_UNPROVEN")


def deliver(event_id: str, repository: str, binding: Binding, adapter_factory=BrowserAdapter) -> dict:
    text = doorbell(event_id, repository)
    receipt = {"event_id": event_id, "status": "BLOCKED", "reason": "LOCAL_FAILURE"}
    try:
        state = State(external_path(str(binding.state_path)))
        with state.locked() as data:
            previous = data["events"].get(event_id)
            if previous == "SUBMITTED":
                return dict(receipt, status="NOOP", reason="ALREADY_SUBMITTED")
            if previous is not None:
                raise WakeBlocked("ATTEMPT_REQUIRES_HUMAN")
            if len(data["events"]) >= MAX_EVENTS:
                raise WakeBlocked("STATE_CAPACITY_REQUIRES_HUMAN")
            with adapter_factory(binding) as adapter:
                adapter.check(text)
                data["events"][event_id] = "ATTEMPTING"
                state.write(data)
                adapter.submit(text)
                adapter.prove(text)
                data["events"][event_id] = "SUBMITTED"
                state.write(data)
            return dict(receipt, status="SUBMITTED", reason="EXACT_USER_TURN_PROVEN")
    except WakeBlocked as exc:
        return dict(receipt, reason=str(exc))
    except Exception:
        # Dependency exceptions may contain private session or browser material.
        return receipt


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # Do not echo malformed arguments into an operational log.
        print(json.dumps({"status": "BLOCKED", "reason": "INVALID_INPUT"}))
        raise SystemExit(1)


def main(argv=None) -> int:
    parser = _Parser(description="Bounded local user doorbell", allow_abbrev=False)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args(argv)
    try:
        doorbell(args.event_id, args.repository)
    except WakeBlocked:
        print(json.dumps({"status": "BLOCKED", "reason": "INVALID_INPUT"}))
        return 1
    try:
        receipt = deliver(args.event_id, args.repository, load_binding())
    except WakeBlocked as exc:
        receipt = {"event_id": args.event_id, "status": "BLOCKED", "reason": str(exc)}
    except Exception:
        receipt = {"event_id": args.event_id, "status": "BLOCKED", "reason": "LOCAL_FAILURE"}
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] in ("SUBMITTED", "NOOP") else 1


if __name__ == "__main__":
    sys.exit(main())
