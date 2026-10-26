"""Original ordinary caller integration; Runtime owns executing these tests.

Fixture transport tests exercise deterministic guards, never claim live Codex
delivery. The separate installed-native test uses the actual app-server binary,
model turns and commits; unavailable native support is a failing BLOCKER.
"""

from dataclasses import replace
import io
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from aios_renew import codex_adapter
from aios_renew import codex_live_transport as native
from aios_renew import ka01_admission as ka01
from aios_renew import operator
from aios_renew.artifacts import ResultPackage, validate_structural_result
from aios_renew.execution_profile import ResolvedExecutionProfile
from aios_renew.intra_run_proof_contract import parse_native_primary_checkpoint
from aios_renew.verification_contract import VerificationContractError
from tests.git_fixture_support import commit_fixture_state
from tests.operator_test_support import git, static_payload
from tests.test_ka01_v13_activation import ProductionFixture


class _ObservedStdoutQueue(queue.Queue):
    def __init__(self, maxsize):
        super().__init__(maxsize=maxsize)
        self.saturated = threading.Event()
        self.backpressured = threading.Event()

    def put(self, item, block=True, timeout=None):
        if self.full():
            self.saturated.set()
        try:
            return super().put(item, block=block, timeout=timeout)
        except queue.Full:
            if block and timeout is not None:
                self.backpressured.set()
            raise


_NATIVE_HANDSHAKE = """
import json, sys
while True:
    request = json.loads(sys.stdin.buffer.readline())
    method = request['method']
    if method == 'initialized':
        continue
    if method == 'initialize':
        result = dict(userAgent='local-protocol-fixture')
    elif method == 'account/read':
        result = dict(requiresOpenaiAuth=True,
            account=dict(type='chatgpt', email='fixture@example.invalid', planType='pro'))
    else:
        assert method == 'thread/start'
        params = request['params']
        result = dict(thread=dict(id='original-thread'), model=params['model'],
            modelProvider='openai', reasoningEffort='low', approvalPolicy='never',
            cwd=params['cwd'], sandbox=dict(type='readOnly'))
    print(json.dumps(dict(id=request['id'], result=result)), flush=True)
    if method == 'thread/start':
        break
"""


@pytest.fixture
def native_stdout_session(tmp_path, monkeypatch):
    """Real local pipes/process; synthetic protocol, no model or AIOS RUN."""
    sessions = []

    def start(script):
        profile = ResolvedExecutionProfile(
            run_id="RUN-007-001", executor="codex", model="test/native", reasoning_effort="low")
        owner, polled_threads, readers = threading.get_ident(), [], {}
        popen = subprocess.Popen

        def poll():
            polled_threads.append(threading.get_ident())
            assert threading.get_ident() == owner

        def process(command, **kwargs):
            return popen([sys.executable, "-u", "-c", _NATIVE_HANDSHAKE + script], **kwargs)

        def thread(*, target, **kwargs):
            reader = threading.Thread(target=target, **kwargs)
            readers[target.__name__] = reader
            return reader

        with monkeypatch.context() as patch:
            patch.setattr(native.shutil, "which", lambda _: sys.executable)
            patch.setattr(native.subprocess, "Popen", process)
            patch.setattr(native, "queue", SimpleNamespace(
                Queue=_ObservedStdoutQueue, Full=queue.Full, Empty=queue.Empty))
            patch.setattr(native, "threading", SimpleNamespace(
                Event=threading.Event, get_ident=threading.get_ident, Thread=thread))
            session = native.CodexLiveTransport(workspace=tmp_path, profile=profile,
                authorizes_mutation=False, poll=poll, timeout_seconds=15)
        session.reader, session.stderr_reader = readers["_read_stdout"], readers["_drain_stderr"]
        session.polled_threads = polled_threads
        sessions.append(session)
        return session

    yield start
    for session in sessions:
        session.close()
        session.reader.join(timeout=3)
        session.stderr_reader.join(timeout=3)
        assert not session.reader.is_alive() and not session.stderr_reader.is_alive()


_STDOUT_BURST = """
import json, sys, threading
def burst():
    for index in range(3000):
        print(json.dumps(dict(method='item/commandExecution/outputDelta',
            params=dict(threadId='original-thread', turnId='original-turn', delta=str(index)))))
    sys.stdout.flush()
threading.Thread(target=burst, daemon=True).start()
if sys.stdin.buffer.readline().strip() == b'stderr':
    sys.stderr.buffer.write(b'x' * 4096)
    sys.stderr.flush()
sys.stdin.buffer.readline()
"""


def _await_stdout_backpressure(session):
    assert session._messages.saturated.wait(timeout=3)
    # Wait through a full timed put with no consumer, not just a scheduling race.
    assert session._messages.backpressured.wait(timeout=3)
    assert session._messages.maxsize == session._messages.qsize() == 512
    assert session.reader.is_alive() and not session._broken.is_set()
    session.assert_live(session._session_identity)


