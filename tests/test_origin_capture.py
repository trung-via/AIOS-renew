"""Synthetic offline cases only: no live provider identity or canonical mutation."""

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from aios_renew.origin_capture import (
    HANDLE_PREFIX, MAX_METADATA_FIELDS, MAX_METADATA_KEY_CHARS, MAX_SESSION_BYTES,
    OPENAI_SESSION_DOMAIN, ORIGIN_HANDLE_CONTRACT, capture_origin,
)

# Deliberately synthetic transport tokens, never captured from a real host.
SYNTHETIC_A = "synthetic-test-origin-A"
SYNTHETIC_B = "synthetic-test-origin-B"


@pytest.fixture
def probe():
    path = Path(__file__).resolve().parents[1] / "scripts" / "aios_origin_capture_probe.py"
    spec = importlib.util.spec_from_file_location("origin_capture_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_repeat_distinct_origins_and_explicit_derivation():
    first = capture_origin({"openai/session": SYNTHETIC_A})
    assert first == capture_origin({"openai/session": SYNTHETIC_A})
    assert first.origin_handle != capture_origin({"openai/session": SYNTHETIC_B}).origin_handle
    assert first.as_dict() == {
        "contract": ORIGIN_HANDLE_CONTRACT,
        "status": "CAPTURED", "reason": "ACCEPTED",
        "origin_handle": HANDLE_PREFIX + hashlib.sha256(
            b"AIOS\x00ORIGIN_HANDLE_V1\x00openai/session\x00" + SYNTHETIC_A.encode("ascii")
        ).hexdigest(),
    }
    assert OPENAI_SESSION_DOMAIN == b"AIOS\x00ORIGIN_HANDLE_V1\x00openai/session\x00"
    assert first.origin_handle != HANDLE_PREFIX + hashlib.sha256(SYNTHETIC_A.encode()).hexdigest()
    assert first.origin_handle != HANDLE_PREFIX + hashlib.sha256(
        OPENAI_SESSION_DOMAIN.replace(b"V1", b"V2") + SYNTHETIC_A.encode()
    ).hexdigest()
    assert first.origin_handle != HANDLE_PREFIX + hashlib.sha256(
        OPENAI_SESSION_DOMAIN.replace(b"openai/session", b"other/session") + SYNTHETIC_A.encode()
    ).hexdigest()


def test_exact_value_is_not_normalized_or_interpreted():
    values = [SYNTHETIC_A, SYNTHETIC_A.lower(), SYNTHETIC_A + "!",
              "synthetic%2Ftoken", "synthetic/token"]
    handles = [capture_origin({"openai/session": item}).origin_handle for item in values]
    assert None not in handles
    assert len(set(handles)) == len(values)
    assert capture_origin({"openai/session": " " + SYNTHETIC_A}).status == "UNPROVED"


@pytest.mark.parametrize("metadata", [
    None, {}, [], "synthetic metadata text", 1, False,
    {"openai/session": None}, {"openai/session": 7}, {"openai/session": False},
    {"openai/session": []}, {"openai/session": {"id": SYNTHETIC_A}},
    {"openai/session": b"synthetic-bytes"}, {"openai/session": ""},
    {"openai/session": " "}, {"openai/session": "synthetic token"},
    {"openai/session": "synthetic\nvalue"}, {"openai/session": "synthetic\x00value"},
    {"openai/session": "synthetic\x7fvalue"}, {"openai/session": "synthetic\u200bvalue"},
    {"openai/session": "synthetic\ud800value"}, {"openai/session": "synthetic\u00e9value"},
    {"openai/session": "x" * (MAX_SESSION_BYTES + 1)},
    {1: "invalid key", "openai/session": SYNTHETIC_A},
    {"": "invalid key", "openai/session": SYNTHETIC_A},
    {"x" * (MAX_METADATA_KEY_CHARS + 1): None, "openai/session": SYNTHETIC_A},
    {**{str(i): None for i in range(MAX_METADATA_FIELDS)}, "openai/session": SYNTHETIC_A},
])
def test_fail_closed_metadata(metadata, capsys, caplog):
    result = capture_origin(metadata)
    assert result.status == "UNPROVED"
    assert result.origin_handle is None
    assert set(result.as_dict()) == {"contract", "status", "reason", "origin_handle"}
    assert capsys.readouterr() == ("", "")
    assert caplog.records == []


def test_bounds_are_inclusive_and_unrelated_values_are_not_traversed():
    metadata = {str(i): object() for i in range(MAX_METADATA_FIELDS - 1)}
    metadata["openai/session"] = "x" * MAX_SESSION_BYTES
    assert capture_origin(metadata).status == "CAPTURED"
    assert capture_origin({"openai/session": "!"}).status == "CAPTURED"


@pytest.mark.parametrize("alias", [
    "session", "openai/sessionId", "OpenAI/session", "openai/session ",
    "openai/subject", "openai/organization", "openai/widgetSessionId",
    "chat_id", "chat_url", "repository", "default_chat", "model_text", "active_tab",
])
def test_only_exact_field_is_origin(alias):
    assert capture_origin({alias: SYNTHETIC_A}).origin_handle is None
    assert capture_origin({"_meta": {"openai/session": SYNTHETIC_A}}).origin_handle is None


def test_no_environment_or_surrounding_metadata_fallback(monkeypatch, probe):
    for key in ["AIOS_LOCAL_CHAT_WAKE_CONFIG", "AIOS_REPOSITORY", "AIOS_DEFAULT_CHAT",
                "OPENAI_SESSION", "CHAT_URL", "ACTIVE_TAB", "MODEL_TEXT"]:
        monkeypatch.setenv(key, SYNTHETIC_A)
    alternatives = {
        "repository": SYNTHETIC_A, "default_chat": SYNTHETIC_A,
        "arguments": {"openai/session": SYNTHETIC_A}, "model_text": SYNTHETIC_A,
        "browser_state": {"active_tab": SYNTHETIC_A},
        "openai/subject": SYNTHETIC_A, "openai/organization": SYNTHETIC_A,
    }
    for metadata in [alternatives, {**alternatives, "openai/session": None}]:
        result = probe.probe_tool_call(probe.TOOL_NAME, {}, host_metadata=metadata)
        assert result["status"] == "UNPROVED"
        assert result["origin_handle"] is None
    # The exact source, when valid, is unaffected by all potential alternatives.
    assert capture_origin({**alternatives, "openai/session": SYNTHETIC_B}) == (
        capture_origin({"openai/session": SYNTHETIC_B})
    )


def test_probe_needs_no_ambient_io_for_identity_or_mutation(probe, monkeypatch):
    import builtins
    import os
    import socket

    def forbidden(*_args, **_kwargs):
        pytest.fail("Origin capture attempted ambient I/O or configuration fallback")

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(os, "getenv", forbidden)
        guard.setattr(os._Environ, "get", forbidden)
        guard.setattr(socket, "create_connection", forbidden)
        assert probe.probe_tool_call(probe.TOOL_NAME, {}, host_metadata=None)["origin_handle"] is None
        assert probe.probe_tool_call(probe.TOOL_NAME, {},
                                    host_metadata={"openai/session": SYNTHETIC_A}) == (
            capture_origin({"openai/session": SYNTHETIC_A}).as_dict()
        )


@pytest.mark.parametrize("arguments", [
    {"openai/session": SYNTHETIC_A}, {"_meta": {"openai/session": SYNTHETIC_A}},
    {"chat_id": SYNTHETIC_A}, {"repository": SYNTHETIC_A},
    {"default_chat": SYNTHETIC_A}, {"browser_state": {"active_tab": SYNTHETIC_A}},
    {"text": SYNTHETIC_A}, "synthetic model text", [], False,
])
def test_probe_rejects_semantic_arguments_even_with_valid_host_origin(probe, arguments):
    result = probe.probe_tool_call(probe.TOOL_NAME, arguments,
                                  host_metadata={"openai/session": SYNTHETIC_B})
    assert result["reason"] == "PROBE_INPUT_INVALID"
    assert result["origin_handle"] is None


def test_probe_passes_host_metadata_to_neutral_result_without_mutation(probe, monkeypatch,
                                                                    tmp_path, capsys, caplog):
    monkeypatch.chdir(tmp_path)
    metadata = {"openai/session": SYNTHETIC_A, "openai/locale": "en"}
    before = dict(metadata)
    seen = []
    def observe(host_metadata):
        seen.append(host_metadata is metadata)
        return capture_origin(host_metadata)
    monkeypatch.setattr(probe, "capture_origin", observe)
    expected = capture_origin(metadata).as_dict()
    assert probe.probe_tool_call(probe.TOOL_NAME, {}, host_metadata=metadata) == expected
    assert probe.probe_tool_call(probe.TOOL_NAME, None, host_metadata=metadata) == expected
    assert seen == [True, True]
    assert metadata == before
    assert list(tmp_path.iterdir()) == []
    output = json.dumps(expected) + repr(capture_origin(metadata))
    assert SYNTHETIC_A not in output
    assert "openai/session" not in output
    assert capsys.readouterr() == ("", "")
    assert caplog.records == []
    assert probe.probe_tool_call("other-tool", {}, host_metadata=metadata)["origin_handle"] is None


@pytest.mark.parametrize("line", [
    b'{"_meta":{"openai/session":"synthetic-A","openai/session":"synthetic-B"}}',
    b'{"params":{},"params":{"_meta":{"openai/session":"synthetic-A"}}}',
    b'{"_meta":{"openai/session":NaN}}', b'[]', b'bad JSON', b'\xff',
])
def test_wire_rejects_ambiguity_without_echo(probe, line):
    with pytest.raises(ValueError, match="^PROBE_REQUEST_INVALID$"):
        probe.validate_wire_request(line)


def test_wire_bound_and_valid_host_metadata(probe):
    line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": probe.TOOL_NAME, "arguments": {},
                                  "_meta": {"openai/session": SYNTHETIC_A}}}).encode()
    assert probe.validate_wire_request(line) == line.decode()
    with pytest.raises(ValueError, match="^PROBE_REQUEST_INVALID$"):
        probe.validate_wire_request(b" " * (probe.MAX_REQUEST_BYTES + 1))


