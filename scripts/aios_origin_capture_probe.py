"""Dedicated read-only development MCP probe (stdio, for Secure MCP Tunnel).

No AIOS operator/worker entry point is invoked. Only the MCP host supplies origin
metadata. Importing this module does not start a server or access canonical state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

from aios_renew.origin_capture import capture_origin, unproved

TOOL_NAME = "aios_origin_capture"
MAX_REQUEST_BYTES = 65536
INPUT_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "contract": {"type": "string", "const": "ORIGIN_HANDLE_V1"},
        "status": {"type": "string", "enum": ["CAPTURED", "UNPROVED"]},
        "reason": {"type": "string", "enum": [
            "ACCEPTED", "METADATA_ABSENT", "METADATA_INVALID", "ORIGIN_INPUT_ABSENT",
            "ORIGIN_INPUT_INVALID", "ORIGIN_INPUT_OVER_BOUND", "PROBE_INPUT_INVALID",
        ]},
        "origin_handle": {"anyOf": [
            {"type": "null"},
            {"type": "string", "pattern": "^origin-v1:[0-9a-f]{64}$"},
        ]},
    },
    "required": ["contract", "status", "reason", "origin_handle"],
    "additionalProperties": False,
}


def probe_tool_call(name: str, arguments: object, *, host_metadata: object) -> dict:
    """Offline-injectable adapter; semantic/caller identity arguments are rejected."""
    if name != TOOL_NAME or (arguments is not None and
                             (type(arguments) is not dict or arguments)):
        return unproved("PROBE_INPUT_INVALID").as_dict()
    return capture_origin(host_metadata).as_dict()


def validate_wire_request(line: bytes) -> str:
    """Reject ambiguous JSON and oversized input before SDK parsing/logging.

    Rejected wire input closes the probe connection; no value or exception text
    is sent back. A JSON object cannot represent duplicate origin keys safely.
    """
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("PROBE_REQUEST_INVALID")
            result[key] = value
        return result

    def reject_constant(_value):
        raise ValueError("PROBE_REQUEST_INVALID")

    try:
        if not line or len(line) > MAX_REQUEST_BYTES:
            raise ValueError
        text = line.decode("utf-8", errors="strict")
        request = json.loads(text, object_pairs_hook=unique_object,
                             parse_constant=reject_constant)
        if type(request) is not dict:
            raise ValueError
        return text
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("PROBE_REQUEST_INVALID") from None


class _BoundedStdin:
    """Only one bounded request line is resident at the stdio ingress."""

    async def __aiter__(self):
        import anyio

        while True:
            line = await anyio.to_thread.run_sync(
                lambda: sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1),
                abandon_on_cancel=True,
            )
            if not line:
                return
            yield validate_wire_request(line)


def build_server():
    """Register exactly one tool; the MCP SDK is an optional development extra."""
    from mcp.server.lowlevel import Server
    from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

    server = Server("aios-origin-capture-probe", version="1.0.0")

    @server.list_tools()
    async def list_tools():
        return [Tool(
            name=TOOL_NAME,
            description=("Read-only development origin capture from host tool-call "
                         "metadata. Call with {}. Returns an opaque handle or "
                         "UNPROVED; grants no route or lifecycle authority."),
            inputSchema=INPUT_SCHEMA,
            outputSchema=OUTPUT_SCHEMA,
            annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                        idempotentHint=True, openWorldHint=False),
        )]

    # Own argument rejection so SDK validation errors cannot echo caller content.
    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        try:
            meta = server.request_context.meta
            host_metadata = None if meta is None else meta.model_dump(
                by_alias=True, exclude_unset=True,
            )
            payload = probe_tool_call(name, arguments, host_metadata=host_metadata)
        except Exception:
            payload = unproved("METADATA_INVALID").as_dict()
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, sort_keys=True))],
            structuredContent=payload,
            isError=payload["status"] != "CAPTURED",
        )

    return server


async def serve_stdio():
    from mcp.server.stdio import stdio_server

    server = build_server()
    async with stdio_server(stdin=_BoundedStdin()) as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> int:
    # Dedicated probe process: no SDK/debug/request logging of provider metadata.
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(serve_stdio())
    except KeyboardInterrupt:
        return 0
    except Exception:
        # Never emit tracebacks, exception text, request bodies or source metadata.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