def test_native_stdout_burst_and_slow_consumer_preserve_bounded_lossless_fifo(native_stdout_session):
    session = native_stdout_session(_STDOUT_BURST)
    _await_stdout_backpressure(session)
    received = []
    for batch in range(3):
        session._messages.backpressured.clear()
        received.extend(session._next()["params"]["delta"] for _ in range(1000))
        if batch < 2:
            _await_stdout_backpressure(session)
    assert received == [str(index) for index in range(3000)]
    assert session._messages.empty() and not session._broken.is_set()
    session.assert_live(session._session_identity)
    assert set(session.polled_threads) == {session._owner_thread}


@pytest.mark.parametrize("termination", ["close", "interrupt", "deadline", "exit", "stderr"])
def test_native_stdout_starvation_releases_on_original_stop(
    native_stdout_session, monkeypatch, termination,
):
    session = native_stdout_session(_STDOUT_BURST)
    _await_stdout_backpressure(session)
    deadline = session._deadline
    if termination == "close":
        session.close()
        session.close()
    elif termination == "interrupt":
        poll = session._poll

        def interrupted():
            session.close()
            raise KeyboardInterrupt()

        session._poll = interrupted
        with pytest.raises(KeyboardInterrupt):
            session._next()
        session._poll = poll
    elif termination == "deadline":
        monkeypatch.setattr(native, "time", SimpleNamespace(monotonic=lambda: deadline))
    elif termination == "exit":
        session._original_process.kill()
        session._original_process.wait(timeout=3)
    else:
        # Only stderr can exceed the reduced limit while stdout is backpressured.
        monkeypatch.setattr(native, "MAX_STREAM_BYTES", 4095)
        session._pipes[0].write(b"stderr\n")
        session._pipes[0].flush()
    session.reader.join(timeout=3)
    assert not session.reader.is_alive() and session._messages.qsize() == 512
    assert session._deadline == deadline
    if termination == "stderr":
        assert session._broken.is_set() and session._original_process.poll() is None
    code = "SESSION_DEADLINE_EXHAUSTED" if termination == "deadline" else "ORIGINAL_PROCESS_CONNECTION_SESSION_LOST"
    with pytest.raises(native.NativeSessionStop, match=code):
        session._next()


@pytest.mark.parametrize("raw", [
    b"", b"{}", b"not JSON\n", b'{"id":1,"id":1}\n', b'{"id":NaN}\n',
    b"[]\n", b'"\xff"\n', b" " * native.MAX_MESSAGE_BYTES + b"\n",
])
def test_native_stdout_reader_rejects_eof_malformed_and_oversized_frames(raw):
    session = native.CodexLiveTransport.__new__(native.CodexLiveTransport)
    session._closed, session._broken = False, threading.Event()
    session._messages = queue.Queue(maxsize=512)
    session._pipes = (None, io.BytesIO(raw), None)
    session._deadline = time.monotonic() + 3
    session._original_process = SimpleNamespace(poll=lambda: None)
    session._read_stdout()
    assert session._broken.is_set() and session._messages.empty()


def test_native_stdout_reader_io_error_fails_closed():
    class BrokenStdout:
        def readline(self, bound):
            raise OSError()

    session = native.CodexLiveTransport.__new__(native.CodexLiveTransport)
    session._closed, session._broken = False, threading.Event()
    session._messages = queue.Queue(maxsize=512)
    session._pipes = (None, BrokenStdout(), None)
    session._read_stdout()
    assert session._broken.is_set() and session._messages.empty()


def test_native_stdout_reader_retains_total_stream_bound(monkeypatch):
    monkeypatch.setattr(native, "MAX_STREAM_BYTES", 5)
    session = native.CodexLiveTransport.__new__(native.CodexLiveTransport)
    session._closed, session._broken = False, threading.Event()
    session._messages = queue.Queue(maxsize=512)
    session._pipes = (None, io.BytesIO(b"{}\n{}\n"), None)
    session._deadline = time.monotonic() + 3
    session._original_process = SimpleNamespace(poll=lambda: None)
    session._read_stdout()
    assert session._broken.is_set() and session._messages.qsize() == 1
    assert session._messages.get_nowait() == {}


@pytest.mark.parametrize("defect", ["profile", "binary", "process", "owner", "pipe", "session"])
def test_native_stdout_backpressure_preserves_original_identity_guards(native_stdout_session, monkeypatch, defect):
    session = native_stdout_session(_STDOUT_BURST)
    _await_stdout_backpressure(session)
    expected = session.identity()
    if defect == "profile":
        monkeypatch.setattr(session, "profile", replace(session.profile, model="changed"))
    elif defect == "binary":
        monkeypatch.setattr(session, "_binary_stat", ())
    elif defect == "process":
        monkeypatch.setattr(session, "_process", SimpleNamespace(pid=session._pid))
    elif defect == "owner":
        monkeypatch.setattr(session, "_owner_thread", -1)
    elif defect == "pipe":
        monkeypatch.setattr(session._process, "stdin", io.BytesIO())
    else:
        expected["thread_id"] = "foreign-thread"
    try:
        with pytest.raises(native.NativeSessionStop, match="ORIGINAL_PROCESS_CONNECTION_SESSION_LOST"):
            session.assert_live(expected)
        assert not session._broken.is_set()
    finally:
        # Restore test substitutions before the fixture closes the real process.
        monkeypatch.undo()


