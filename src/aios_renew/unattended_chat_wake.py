"""One authorized local acquisition, downstream of exact H4C1 lane resolution.

No entry point, router, queue, retry service, browser/account discovery or live
proof. Sensitive configuration and OS observations stay inside this boundary.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit

from . import local_chat_wake as wake

CONFIG_ENV = "AIOS_UNATTENDED_CHAT_WAKE_CONFIG"
ACQUISITION_SECONDS = 20
MAX_ENVIRONMENTS = 8
PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,63}")


@dataclass(frozen=True)
class Environment:
    endpoint: str
    executable: Path
    user_data_dir: Path
    profile_directory: str
    allow_launch: bool
    port: int

    def command(self):
        # No arbitrary switches or URL arguments from configuration. The only
        # conversation navigation is the later, exact Binding.chat_url goto.
        return [str(self.executable),
                "--user-data-dir=" + str(self.user_data_dir),
                "--profile-directory=" + self.profile_directory,
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=" + str(self.port),
                "--enable-automation", "--no-first-run",
                "--no-default-browser-check", "--no-startup-window"]


def _local_path(value):
    from .origin_bootstrap import registry_path, BootstrapBlocked
    if type(value) is not str:
        raise wake.WakeBlocked("ACQUISITION_CONFIG_INVALID")
    original = Path(value)
    if original.drive.startswith("\\\\"):
        raise wake.WakeBlocked("ACQUISITION_CONFIG_INVALID")
    # Check lexical parents too: a repository-owned symlink is not local config.
    for parent in (original, *original.parents):
        if ((parent / ".git").exists() or ((parent / "HEAD").is_file()
                and (parent / "objects").is_dir() and (parent / "config").is_file())):
            raise wake.WakeBlocked("CONFIG_OR_STATE_IN_REPOSITORY")
    try:
        path = registry_path(value)
        if path.drive.startswith("\\\\"):
            raise wake.WakeBlocked("ACQUISITION_CONFIG_INVALID")
        return path
    except BootstrapBlocked as exc:
        raise wake.WakeBlocked(str(exc)) from None


def load_environment(binding):
    """Match an explicit endpoint; never derive configuration from OS/browser."""
    try:
        location = os.environ.get(CONFIG_ENV)
        if not location:
            raise wake.WakeBlocked("ACQUISITION_CONFIG_INVALID")
        path = _local_path(location)
        data = wake.read_json(path)
        if (set(data) != {"version", "environments"}
                or type(data["version"]) is not int or data["version"] != 1
                or type(data["environments"]) is not list
                or not 1 <= len(data["environments"]) <= MAX_ENVIRONMENTS):
            raise ValueError
        environments, endpoints, profiles = [], set(), set()
        for item in data["environments"]:
            if (type(item) is not dict or set(item) != {
                    "cdp_endpoint", "executable", "user_data_dir",
                    "profile_directory", "allow_launch", "exclusive_user_data"}
                    or type(item["allow_launch"]) is not bool
                    or item["exclusive_user_data"] is not True
                    or type(item["profile_directory"]) is not str
                    or not PROFILE.fullmatch(item["profile_directory"])):
                raise ValueError
            endpoint = item["cdp_endpoint"]
            parts = urlsplit(endpoint)
            # The Windows acquisition boundary supports an explicit IPv4
            # loopback listener, never hostname/DNS or wildcard substitution.
            if (type(endpoint) is not str or parts.scheme != "http"
                    or parts.netloc != "127.0.0.1:" + str(parts.port)
                    or not parts.port or parts.path not in ("", "/")
                    or parts.query or parts.fragment):
                raise ValueError
            executable = _local_path(item["executable"])
            directory = _local_path(item["user_data_dir"])
            profile = _local_path(str(directory / item["profile_directory"]))
            if (not executable.is_file() or not directory.is_dir()
                    or not profile.is_dir() or profile.parent != directory
                    or (os.name == "nt" and executable.suffix.lower() != ".exe")
                    or path == executable or directory == path or directory in path.parents
                    or directory == executable or directory in executable.parents):
                raise ValueError
            if endpoint in endpoints or any(
                    directory == other or directory in other.parents or other in directory.parents
                    for other in profiles):
                raise ValueError
            endpoints.add(endpoint)
            profiles.add(directory)
            environments.append(Environment(endpoint, executable, directory,
                                            item["profile_directory"], item["allow_launch"], parts.port))
        matches = [item for item in environments if item.endpoint == binding.cdp_endpoint]
        if len(matches) != 1:
            raise wake.WakeBlocked("ENDPOINT_MISMATCH")
        return matches[0]
    except wake.WakeBlocked:
        raise
    except (OSError, ValueError, TypeError, AttributeError):
        raise wake.WakeBlocked("ACQUISITION_CONFIG_INVALID") from None


def _switch(arguments, name):
    prefix = name + "="
    matches = [arg[len(prefix):] for arg in arguments if arg.startswith(prefix)]
    # Refuse split spelling and duplicates instead of interpreting competing
    # switches differently from Chromium. No defaults or inferred profiles.
    if name in arguments or len(matches) != 1 or not matches[0]:
        raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
    return matches[0]


def _windows_arguments(command):
    import ctypes
    from ctypes import wintypes
    count = ctypes.c_int()
    parse = ctypes.WinDLL("shell32", use_last_error=True).CommandLineToArgvW
    parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    pointer = parse(command, ctypes.byref(count))
    if not pointer:
        raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
    try:
        if not 1 <= count.value <= 256:
            raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        return [pointer[index] for index in range(count.value)]
    finally:
        free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(ctypes.cast(pointer, ctypes.c_void_p))


class LocalEnvironment:
    """Bounded Windows OS checks, for occupancy/ownership only, never selection."""

    def __init__(self, environment, deadline):
        self.environment, self.deadline = environment, deadline
        self.process = None

    def remaining_ms(self, ceiling=3000):
        remaining = int((self.deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise wake.WakeBlocked("ACQUISITION_TIMED_OUT")
        return min(remaining, ceiling)

    @contextmanager
    def locked(self):
        # Different durable chat lanes can share this one environment. Serialize
        # only acquisition so concurrent invocations cannot both launch it.
        path = self.environment.user_data_dir / ".aios-unattended-acquisition.lock"
        self.remaining_ms()
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError:
            raise wake.WakeBlocked("PROFILE_LOCKED") from None
        try:
            yield
        finally:
            os.close(descriptor)
            path.unlink()  # Never remove a preexisting/stale lock.

    def endpoint_absent(self):
        """Only a successful Windows zero-listener observation permits launch."""
        # Enumerate first: a filtered Get-NetTCPConnection query reports no
        # matches as an error. A successful full table can prove an empty set
        # for this exact port, on any local address (including wildcard/IPv6).
        listeners = self._query(
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
            "$listeners=@(Get-NetTCPConnection -ErrorAction Stop | Where-Object { "
            "$_.State -eq 'Listen' -and "
            "$_.LocalPort -eq ([int]$env:AIOS_ACQUISITION_PORT) } | "
            "Select-Object LocalAddress,LocalPort,OwningProcess); "
            "ConvertTo-Json -InputObject $listeners -Compress")
        if type(listeners) is not list:
            raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN") from None
        # No socket outcome is consulted. Only the empty array proves absence;
        # any row, including malformed or ambiguous rows, blocks launch.
        return not listeners

    def _query(self, script):
        if os.name != "nt":
            raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        # A fixed Windows system utility; never locate a browser executable.
        helper = Path(os.environ.get("SystemRoot", "")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        if not helper.is_absolute() or not helper.is_file():
            raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        environment = {**os.environ,
                       "AIOS_ACQUISITION_PORT": str(self.environment.port),
                       "AIOS_ACQUISITION_EXE_NAME": self.environment.executable.name}
        try:
            result = subprocess.run([str(helper), "-NoProfile", "-NonInteractive", "-Command",
                                     "$ErrorActionPreference='Stop'; " + script],
                                    env=environment, capture_output=True,
                                    timeout=self.remaining_ms() / 1000, check=False,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.remaining_ms()
            if result.returncode or len(result.stdout) > wake.MAX_BYTES:
                raise ValueError
            return json.loads(result.stdout.decode("utf-8-sig"))
        except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
            raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN") from None

    def profile_available(self):
        # Chromium singleton markers are never removed, even if apparently stale.
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie", "lockfile", "DevToolsActivePort"):
            if os.path.lexists(self.environment.user_data_dir / name):
                raise wake.WakeBlocked("PROFILE_LOCKED")
        processes = self._query(
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
            "$p=@(Get-CimInstance Win32_Process | Where-Object { "
            "$_.ProcessId -ne $PID -and ($_.Name -eq $env:AIOS_ACQUISITION_EXE_NAME -or "
            "$_.CommandLine -like '*--user-data-dir*') } | "
            "Select-Object ExecutablePath,CommandLine); "
            "ConvertTo-Json -InputObject $p -Compress")
        if type(processes) is not list or len(processes) > 256:
            raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        for process in processes:
            if (type(process) is not dict or type(process.get("ExecutablePath")) is not str
                    or not Path(process["ExecutablePath"]).is_absolute()
                    or "\0" in process["ExecutablePath"]
                    or type(process.get("CommandLine")) is not str
                    or not process["CommandLine"].strip() or "\0" in process["CommandLine"]):
                raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            arguments = _windows_arguments(process["CommandLine"])
            if not arguments[0]:
                raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            if any(arg.startswith("--type=") for arg in arguments):
                continue  # Child process, never the profile-owning main process.
            user_data_arguments = [arg for arg in arguments if "--user-data-dir" in arg]
            if not user_data_arguments:
                # No explicit claim on the dedicated configured directory. This
                # record proves nothing about which implicit profile is in use.
                continue
            if (len(user_data_arguments) != 1
                    or not user_data_arguments[0].startswith("--user-data-dir=")):
                raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            configured_path = Path(_switch(arguments, "--user-data-dir"))
            if not configured_path.is_absolute():
                raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            directory = configured_path.resolve()
            if directory == self.environment.user_data_dir:
                raise wake.WakeBlocked("PROFILE_LOCKED")
        self.remaining_ms()

    def launch(self):
        if not self.environment.allow_launch:
            raise wake.WakeBlocked("LAUNCH_NOT_AUTHORIZED")
        self.profile_available()
        if not self.endpoint_absent():
            raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        self.remaining_ms()
        # Do not pass the workflow's freshness credential into a browser process.
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() not in {"GITHUB_TOKEN", "GH_TOKEN"}
                       and not key.upper().startswith("AIOS_")}
        try:
            self.process = subprocess.Popen(self.environment.command(), env=environment,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, close_fds=True,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError:
            raise wake.WakeBlocked("LAUNCH_UNCERTAIN") from None
        # Never kill/terminate or retry this process, including failed acquisition.

    def prove_owner(self):
        owner = self._query(
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
            "$listeners=@(Get-NetTCPConnection -State Listen "
            "-LocalPort ([int]$env:AIOS_ACQUISITION_PORT)); "
            "if($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne '127.0.0.1'){exit 1}; "
            "$p=Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $listeners[0].OwningProcess); "
            "$p | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress")
        if (type(owner) is not dict or type(owner.get("ProcessId")) is not int
                or type(owner.get("ExecutablePath")) is not str
                or type(owner.get("CommandLine")) is not str):
            raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        arguments = _windows_arguments(owner["CommandLine"])
        expected = self.environment
        try:
            valid = (Path(owner["ExecutablePath"]).is_absolute()
                     and Path(arguments[0]).is_absolute()
                     and Path(_switch(arguments, "--user-data-dir")).is_absolute()
                     and Path(owner["ExecutablePath"]).resolve() == expected.executable
                     and Path(arguments[0]).resolve() == expected.executable
                     and Path(_switch(arguments, "--user-data-dir")).resolve() == expected.user_data_dir
                     and _switch(arguments, "--profile-directory") == expected.profile_directory
                     and _switch(arguments, "--remote-debugging-address") == "127.0.0.1"
                     and _switch(arguments, "--remote-debugging-port") == str(expected.port)
                     and not any(arg.startswith("--type=") for arg in arguments))
        except (OSError, ValueError):
            valid = False
        if not valid:
            raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
        if self.process is not None and (self.process.poll() is not None or owner["ProcessId"] != self.process.pid):
            raise wake.WakeBlocked("LAUNCH_UNCERTAIN")
        self.remaining_ms()


def _bounded_call(api, method, local, *args):
    """Bound API methods whose public sync signatures have no timeout.

    Use the existing Playwright sync loop and mapping, never another thread or
    browser controller. An unsupported bridge fails closed; there is no call to
    the unbounded public method as a fallback. Cancellation is uncertainty, not
    permission to issue a second page-creation request.
    """
    try:
        from playwright._impl._sync_base import mapping
    except ImportError:
        raise wake.WakeBlocked("BROWSER_CONTEXT_UNPROVEN") from None
    implementation = getattr(api, "_impl_obj", None)
    operation = getattr(implementation, method, None)
    sync = getattr(api, "_sync", None)
    if (not callable(operation) or not callable(sync)
            or not callable(getattr(mapping, "from_maybe_impl", None))):
        raise wake.WakeBlocked("BROWSER_CONTEXT_UNPROVEN")
    timeout = local.remaining_ms() / 1000
    try:
        result = sync(asyncio.wait_for(operation(*args), timeout=timeout))
        return mapping.from_maybe_impl(result)
    except asyncio.TimeoutError:
        raise wake.WakeBlocked("ACQUISITION_TIMED_OUT") from None


def _prove_context(browser, contexts, local):
    if len(contexts) != 1:
        raise wake.WakeBlocked("BROWSER_CONTEXT_UNPROVEN")
    session = _bounded_call(browser, "new_browser_cdp_session", local)
    result = _bounded_call(session, "send", local, "Target.getBrowserContexts")
    # CDP lists non-default contexts. Zero proves the sole attached context is
    # the configured profile's default, without choosing an incognito/account.
    if type(result) is not dict or result != {"browserContextIds": []}:
        raise wake.WakeBlocked("BROWSER_CONTEXT_UNPROVEN")
    _bounded_call(session, "detach", local)
    if list(browser.contexts) != contexts:
        raise wake.WakeBlocked("BROWSER_CONTEXT_UNPROVEN")


class UnattendedBrowserAdapter(wake.BrowserAdapter):
    """Keep existing pages; acquire once only under a supplied lane barrier."""

    def __init__(self, binding):
        super().__init__(binding)
        self.before_acquire = None

    def authorize_acquisition(self, barrier):
        self.before_acquire = barrier

    @staticmethod
    def _targets(browser, url, *, bounded=False):
        contexts = list(browser.contexts)
        if bounded and len(contexts) > 8:
            raise wake.WakeBlocked("BROWSER_CONTEXT_UNPROVEN")
        pages = [page for context in contexts for page in context.pages]
        if bounded and len(pages) > 32:
            raise wake.WakeBlocked("TARGET_PAGE_NOT_UNIQUE")
        matches = []
        for page in pages:
            try:
                if wake.normalize_chat(page.url) == url:
                    matches.append(page)
            except wake.WakeBlocked:
                pass
        if len(matches) > 1:
            raise wake.WakeBlocked("TARGET_PAGE_NOT_UNIQUE")
        return contexts, matches

    def _acquire(self, browser, local):
        restoring = browser is None
        if browser is None:
            if not local.endpoint_absent():
                raise wake.WakeBlocked("BROWSER_OWNERSHIP_UNPROVEN")
            local.launch()
            while browser is None:
                if local.process.poll() is not None:
                    raise wake.WakeBlocked("LAUNCH_UNCERTAIN")
                try:
                    browser = self.driver.chromium.connect_over_cdp(
                        self.binding.cdp_endpoint, timeout=local.remaining_ms(1000))
                except Exception:
                    local.remaining_ms()
                    time.sleep(min(0.2, local.remaining_ms() / 1000))
        local.prove_owner()
        contexts, targets = self._targets(browser, self.binding.chat_url, bounded=True)
        _prove_context(browser, contexts, local)
        if targets and not restoring:
            raise wake.WakeBlocked("TARGET_PAGE_CHANGED")
        if not targets:
            context = contexts[0]
            page = _bounded_call(context, "new_page", local)  # At most one request.
            current_contexts, current_targets = self._targets(browser, self.binding.chat_url, bounded=True)
            if (current_contexts != contexts or current_targets or page.url != "about:blank"
                    or page not in context.pages):
                raise wake.WakeBlocked("TARGET_PAGE_CHANGED")
            page.goto(self.binding.chat_url, wait_until="domcontentloaded", timeout=local.remaining_ms(8000))
            if (list(browser.contexts) != contexts
                    or self.select_page(browser, self.binding.chat_url) is not page):
                raise wake.WakeBlocked("TARGET_PAGE_CHANGED")
        else:
            page = targets[0]
        local.prove_owner()  # Endpoint/profile replacement cannot authorize editing.
        local.remaining_ms()
        self.browser, self.page = browser, page
        self.page.set_default_timeout(3000)

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.driver = sync_playwright().start()
        try:
            browser = None
            try:
                browser = self.driver.chromium.connect_over_cdp(self.binding.cdp_endpoint, timeout=10000)
            except Exception:
                pass  # A separate Windows listener-table proof is required before launch.
            if browser is not None:
                _, targets = self._targets(browser, self.binding.chat_url)
                if targets:
                    self.browser, self.page = browser, targets[0]
                    self.page.set_default_timeout(3000)
                    return self  # Preserve attachment without configuration or OS inspection.
            if self.before_acquire is None or self.binding.route_handle is None:
                raise wake.WakeBlocked("ATTEMPT_REQUIRES_HUMAN")
            self.before_acquire()
            deadline = time.monotonic() + ACQUISITION_SECONDS
            local = LocalEnvironment(load_environment(self.binding), deadline)
            with local.locked():
                self._acquire(browser, local)
            return self
        except Exception as exc:
            self.driver.stop()  # Disconnect only, including any failed launch/navigation.
            if isinstance(exc, (wake.WakeBlocked, wake._Resolved)):
                raise
            raise wake.WakeBlocked("LAUNCH_UNCERTAIN") from None