def test_probe_process_failure_does_not_emit_exception_input(probe, monkeypatch, capsys):
    import logging

    async def fail():
        raise ValueError(SYNTHETIC_A)
    monkeypatch.setattr(probe, "serve_stdio", fail)
    previous_level = logging.root.manager.disable
    try:
        assert probe.main() == 1
        assert logging.root.manager.disable == logging.CRITICAL
        assert capsys.readouterr() == ("", "")
    finally:
        logging.disable(previous_level)


def test_mcp_registration_and_actual_request_context_adapter(probe, tmp_path):
    # The optional SDK runs the real stdio entry, still entirely offline.
    pytest.importorskip("mcp")
    import anyio
    import os
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def exercise():
        params = StdioServerParameters(
            command=sys.executable, args=[probe.__file__], cwd=str(tmp_path),
            env={"PYTHONPATH": os.pathsep.join(str(Path(p).resolve()) for p in sys.path if p),
                 "PYTHONDONTWRITEBYTECODE": "1"},
        )
        with anyio.fail_after(15):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    assert len(listed.tools) == 1
                    tool = listed.tools[0]
                    assert tool.name == probe.TOOL_NAME
                    assert tool.inputSchema == probe.INPUT_SCHEMA
                    assert tool.annotations.readOnlyHint is True
                    assert tool.annotations.destructiveHint is False
                    assert tool.annotations.idempotentHint is True
                    assert tool.annotations.openWorldHint is False
                    for source in [SYNTHETIC_A, SYNTHETIC_A, SYNTHETIC_B]:
                        metadata = {"openai/session": source, "openai/locale": "en"}
                        response = await session.call_tool(probe.TOOL_NAME, {}, meta=metadata)
                        expected = capture_origin(metadata).as_dict()
                        assert response.structuredContent == expected
                        assert json.loads(response.content[0].text) == expected
                        assert SYNTHETIC_A not in response.model_dump_json()
                        assert SYNTHETIC_B not in response.model_dump_json()
                    missing = await session.call_tool(probe.TOOL_NAME, {})
                    assert missing.structuredContent["status"] == "UNPROVED"
                    assert missing.structuredContent["origin_handle"] is None
    asyncio.run(exercise())
    assert list(tmp_path.iterdir()) == []