_SCRIPTED_NATIVE_TURN = """
import json, sys
candidate, fault = CANDIDATE, FAULT
def emit(message):
    print(json.dumps(message), flush=True)
for ordinal, line in enumerate(sys.stdin.buffer, 1):
    request = json.loads(line)
    assert request['method'] == 'turn/start'
    turn_id = 'turn-' + str(ordinal)
    params = dict(threadId='original-thread', turnId=turn_id)
    emit(dict(id=request['id'], result=dict(turn=dict(id=turn_id, status='inProgress'))))
    emit(dict(method='turn/started', params=dict(params, turn=dict(id=turn_id, status='inProgress'))))
    user = dict(id='user-' + str(ordinal), type='userMessage',
        clientId=request['params']['clientUserMessageId'], content=request['params']['input'])
    emit(dict(method='item/completed', params=dict(params, item=user)))
    for index in range(2400):
        emit(dict(method='item/commandExecution/outputDelta', params=dict(params, delta=str(index))))
    if fault == 'server-request':
        emit(dict(id=900, method='item/commandExecution/requestApproval', params=params))
    elif fault == 'foreign-thread':
        emit(dict(method='item/commandExecution/outputDelta', params=dict(params, threadId='foreign')))
    elif fault == 'foreign-turn':
        emit(dict(method='item/commandExecution/outputDelta', params=dict(params, turnId='foreign')))
    elif fault == 'unsupported':
        emit(dict(method='unreviewed/notification', params=params))
    elif fault == 'duplicate-response':
        emit(dict(id=request['id'], result=dict(turn=dict(id=turn_id, status='inProgress'))))
    elif fault == 'duplicate-item':
        emit(dict(method='item/completed', params=dict(params, item=user)))
    elif fault == 'malformed':
        print('not JSON', flush=True)
    elif fault == 'eof':
        raise SystemExit(0)
    emit(dict(method='item/completed', params=dict(params,
        item=dict(id='output-' + str(ordinal), type='agentMessage', phase='final_answer',
            text=json.dumps(candidate)))))
    emit(dict(method='turn/completed', params=dict(params, turn=dict(id=turn_id, status='completed'))))
"""


def _scripted_turn_session(start, *, operation="PRIMARY", fault=None):
    package = dict(result=dict(head_sha="def456", changed_files=["src/aios_renew/codex_adapter.py"],
        claims=[dict(id="C1", satisfies=["AC1"], claim="Local protocol fixture candidate.", evidence=[])],
        unresolved=[]), evidence=[])
    if operation == "REMEDIATION":
        package["result"]["claims"] = []
    candidate = dict(package=package, feedback_receipt=None)
    script = _SCRIPTED_NATIVE_TURN.replace("CANDIDATE", repr(candidate)).replace("FAULT", repr(fault))
    session = start(script)
    rpc = session._rpc

    def delayed_consumer(method, params):
        session._messages.backpressured.clear()
        response = rpc(method, params)
        _await_stdout_backpressure(session)
        return response

    session._rpc = delayed_consumer
    return session, candidate


@pytest.mark.parametrize("fault,code", [
    ("server-request", "UNSUPPORTED_SERVER_REQUEST"),
    ("foreign-thread", "FOREIGN_THREAD"),
    ("foreign-turn", "STALE_OR_FOREIGN_TURN"),
    ("unsupported", "UNSUPPORTED_NOTIFICATION"),
    ("duplicate-response", "DUPLICATE_RESPONSE"),
    ("duplicate-item", "DUPLICATE_ITEM"),
    ("malformed", "ORIGINAL_PROCESS_CONNECTION_SESSION_LOST"),
    ("eof", "ORIGINAL_PROCESS_CONNECTION_SESSION_LOST"),
])
def test_native_protocol_failure_after_saturation_cannot_complete_or_retry(native_stdout_session, fault, code):
    session, _ = _scripted_turn_session(native_stdout_session, fault=fault)
    with pytest.raises(native.NativeSessionStop, match=code):
        session.turn(prompt="original input", output_schema={})
    assert session.last_turn is None and session._request_id == 4
    assert session._turns == {"turn-1"}
    assert [observation["status"] for observation in session.turn_observations] == ["STARTED"]


