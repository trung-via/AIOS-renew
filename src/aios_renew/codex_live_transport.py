"""One non-resumable first-party Codex app-server stdio connection.

Protocol: https://developers.openai.com/codex/app-server (stable v2 methods).
No socket, saved-thread resume, retry, credential issuer or detached ACK path.
Protocol data is untrusted; only correlated completed turns return candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import signal
import stat
import shutil
import subprocess
import threading
import time
from uuid import uuid4


MAX_MESSAGE_BYTES = 1024 * 1024
MAX_STREAM_BYTES = 128 * MAX_MESSAGE_BYTES
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_NOTIFICATIONS = frozenset({
    "thread/started", "thread/status/changed", "thread/readState/changed", "thread/name/updated",
    "thread/tokenUsage/updated", "thread/settings/updated", "thread/queue/changed",
    "turn/started", "turn/completed", "turn/diff/updated", "turn/plan/updated", "turn/moderationMetadata",
    "item/started", "item/completed", "item/agentMessage/delta", "item/plan/delta",
    "item/commandExecution/outputDelta", "item/commandExecution/terminalInteraction",
    "item/fileChange/outputDelta", "item/fileChange/patchUpdated", "item/mcpToolCall/progress",
    "item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded", "item/reasoning/textDelta",
    "hook/started", "hook/completed", "mcpServer/startupStatus/updated", "mcpServer/event/stream/notification",
    "account/updated", "account/rateLimits/updated", "app/list/updated", "remoteControl/status/changed",
    "skills/changed", "fs/changed", "model/verification", "model/safetyBuffering/updated",
    "warning", "guardianWarning", "deprecationNotice", "configWarning", "windows/worldWritableWarning",
})


def _windows_process_job(process):
    """Kill the original native process tree on close, before releasing its RUN.

    Assignment happens before initialize or any model turn. If confinement is
    unavailable, the channel is unsupported; never leave a mutating child alive.
    """
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
            ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
            ("active", wintypes.DWORD), ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read", "write", "other")]
    class Limits(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", Counters), ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        _stop("PROCESS_TREE_CONFINEMENT_UNAVAILABLE")
    limits = Limits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            or not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle)))):
        kernel.CloseHandle(job)
        _stop("PROCESS_TREE_CONFINEMENT_UNAVAILABLE")
    return lambda: kernel.CloseHandle(job)


class NativeSessionStop(RuntimeError):
    """A fixed, safe STOP code; never include provider output or local paths."""


def _stop(code):
    raise NativeSessionStop("CODEX_LIVE_STOP_" + code)


def _identifier(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        _stop("INVALID_IDENTITY")
    return value


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _decode(raw):
    def pairs(rows):
        value = {}
        for key, item in rows:
            if key in value:
                _stop("DUPLICATE_MESSAGE_FIELD")
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: _stop("NONFINITE_MESSAGE"))
    except (ValueError, UnicodeError, RecursionError):
        _stop("MALFORMED_MESSAGE")
    if type(value) is not dict:
        _stop("INVALID_MESSAGE_TYPE")
    return value


@dataclass(frozen=True)
class NativeTurn:
    connection_id: str
    thread_id: str
    turn_id: str
    request_id: int
    input_sha256: str
    user_item_id: str
    output_item_id: str
    output_json: bytes

    @property
    def output(self):
        return _decode(self.output_json)


class CodexLiveTransport:
    """An original Popen object, private pipes and one new logical thread.

    A new inference turn is a new turn in this session, never represented as
    the original in-progress model turn. Close is irreversible.
    """

    def __init__(self, *, workspace, profile, authorizes_mutation, poll, timeout_seconds):
        self.workspace = str(Path(workspace).absolute())
        self.profile = profile
        self._profile_data = _json(profile.as_dict())
        self._poll = poll
        self._deadline = time.monotonic() + timeout_seconds
        self.connection_id = uuid4().hex
        self.thread_id = None
        self.last_turn = None
        self.turn_observations = []
        self._turns = set()
        self._request_id = 0
        self._account_snapshot = None
        self._account_notification = None
        self._account_update_pending = False
        self._closed = False
        self._broken = threading.Event()
        self._messages = queue.Queue(maxsize=512)
        self._deferred = []
        self._process = None
        self._close_job = None
        self._owner_thread = threading.get_ident()
        self._sandbox = "danger-full-access" if authorizes_mutation else "read-only"
        binary = shutil.which("codex")
        if binary is None or Path(binary).suffix.lower() in {".cmd", ".bat", ".ps1"}:
            _stop("NATIVE_BINARY_UNAVAILABLE")
        try:
            self._binary = Path(binary).resolve(strict=True)
            if self._binary.is_relative_to(Path(self.workspace)):
                _stop("WORKSPACE_BINARY_NOT_FIRST_PARTY")
            info = self._binary.stat()
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 512 * MAX_MESSAGE_BYTES:
                _stop("NATIVE_BINARY_UNAVAILABLE")
            self._binary_stat = self._stat_binary()
            with self._binary.open("rb") as stream:
                self.binary_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError:
            _stop("NATIVE_BINARY_UNAVAILABLE")
        command = [str(self._binary), "app-server", "--listen", "stdio://",
                   "-c", 'model=' + json.dumps(profile.model),
                   "-c", 'model_reasoning_effort=' + json.dumps(profile.reasoning_effort),
                   "-c", 'approval_policy="never"',
                   "-c", "features.multi_agent=false", "-c", "features.remote_plugin=false"]
        try:
            self._process = subprocess.Popen(command, cwd=self.workspace,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if os.name == "nt":
                self._close_job = _windows_process_job(self._process)
            self._original_process = self._process
            self._pipes = (self._process.stdin, self._process.stdout, self._process.stderr)
            self._pid = self._process.pid
            threading.Thread(target=self._read_stdout, daemon=True).start()
            threading.Thread(target=self._drain_stderr, daemon=True).start()
            initialized = self._rpc("initialize", dict(clientInfo=dict(
                name="aios_renew", title="AIOS Runtime", version="1.3")))
            if type(initialized.get("userAgent")) is not str:
                _stop("INITIALIZE_UNSUPPORTED")
            self._send(dict(method="initialized", params={}))
            account = self._read_account()
            # Keep provider identity private; no account data enters the model,
            # checkpoint, feedback, observation or public session identity.
            self._account_snapshot = _json(account)
            self._account_notification = _json(dict(authMode="chatgpt", planType=account["account"]["planType"]))
            started = self._rpc("thread/start", dict(model=profile.model, modelProvider="openai",
                cwd=self.workspace, approvalPolicy="never", sandbox=self._sandbox,
                ephemeral=True, config={"model_reasoning_effort": profile.reasoning_effort,
                                        "features.multi_agent": False}))
            self.thread_id = _identifier(started.get("thread", {}).get("id"))
            if (started.get("model") != profile.model or started.get("modelProvider") != "openai"
                    or started.get("reasoningEffort") != profile.reasoning_effort
                    or started.get("approvalPolicy") != "never"
                    or Path(started.get("cwd", "")).absolute() != Path(self.workspace)
                    or started.get("sandbox", {}).get("type") != (
                        "dangerFullAccess" if authorizes_mutation else "readOnly")):
                _stop("SELECTED_PROFILE_UNSUPPORTED")
            self._session_identity = self.identity()
            for event in self._deferred:
                self._notification(event, handshake=True)
            self._deferred.clear()
        except BaseException as exc:
            self.close()
            if isinstance(exc, NativeSessionStop) or not isinstance(exc, Exception):
                raise
            _stop("NATIVE_CHANNEL_UNAVAILABLE")

    def _stat_binary(self):
        st = self._binary.stat()
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns

    def identity(self):
        return dict(connection_id=self.connection_id, process_id=self._pid,
                    thread_id=self.thread_id, binary_sha256=self.binary_sha256,
                    execution_profile_sha256=hashlib.sha256(self._profile_data).hexdigest())

    def assert_live(self, expected=None):
        process = self._process
        try:
            binary_stat = self._stat_binary()
        except OSError:
            _stop("ORIGINAL_NATIVE_BINARY_LOST")
        if (self._closed or self._broken.is_set() or process is None
                or process is not self._original_process or process.pid != self._pid
                or process.poll() is not None or threading.get_ident() != self._owner_thread
                or (process.stdin, process.stdout, process.stderr) != self._pipes
                or any(p is None or p.closed for p in self._pipes)
                or binary_stat != self._binary_stat
                or _json(self.profile.as_dict()) != self._profile_data
                or (expected is not None and self.identity() != expected)):
            _stop("ORIGINAL_PROCESS_CONNECTION_SESSION_LOST")

    def _read_stdout(self):
        total = 0
        try:
            while not self._closed:
                raw = self._pipes[1].readline(MAX_MESSAGE_BYTES + 1)
                total += len(raw)
                if not raw or len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n") or total > MAX_STREAM_BYTES:
                    self._broken.set()
                    return
                self._messages.put_nowait(_decode(raw))
        except Exception:
            self._broken.set()

    def _drain_stderr(self):
        total = 0
        try:
            while not self._closed:
                raw = self._pipes[2].read(4096)
                if not raw:
                    return
                total += len(raw)
                if total > MAX_STREAM_BYTES:
                    self._broken.set()
                    return
        except Exception:
            self._broken.set()

    def _send(self, message):
        self.assert_live()
        raw = _json(message) + b"\n"
        if len(raw) > MAX_MESSAGE_BYTES:
            _stop("MESSAGE_BOUND")
        done = threading.Event()
        def write():
            try:
                if self._pipes[0].write(raw) != len(raw):
                    self._broken.set()
                self._pipes[0].flush()
            except (OSError, ValueError):
                self._broken.set()
            finally:
                done.set()
        threading.Thread(target=write, daemon=True).start()
        while not done.wait(0.2):
            self._poll()
            self.assert_live()
            if time.monotonic() >= self._deadline:
                _stop("CONNECTION_WRITE_DEADLINE")
        self.assert_live()

    def _next(self):
        while True:
            self._poll()
            self.assert_live()
            if time.monotonic() >= self._deadline:
                _stop("SESSION_DEADLINE_EXHAUSTED")
            try:
                message = self._messages.get(timeout=0.2)
                break
            except queue.Empty:
                continue
        if "id" in message and "method" in message:
            # The selected never-approval profile must not ask this client to
            # supply credentials, permissions, tools, user input or side effects.
            _stop("UNSUPPORTED_SERVER_REQUEST")
        return message

    def _rpc(self, method, params):
        self._request_id += 1
        request_id = self._request_id
        self._send(dict(id=request_id, method=method, params=params))
        for _ in range(200000):
            message = self._next()
            if "id" in message:
                if (type(message["id"]) is not int or message["id"] != request_id
                        or "error" in message or type(message.get("result")) is not dict):
                    _stop("REQUEST_RESPONSE_MISMATCH")
                return message["result"]
            if len(self._deferred) >= 512:
                _stop("NOTIFICATION_BOUND")
            self._deferred.append(message)
        _stop("EVENT_BOUND")

    def _notification(self, message, *, handshake=False, turn_id=None):
        method, params = message.get("method"), message.get("params")
        if type(method) is not str or type(params) is not dict:
            _stop("INVALID_NOTIFICATION")
        if method in {"error", "thread/closed", "thread/archived", "thread/deleted", "thread/reverted",
                "thread/compacted", "thread/environment/disconnected", "model/rerouted",
                "modelProvider/authRecoveryStarted", "modelProvider/authRecoveryCompleted"}:
            _stop("SESSION_ERROR_OR_CLOSED")
        if params.get("threadId", self.thread_id) != self.thread_id:
            _stop("FOREIGN_THREAD")
        if "turnId" in params and params["turnId"] != turn_id:
            _stop("STALE_OR_FOREIGN_TURN")
        if method == "thread/started":
            thread = params.get("thread")
            if (type(thread) is not dict or thread.get("id") != self.thread_id
                    or thread.get("modelProvider") != "openai"):
                _stop("UNSOLICITED_OR_FOREIGN_THREAD")
        if method in {"turn/started", "turn/completed"}:
            turn = params.get("turn")
            if (type(turn) is not dict or turn.get("id") != turn_id
                    or type(turn.get("status")) is not str):
                _stop("INVALID_OR_FOREIGN_TURN")
        if handshake and method.startswith(("turn/", "item/")):
            _stop("UNSOLICITED_TURN")
        if method == "account/updated":
            # The installed protocol reports auth mode/plan, not an account
            # switch. Even an equal notification needs an identity reread at
            # the next idle boundary, before accepting or sending a candidate.
            if _json(params) != getattr(self, "_account_notification", None):
                _stop("ORIGINAL_AUTHENTICATED_ACCOUNT_CHANGED")
            self._account_update_pending = True
        # Deprecated codex/event notifications duplicate the v2 stream. They
        # never establish receipt or candidate identity.
        if method.startswith("codex/event/"):
            # Its legacy id is not a v2 thread/turn identifier and supplies no
            # correlation. Only the v2 user/item/turn stream can return a result.
            legacy = params.get("msg", {})
            if type(legacy) is not dict or legacy.get("type") in {"model_rerouted", "error"}:
                _stop("UNSUPPORTED_LEGACY_EVENT")
            if "model" in legacy and legacy["model"] != self.profile.model:
                _stop("SELECTED_PROFILE_CHANGED")
            return method, params
        if method == "thread/settings/updated":
            settings = params.get("threadSettings", {})
            if (type(settings) is not dict or settings.get("model") != self.profile.model or settings.get("modelProvider") != "openai"
                    or settings.get("effort") != self.profile.reasoning_effort
                    or settings.get("approvalPolicy") != "never"
                    or type(settings.get("cwd")) is not str
                    or Path(settings.get("cwd", "")).absolute() != Path(self.workspace)
                    or type(settings.get("sandboxPolicy")) is not dict
                    or settings.get("sandboxPolicy", {}).get("type") != (
                        "dangerFullAccess" if self._sandbox == "danger-full-access" else "readOnly")):
                _stop("SELECTED_PROFILE_CHANGED")
        if method not in _NOTIFICATIONS:
            _stop("UNSUPPORTED_NOTIFICATION")
        return method, params

    def _drain_idle(self):
        """Check queued source/profile changes before exposing another input."""
        for _ in range(512):
            try:
                message = self._messages.get_nowait()
            except queue.Empty:
                return
            if "id" in message:
                _stop("UNSOLICITED_RESPONSE")
            method, _ = self._notification(message,
                turn_id=None if self.last_turn is None else self.last_turn.turn_id)
            if method.startswith(("turn/", "item/")):
                _stop("LATE_OR_UNSOLICITED_TURN")
        _stop("NOTIFICATION_BOUND")

    def _read_account(self):
        account = self._rpc("account/read", dict(refreshToken=False))
        identity = account.get("account")
        if (account.get("requiresOpenaiAuth") is not True or type(identity) is not dict
                or identity.get("type") != "chatgpt" or type(identity.get("email")) is not str
                or not identity["email"] or type(identity.get("planType")) is not str or not identity["planType"]):
            _stop("AUTHENTICATED_ACCOUNT_IDENTITY_UNSUPPORTED")
        return account

    def assert_idle(self):
        self.assert_live(self._session_identity)
        for _ in range(4):
            self._drain_idle()
            if not self._account_update_pending:
                return
            if _json(self._read_account()) != self._account_snapshot:
                _stop("ORIGINAL_AUTHENTICATED_ACCOUNT_CHANGED")
            # These notifications preceded the reread response. Validate them
            # before clearing only the update that this response authenticated.
            pending, self._deferred = self._deferred, []
            for message in pending:
                method, _ = self._notification(message,
                    turn_id=None if self.last_turn is None else self.last_turn.turn_id)
                if "id" in message or method.startswith(("turn/", "item/")):
                    _stop("LATE_OR_UNSOLICITED_TURN")
            self._account_update_pending = False
        _stop("ACCOUNT_UPDATE_BOUND")

    def turn(self, *, prompt, output_schema):
        self.assert_idle()
        client_id = uuid4().hex
        started = self._rpc("turn/start", dict(threadId=self.thread_id,
            input=[dict(type="text", text=prompt, text_elements=[])],
            clientUserMessageId=client_id, model=self.profile.model,
            effort=self.profile.reasoning_effort, outputSchema=output_schema))
        if type(started.get("turn")) is not dict:
            _stop("INVALID_TURN_START_RESPONSE")
        turn_id = _identifier(started.get("turn", {}).get("id"))
        if turn_id in self._turns or started["turn"].get("status") not in {"inProgress", "completed"}:
            _stop("DUPLICATE_OR_INVALID_TURN")
        self._turns.add(turn_id)
        request_id = self._request_id
        self.turn_observations.append(dict(turn_id=turn_id, request_id=request_id,
            input_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(), status="STARTED"))
        user_items, final_items, completed_items = {}, {}, set()
        pending, self._deferred = self._deferred, []
        for index in range(200000):
            message = pending.pop(0) if pending else self._next()
            if "id" in message:
                _stop("DUPLICATE_RESPONSE")
            method, params = self._notification(message, turn_id=turn_id)
            if method in {"turn/started", "turn/completed"} and params.get("turn", {}).get("id") != turn_id:
                _stop("STALE_OR_FOREIGN_TURN")
            if method in {"item/started", "item/completed"}:
                item = params.get("item", {})
                if type(item) is not dict:
                    _stop("MALFORMED_ITEM")
                item_id = _identifier(item.get("id"))
                if method == "item/completed":
                    if item_id in completed_items:
                        _stop("DUPLICATE_ITEM")
                    completed_items.add(item_id)
                if item.get("type") == "userMessage":
                    content = item.get("content")
                    if (type(content) is not list or len(content) != 1 or type(content[0]) is not dict
                            or set(content[0]) - {"type", "text", "text_elements"}
                            or content[0].get("type") != "text" or content[0].get("text") != prompt
                            or content[0].get("text_elements", []) != [] or item.get("clientId") not in (None, client_id)):
                        _stop("SOURCE_INPUT_MISMATCH")
                    user_items[item_id] = True
                if (method == "item/completed" and item.get("type") == "agentMessage"
                        and item.get("phase") in (None, "final_answer")):
                    if type(item.get("text")) is not str:
                        _stop("INVALID_CANDIDATE")
                    final_items[item_id] = item["text"]
            if method == "turn/completed":
                if (params["turn"].get("status") != "completed" or params["turn"].get("error") is not None
                        or len(user_items) != 1 or len(final_items) != 1 or pending):
                    _stop("INCOMPLETE_CANDIDATE_TURN")
                output_id, text = next(iter(final_items.items()))
                raw = text.encode("utf-8")
                if len(raw) > MAX_MESSAGE_BYTES:
                    _stop("CANDIDATE_BOUND")
                _decode(raw)
                self.assert_live(self._session_identity)
                result = NativeTurn(self.connection_id, self.thread_id, turn_id, request_id,
                    hashlib.sha256(prompt.encode("utf-8")).hexdigest(), next(iter(user_items)), output_id, raw)
                self.last_turn = result
                self.turn_observations[-1].update(status="COMPLETED", user_item_id=result.user_item_id,
                    output_item_id=result.output_item_id, output_sha256=hashlib.sha256(raw).hexdigest())
                self.assert_idle()
                return result
        _stop("EVENT_BOUND")

    def close(self):
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is not None:
            if self._close_job is not None:
                self._close_job()
                self._close_job = None
            elif os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