class FixtureSession:
    """Explicitly synthetic, never accepted as the installed-native test."""
    def __init__(self, fixture, **kwargs):
        self.f = fixture
        self.profile = kwargs["profile"]
        self.connection_id, self.thread_id = "fixture-connection", "fixture-thread"
        self.closed = False
        self.last_turn = None
        self.turn_observations = []
        self.calls = 0
        self.hook = None

    def identity(self):
        return dict(connection_id=self.connection_id, thread_id=self.thread_id, process_id=123,
            binary_sha256="0" * 64, execution_profile_sha256=ka01._digest(self.profile.as_dict()))

    def assert_live(self, expected=None):
        if self.closed or (expected is not None and expected != self.identity()):
            raise native.NativeSessionStop("CODEX_LIVE_STOP_ORIGINAL_PROCESS_CONNECTION_SESSION_LOST")

    def assert_idle(self):
        self.assert_live()

    def turn(self, *, prompt, output_schema):
        self.calls += 1
        ordinal = self.calls
        if self.hook:
            self.hook(self, ordinal, prompt)
        if ordinal == 1:
            receipt = None
            output = self.f.output
        else:
            envelope = json.loads(prompt.rsplit("\n", 1)[1])
            # The fixture observes factual feedback only after exact C1 proof.
            assert envelope["feedback"]["checkpoint_digest"] == self.f.owner._interactive_checkpoints[-1].digest
            assert not (self.f.state.failures / (self.f.run.run_id + ".json")).exists()
            assert not (self.f.state.results / (self.f.run.run_id + ".json")).exists()
            assert len(self.f.verification_calls) == (ordinal - 1) * getattr(self.f, "commands_per_checkpoint", 1)
            assert "traceback" not in prompt.lower() and str(self.f.repo) not in prompt
            receipt = envelope["receipt"]
            output = "complete\n"
            if getattr(self.f, "receipt_defect", None):
                receipt = {**receipt, self.f.receipt_defect: "forged"}
            if getattr(self.f, "same_source", False):
                output = None
            if getattr(self.f, "no_progress", False):
                output = "still incomplete\n"
        if getattr(self.f, "candidates", None):
            output = self.f.candidates[ordinal - 1]
        if output is not None:
            (self.f.repo / "OUTPUT.txt").write_text(output, encoding="utf-8")
            head = commit_fixture_state(self.f.repo, paths=("OUTPUT.txt",), message=f"fixture C{ordinal}",
                user_name="Native fixture", user_email="native@example.invalid")
        else:
            head = git(self.f.repo, "rev-parse", "HEAD")
        payload = static_payload()
        payload["result"].update(head_sha=head, changed_files=["OUTPUT.txt"])
        raw = ka01._bytes(dict(package=payload, feedback_receipt=receipt))
        turn = native.NativeTurn(self.connection_id, self.thread_id, f"fixture-turn-{ordinal}", ordinal + 2,
            ka01._digest(prompt.encode()), f"user-{ordinal}", f"candidate-{ordinal}", raw)
        self.last_turn = turn
        self.turn_observations.append(dict(turn_id=turn.turn_id, request_id=turn.request_id,
            input_sha256=turn.input_sha256, status="COMPLETED"))
        return turn

    def close(self):
        self.closed = True


def fixture_session(f, monkeypatch):
    sessions = []
    def construct(**kwargs):
        session = FixtureSession(f, **kwargs)
        sessions.append(session)
        return session
    monkeypatch.setattr(codex_adapter, "CodexLiveTransport", construct)
    return sessions


def checkpoint(f, ordinal):
    return parse_native_primary_checkpoint((f.owner._checkpoint_directory() / f"n{ordinal:03d}.json").read_bytes())


def test_fixture_ordinary_c1_fail_feedback_c2_pass_one_terminal_and_no_final_rerun(tmp_path, monkeypatch):
    f = ProductionFixture(tmp_path / "correction", monkeypatch, interactive=True)
    f.authorize()
    f.output = "incomplete\n"
    sessions = fixture_session(f, monkeypatch)
    summary = f.execute()
    budget = f.owner._ka01_budget
    first, second = checkpoint(f, 1), checkpoint(f, 2)
    assert len(sessions) == 1 and sessions[0].calls == 2 and sessions[0].closed
    assert len(f.verification_calls) == 2 and not f.native_calls
    assert first.data["failed_identities"] and not second.data["failed_identities"]
    assert first.data["binding"]["candidate_sha"] != second.data["binding"]["candidate_sha"]
    assert second.data["predecessor_digest"] == first.digest
    assert first.data["binding"]["session"] == second.data["binding"]["session"]
    assert first.data["binding"]["turn"]["turn_id"] != second.data["binding"]["turn"]["turn_id"]
    assert budget.checkpoints_spent == 2 and budget.corrections_spent == 1 and budget.native_calls == 1
    assert budget.pending is None and budget.verification_spent_ns > 0
    assert budget.verification_reserved_ns == budget.verification_settled_ns
    assert len(list(f.state.results.glob("*.json"))) == 1 and not list(f.state.failures.glob("*.json"))
    result = json.loads(summary.result_path.read_bytes())
    assert result["evidence"][0]["subject_sha"] == second.data["binding"]["candidate_sha"] == summary.head_sha
    assert result["evidence"][0]["evidence_id"] == f.run.run_id + "-V002"
    observation = json.loads((f.state.observations / (f.run.run_id + ".ka01.json")).read_bytes())
    assert observation == summary.ka01 and observation["final_status"] == "RESULT"
    assert len(observation["turns"]) == 2 and observation["source_main_sha"] == f.base
    assert observation["review"] == observation["publication"] == "NOT_OBSERVED"
    original = summary.result_path.read_bytes()
    with pytest.raises(VerificationContractError):
        f.owner.complete(ResultPackage(validate_structural_result(f.package["result"]), ()), f.policy)
    assert summary.result_path.read_bytes() == original and len(f.verification_calls) == 2


def test_fixture_first_candidate_pass_needs_no_feedback_or_second_turn(tmp_path, monkeypatch):
    f = ProductionFixture(tmp_path / "first-pass", monkeypatch, interactive=True)
    f.authorize()
    sessions = fixture_session(f, monkeypatch)
    summary = f.execute()
    assert len(sessions) == 1 and sessions[0].calls == 1 and sessions[0].closed
    assert len(f.verification_calls) == 1 and summary.ka01["checkpoints_spent"] == 1
    assert summary.ka01["corrections_spent"] == 0 and not f.owner._interactive_feedback


@pytest.mark.parametrize("field", ["receipt_id", "feedback_digest", "checkpoint_digest", "source_sha", "previous_turn_id"])
def test_fixture_forged_or_stale_feedback_return_never_reaches_c2_verification(tmp_path, monkeypatch, field):
    f = ProductionFixture(tmp_path / "forged", monkeypatch, interactive=True)
    f.authorize()
    f.output, f.receipt_defect = "incomplete\n", field
    sessions = fixture_session(f, monkeypatch)
    with pytest.raises(VerificationContractError, match="FORGED_DUPLICATE_STALE"):
        f.execute()
    assert len(sessions) == 1 and sessions[0].calls == 2 and len(f.verification_calls) == 1
    assert not list(f.state.results.glob("*.json")) and f.owner._ka01_budget.corrections_spent == 0


@pytest.mark.parametrize("defect", ["same-source", "no-progress", "consent", "profile", "lease", "session",
    "lost-process", "replacement-process", "feedback-file", "checkpoint-file", "source", "crash"])
def test_fixture_loss_or_nonprogress_cannot_resume_or_create_result(tmp_path, monkeypatch, defect):
    f = ProductionFixture(tmp_path / "stops", monkeypatch, interactive=True)
    f.authorize()
    f.output = "incomplete\n"
    f.same_source, f.no_progress = defect == "same-source", defect == "no-progress"
    sessions = fixture_session(f, monkeypatch)
    original = codex_adapter.CodexLiveTransport
    def construct(**kwargs):
        session = original(**kwargs)
        def lose(session, ordinal, prompt):
            if ordinal != 2:
                return
            budget = f.owner._ka01_budget
            if defect == "consent":
                f.issues[1101]["state"] = "closed"
            elif defect == "profile":
                budget.native_adapter._execution_profile = replace(f.profile, model="changed")
            elif defect == "lease":
                budget.leases.release(budget.lease)
                budget.leases.acquire(f.run)
            elif defect == "session":
                session.thread_id = "replacement-thread"
            elif defect == "lost-process":
                session.closed = True
            elif defect == "replacement-process":
                budget.native_adapter._live_transport = FixtureSession(f, profile=f.profile)
            elif defect == "feedback-file":
                (f.owner._checkpoint_directory() / "n001.feedback.json").write_bytes(b"{}")
            elif defect == "checkpoint-file":
                (f.owner._checkpoint_directory() / "n001.json").write_bytes(b"{}")
            elif defect == "source":
                git(f.remote, "update-ref", "refs/heads/main", f.selector)
            elif defect == "crash":
                raise KeyboardInterrupt()
        session.hook = lose
        return session
    monkeypatch.setattr(codex_adapter, "CodexLiveTransport", construct)
    with pytest.raises((RuntimeError, VerificationContractError, KeyboardInterrupt)):
        f.execute()
    assert len(sessions) == 1 and sessions[0].closed and sessions[0].calls == 2
    assert len(f.verification_calls) <= 2 and not list(f.state.results.glob("*.json"))
    budget = f.owner._ka01_budget
    assert budget.stopped
    with pytest.raises(VerificationContractError):
        budget.guard(live=False)
    with pytest.raises(VerificationContractError):
        budget.open_native_session(lambda: pytest.fail("STOP must not spawn a replacement process"))
    observed = json.loads((f.state.observations / (f.run.run_id + ".ka01.json")).read_bytes())
    assert observed["final_status"] == "STOP" and observed["checkpoints_spent"] <= 2


def test_fixture_default_allows_two_improving_corrections_and_three_checkpoints(tmp_path, monkeypatch):
    proof = ("from pathlib import Path\n"
        "def test_started():\n    assert Path('OUTPUT.txt').read_text().startswith('complete')\n"
        "def test_newline():\n    assert Path('OUTPUT.txt').read_text().endswith('\\n')\n")
    f = ProductionFixture(tmp_path / "three-checkpoints", monkeypatch, interactive=True, proof_source=proof)
    f.authorize()
    f.candidates = ["incomplete", "complete", "complete\n"]
    sessions = fixture_session(f, monkeypatch)
    summary = f.execute()
    assert sessions[0].calls == len(f.verification_calls) == summary.ka01["checkpoints_spent"] == 3
    assert summary.ka01["corrections_spent"] == 2
    assert [len(checkpoint(f, i).data["failed_identities"]) for i in (1, 2, 3)] == [2, 1, 0]


def test_fixture_duplicate_feedback_consumption_is_sticky_stop(tmp_path, monkeypatch):
    from aios_renew.runtime import RuntimeCompletion
    f = ProductionFixture(tmp_path / "duplicate", monkeypatch, interactive=True)
    f.authorize()
    f.output = "incomplete\n"
    sessions = fixture_session(f, monkeypatch)
    accept = RuntimeCompletion._accept_interactive_correction
    def duplicated(owner, **kwargs):
        accept(owner, **kwargs)
        accept(owner, **kwargs)
    monkeypatch.setattr(RuntimeCompletion, "_accept_interactive_correction", duplicated)
    with pytest.raises(VerificationContractError, match="FORGED_DUPLICATE_STALE"):
        f.execute()
    assert sessions[0].calls == 2 and len(f.verification_calls) == 1
    assert f.owner._ka01_budget.corrections_spent == 1 and not list(f.state.results.glob("*.json"))


def test_fixture_transport_ack_alone_is_never_a_candidate(tmp_path, monkeypatch):
    f = ProductionFixture(tmp_path / "ack", monkeypatch, interactive=True)
    f.authorize()
    sessions = fixture_session(f, monkeypatch)
    actual = FixtureSession.turn
    def ack(self, **kwargs):
        result = actual(self, **kwargs)
        self.last_turn = replace(result, output_json=b'{"status":"ACK"}')
        return self.last_turn
    monkeypatch.setattr(FixtureSession, "turn", ack)
    with pytest.raises(operator.OperatorError, match="INVALID_CANDIDATE_ENVELOPE"):
        f.execute()
    assert sessions[0].calls == 1 and not f.verification_calls and not list(f.state.results.glob("*.json"))


@pytest.mark.parametrize("defect", ["incomplete", "unstable", "unknown-exit", "setup-fail", "missing-native", "toolchain"])
def test_fixture_ineligible_native_proof_stops_before_factual_feedback(tmp_path, monkeypatch, defect):
    f = ProductionFixture(tmp_path / "ineligible", monkeypatch, interactive=True)
    f.authorize()
    f.output = "incomplete\n"
    sessions = fixture_session(f, monkeypatch)
    actual = f.verify
    def corrupt(command, **kwargs):
        completed = actual(command, **kwargs)
        path = Path(kwargs["env"]["AIOS_BP_V4_PLUGIN_OUTPUT"])
        if defect == "missing-native":
            path.unlink()
        elif defect == "unknown-exit":
            completed = subprocess.CompletedProcess(completed.args, 2, completed.stdout, completed.stderr)
        else:
            data = json.loads(path.read_bytes())
            canonical = data["failure_diagnostics"]["canonical"]
            if defect == "incomplete":
                canonical["complete"] = False
            elif defect == "unstable":
                canonical["unstable"] = True
            elif defect == "toolchain":
                data["toolchain"]["installed_distributions_digest"] = "0" * 64
            else:
                next(r for r in canonical["reports"] if r["outcome"] == "FAIL")["phase"] = "setup"
            path.write_bytes(ka01._bytes(data))
        return completed
    with pytest.raises((RuntimeError, ValueError, OSError)):
        f.execute(verification_runner=corrupt)
    assert sessions[0].calls == 1 and not f.owner._interactive_feedback
    assert not list(f.state.results.glob("*.json"))


@pytest.mark.parametrize("limits", [{"max_corrections": 0}, {"max_checkpoints": 1}, {"max_tokens": 1}])
def test_fixture_exhaustion_never_borrows_another_run_or_correction(tmp_path, monkeypatch, limits):
    f = ProductionFixture(tmp_path / "budget", monkeypatch, interactive=True)
    f.authorize([limits])
    f.output = "incomplete\n"
    sessions = fixture_session(f, monkeypatch)
    with pytest.raises(VerificationContractError):
        f.execute()
    assert len(sessions) <= 1 and (not sessions or sessions[0].calls == 1)
    assert len(f.verification_calls) <= 1 and not list(f.state.results.glob("*.json"))


@pytest.mark.parametrize("resource", ["wall", "verification"])
def test_fixture_running_proof_exhaustion_keeps_unknown_reservation_and_stops(tmp_path, monkeypatch, resource):
    f = ProductionFixture(tmp_path / "time-exhaustion", monkeypatch, interactive=True)
    f.authorize()
    sessions = fixture_session(f, monkeypatch)
    def exhaust(f):
        budget = f.owner._ka01_budget
        now, real = ka01.time.monotonic_ns(), ka01.time.time_ns()
        delta = budget.wall_limit_ns if resource == "wall" else budget.verification_limit_ns
        monkeypatch.setattr(ka01.time, "monotonic_ns", lambda: now + delta)
        monkeypatch.setattr(ka01.time, "time_ns", lambda: real + delta)
    f.verification_hook = exhaust
    with pytest.raises(VerificationContractError):
        f.execute()
    budget = f.owner._ka01_budget
    assert sessions[0].calls == 1 and sessions[0].closed and len(f.verification_calls) == 1
    assert budget.stopped and budget.pending is not None
    assert not f.owner._interactive_feedback and not list(f.state.results.glob("*.json"))


@pytest.mark.parametrize("mode", ["old-implementation", "old-consent"])
def test_v3_cannot_inherit_v2_human_selection_or_consent(tmp_path, monkeypatch, mode):
    f = ProductionFixture(tmp_path / "no-upgrade", monkeypatch, interactive=True)
    f.authorize(selection_change=(lambda s: s.update(implementation=ka01.PRODUCTION_IMPLEMENTATION)) if mode == "old-implementation" else None,
        consent_change=(lambda c: c.update(mode=ka01.PRODUCTION_MODE)) if mode == "old-consent" else None)
    sessions = fixture_session(f, monkeypatch)
    with pytest.raises(VerificationContractError):
        f.execute()
    assert not sessions and not f.verification_calls


def test_native_channel_unavailable_is_explicit_blocker_stop(tmp_path, monkeypatch):
    f = ProductionFixture(tmp_path / "unavailable", monkeypatch, interactive=True)
    f.authorize()
    def unavailable(**kwargs):
        raise native.NativeSessionStop("CODEX_LIVE_STOP_NATIVE_BINARY_UNAVAILABLE")
    monkeypatch.setattr(codex_adapter, "CodexLiveTransport", unavailable)
    with pytest.raises(native.NativeSessionStop, match="NATIVE_BINARY_UNAVAILABLE"):
        f.execute()
    observed = json.loads((f.state.observations / (f.run.run_id + ".ka01.json")).read_bytes())
    assert observed["native_feedback_delivery"] == "BLOCKER_NATIVE_SESSION_UNAVAILABLE"
    assert observed["final_status"] == "STOP" and not f.verification_calls


def test_fixture_terminal_contention_cannot_complete_or_deliver_feedback(tmp_path, monkeypatch):
    from aios_renew.runtime import persist_failure
    f = ProductionFixture(tmp_path / "terminal-contention", monkeypatch, interactive=True)
    f.authorize()
    sessions = fixture_session(f, monkeypatch)
    def contend(f):
        persist_failure(f.repo, state=f.state, task=f.task, run=f.run, run_path=f.owner.run_path,
            failure=RuntimeError("competing terminal"), transport=False)
    f.verification_hook = contend
    with pytest.raises(VerificationContractError, match="COMPETING_OR_UNKNOWN_EFFECT"):
        f.execute()
    assert sessions[0].calls == 1 and not list(f.state.results.glob("*.json"))
    assert not f.owner._interactive_feedback and f.owner._ka01_budget.stopped


@pytest.mark.parametrize("method,params", [
    ("model/rerouted", dict(threadId="original-thread", turnId="original-turn", toModel="substitution")),
    ("item/completed", dict(threadId="forged-thread", turnId="original-turn", item={})),
    ("item/completed", dict(threadId="original-thread", turnId="stale-turn", item={})),
    ("thread/environment/disconnected", dict(threadId="original-thread")),
    ("thread/arbitraryUnreviewedMethod", {}),
    ("account/updated", {}),
])
def test_native_protocol_notifications_do_not_grant_identity_or_profile_changes(method, params):
    session = native.CodexLiveTransport.__new__(native.CodexLiveTransport)
    session.thread_id = "original-thread"
    with pytest.raises(native.NativeSessionStop):
        session._notification(dict(method=method, params=params), turn_id="original-turn")


@pytest.mark.parametrize("raw", [b'{"id":1,"id":1}', b'{"id":NaN}', b'[]', b'not JSON'])
def test_native_protocol_rejects_malformed_duplicate_or_untyped_messages(raw):
    with pytest.raises(native.NativeSessionStop):
        native._decode(raw)


def test_native_protocol_duplicate_rpc_response_cannot_ack_another_turn():
    session = native.CodexLiveTransport.__new__(native.CodexLiveTransport)
    session._request_id = 10
    session._send = lambda message: None
    session._next = lambda: dict(id=10, result={})
    with pytest.raises(native.NativeSessionStop, match="REQUEST_RESPONSE_MISMATCH"):
        session._rpc("turn/start", {})


@pytest.mark.parametrize("defect", ["none", "delayed-duplicate", "email", "plan", "logout"])
def test_native_account_updates_require_original_private_identity(defect):
    session = native.CodexLiveTransport.__new__(native.CodexLiveTransport)
    account = dict(requiresOpenaiAuth=True,
        account=dict(type="chatgpt", email="original@example.invalid", planType="pro"))
    notification = dict(method="account/updated", params=dict(authMode="chatgpt", planType="pro"))
    session.thread_id, session.last_turn = "original-thread", None
    session._session_identity = {}
    session._account_snapshot = native._json(account)
    session._account_notification = native._json(notification["params"])
    session._account_update_pending = False
    session._messages, session._deferred = queue.Queue(), []
    session.assert_live = lambda expected: None
    reads = []
    def read(method, params):
        reads.append((method, params))
        if defect == "delayed-duplicate":
            session._deferred.append(notification)
        elif defect in {"email", "plan"}:
            account["account"]["email" if defect == "email" else "planType"] = "changed"
        elif defect == "logout":
            account["account"] = None
        return account
    session._rpc = read
    session._notification(notification)
    assert session._account_update_pending and not reads
    if defect in {"none", "delayed-duplicate"}:
        session.assert_idle()
        assert not session._account_update_pending and not session._deferred
    else:
        with pytest.raises(native.NativeSessionStop, match="ACCOUNT"):
            session.assert_idle()
    assert reads == [("account/read", {"refreshToken": False})]


@pytest.mark.parametrize("params", [dict(authMode="apikey", planType="pro"),
    dict(authMode=None, planType=None), dict(authMode="chatgpt", planType="changed"), {}])
def test_native_account_notification_cannot_change_bound_auth_mode_or_plan(params):
    session = native.CodexLiveTransport.__new__(native.CodexLiveTransport)
    session.thread_id = "original-thread"
    session._account_notification = native._json(dict(authMode="chatgpt", planType="pro"))
    with pytest.raises(native.NativeSessionStop, match="ORIGINAL_AUTHENTICATED_ACCOUNT_CHANGED"):
        session._notification(dict(method="account/updated", params=params))


def test_installed_native_codex_original_session_c1_f1_c2(tmp_path, monkeypatch):
    """Mandatory live proof; no fixture provider, environment skip or ACK pass.

    Human authentication HTTP remains the source fixture. Native process, model
    output/receipt, implementation commits, verifier, observer and terminal are
    real. Native-unavailable is visibly a BLOCKER and cannot pass this test.
    """
    assert codex_adapter.CodexLiveTransport is native.CodexLiveTransport
    f = ProductionFixture(tmp_path / "installed-native", monkeypatch, interactive=True, first_fail=True)
    f.authorize()
    def forbidden_exec(*args, **kwargs):
        pytest.fail("BLOCKER: interactive PRIMARY attempted one-shot codex exec")
    try:
        summary = f.execute(native_runner=forbidden_exec)
    except native.NativeSessionStop as exc:
        pytest.fail("BLOCKER: installed native Codex session unavailable: " + str(exc), pytrace=False)
    budget = f.owner._ka01_budget
    session = budget.session
    assert type(session) is native.CodexLiveTransport and isinstance(session._original_process, subprocess.Popen)
    first, second = checkpoint(f, 1), checkpoint(f, 2)
    assert session._closed and len(session._turns) == 2 and budget.native_calls == 1
    assert len(f.verification_calls) == 2 and len(f.owner._interactive_receipts) == 1
    assert first.data["failed_identities"] and not second.data["failed_identities"]
    a, b = first.data["binding"], second.data["binding"]
    assert a["session"] == b["session"] == budget.session_identity
    assert a["turn"]["turn_id"] != b["turn"]["turn_id"] and a["turn"]["input_sha256"] != b["turn"]["input_sha256"]
    assert a["candidate_sha"] != b["candidate_sha"] == summary.head_sha and a["tree_sha"] != b["tree_sha"]
    assert git(f.repo, "merge-base", "--is-ancestor", a["candidate_sha"], b["candidate_sha"]) == ""
    assert (f.repo / "OUTPUT.txt").read_text() == "complete\n" and git(f.repo, "status", "--porcelain") == ""
    assert len(list(f.state.results.glob("*.json"))) == 1 and not list(f.state.failures.glob("*.json"))
    assert summary.ka01["checkpoints_spent"] == 2 and summary.ka01["corrections_spent"] == 1
    assert 0 < summary.ka01["verification_spent_ns"] < 180 * 60 * 1_000_000_000
    assert summary.ka01["final_status"] == "RESULT" and summary.ka01["source_main_sha"] == f.base
