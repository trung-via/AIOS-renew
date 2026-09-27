"""Offline BP8-P1 proof of the opt-in Gemini Reviewer transport boundary."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from aios_renew.gemini_reviewer_transport import (
    GeminiReviewerTransport, GeminiReviewerTransportError, MODEL, PROVIDER,
    REQUEST_BYTES,
)
from aios_renew.reviewer_provider import (
    JsonReviewerProvider, ReviewerAttemptError, attempt,
)
from test_reviewer_provider import inputs
from test_reviewer_provider_protocol import body


ROOT = Path(__file__).resolve().parents[1]
USAGE = {"input_tokens": 120, "output_tokens": 30, "cached_input_tokens": 20}


def envelope(semantic=None, **changes):
    value = {"status": "SUCCESS", "structured_output": semantic or {
        "request_fingerprint": "f" * 64, "semantic_body": body(),
    }, "usage": USAGE}
    value.update(changes)
    return json.dumps(value, separators=(",", ":")).encode()


class Runner:
    def __init__(self, response=None, failure=None):
        self.response = response
        self.failure = failure
        self.calls = []
        self.workspaces = []
        self.requests = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        workspace = Path(kwargs["cwd"])
        self.workspaces.append(workspace)
        self.requests.append((workspace / "reviewer_request.json").read_bytes())
        assert (workspace / "response_schema.json").is_file()
        if self.failure is not None:
            raise self.failure
        return subprocess.CompletedProcess(
            command, 0, envelope() if self.response is None else self.response, b"")


def assert_isolated(runner, request):
    assert len(runner.calls) == 1
    command, kwargs = runner.calls[0]
    assert runner.requests == [request]
    assert all(not path.exists() for path in runner.workspaces)
    assert command.count("agy") == 1
    assert command == (
        "agy", "--print", command[2], "--add-dir", kwargs["cwd"],
        "--model", MODEL, "--effort", "medium",
        "--disable-slash-commands", "--output-format", "json",
        "--json-schema", str(Path(kwargs["cwd"]) / "response_schema.json"),
        "--print-timeout", "300s",
    )
    assert "--mode" not in command
    assert "plan" not in command
    assert command[command.index("--json-schema") + 1].startswith(kwargs["cwd"])
    assert kwargs["timeout"] == 300
    assert kwargs["capture_output"] is True and kwargs["text"] is False
    assert kwargs["check"] is False and "env" not in kwargs
    rendered = " ".join(command)
    assert str(ROOT) not in rendered
    assert request.decode() not in rendered
    assert "accept-edits" not in rendered
    assert "dangerously-skip-permissions" not in rendered
    assert "git" not in rendered.lower().replace("do not discover or mutate any repository, git, github", "")


def test_maximum_request_is_exact_file_delivery_with_bounded_command():
    request = b'"' + b'x' * (REQUEST_BYTES - 2) + b'"'
    runner = Runner()
    transport = GeminiReviewerTransport(effort="medium", runner=runner)
    wrapper = json.loads(transport(request))
    assert_isolated(runner, request)
    command = runner.calls[0][0]
    assert sum(len(part) for part in command) < 2000
    assert wrapper["attribution"] == {
        "provider": PROVIDER, "model": MODEL,
        "session_id": None, "invocation_id": None,
    }
    observation = transport.last_observation
    assert observation.request_sha256 == hashlib.sha256(request).hexdigest()
    assert (observation.provider, observation.model, observation.effort,
            observation.invocation_count) == (PROVIDER, MODEL, "medium", 1)
    assert (observation.native_usage.input_tokens,
            observation.native_usage.output_tokens,
            observation.native_usage.cached_input_tokens) == (120, 30, 20)
    with pytest.raises(FrozenInstanceError):
        observation.model = "other"
    assert not any(hasattr(observation, name) for name in (
        "request", "response", "credentials", "environment", "reasoning", "evidence"))


@pytest.mark.parametrize("bad", [None, "", "default", "xhigh", 3])
def test_effort_is_explicit_and_invalid_effort_has_no_call(bad):
    runner = Runner()
    with pytest.raises((TypeError, ValueError)):
        if bad is None:
            GeminiReviewerTransport(runner=runner)
        else:
            GeminiReviewerTransport(effort=bad, runner=runner)
    assert runner.calls == []


def test_invalid_request_bound_and_encoding_do_not_call_native():
    runner = Runner()
    transport = GeminiReviewerTransport(effort="medium", runner=runner)
    for request in (b"x" * (REQUEST_BYTES + 1), b"\xff", "not bytes"):
        with pytest.raises(GeminiReviewerTransportError):
            transport(request)
    assert runner.calls == []


@pytest.mark.parametrize("response,failure", [
    (b"", None), (b"\xff", None), (b"[]", None),
    (b'{"status":"SUCCESS","status":"SUCCESS"}', None),
    (b'{"status":"SUCCESS","usage":{"input_tokens":1,"input_tokens":2}}', None),
    (b'{"status":"SUCCESS","usage":{"input_tokens":1e999}}', None),
    (b'{"status":"SUCCESS","structured_output":{},"usage":{}}', None),
    (envelope(status="FAILURE"), None),
    (envelope(structured_output=None), None),
    (envelope(usage=None), None),
    (envelope(usage={**USAGE, "output_tokens": -1}), None),
    (envelope(usage={**USAGE, "cached_input_tokens": 121}), None),
    (envelope(usage={**USAGE, "cache_read_tokens": 19}), None),
    (b"x" * 262145, None),
    (None, subprocess.TimeoutExpired("agy", 300)),
    (None, OSError("secret detail must not escape")),
], ids=[
    "empty-output", "invalid-utf8", "non-object", "duplicate-status",
    "duplicate-usage", "nonfinite-usage", "invalid-structured-output",
    "invalid-status", "missing-structured-output", "missing-usage",
    "negative-usage", "excess-cache-usage", "invalid-cache-alias",
    "over-bound-output", "timeout", "runner-error",
])
def test_failure_closes_workspace_and_stops_after_one_call(response, failure):
    runner = Runner(response=response, failure=failure)
    transport = GeminiReviewerTransport(effort="medium", runner=runner)
    request = b'{"exact":"bytes"}'
    with pytest.raises(GeminiReviewerTransportError) as caught:
        transport(request)
    assert "secret detail" not in str(caught.value)
    assert transport.last_observation is None
    assert_isolated(runner, request)


def test_nonzero_exit_and_invalid_semantic_shape_fail_closed():
    class Nonzero(Runner):
        def __call__(self, command, **kwargs):
            super().__call__(command, **kwargs)
            return subprocess.CompletedProcess(command, 7, envelope(), b"secret")

    for runner in (Nonzero(), Runner(envelope(semantic={"unexpected": 1})),
                   Runner(envelope(semantic={
                       "request_fingerprint": "f" * 64,
                       "semantic_body": {"padding": "x" * 196608},
                   }))):
        transport = GeminiReviewerTransport(effort="medium", runner=runner)
        with pytest.raises(GeminiReviewerTransportError):
            transport(b"{}")
        assert_isolated(runner, b"{}")
        assert transport.last_observation is None


def test_native_cache_read_witness_alias_is_normalized():
    runner = Runner(envelope(usage={
        "input_tokens": 120, "output_tokens": 30, "cache_read_tokens": 20,
        "cache_creation_tokens": 4,
    }))
    transport = GeminiReviewerTransport(effort="medium", runner=runner)
    transport(b"{}")
    assert transport.last_observation.native_usage.cached_input_tokens == 20
    assert_isolated(runner, b"{}")


def test_valid_native_output_composes_with_existing_json_provider_and_p3():
    class SemanticRunner(Runner):
        def __call__(self, command, **kwargs):
            request = json.loads((Path(kwargs["cwd"]) / "reviewer_request.json").read_bytes())
            self.response = envelope({
                "request_fingerprint": request["request_fingerprint"],
                "semantic_body": body(),
            })
            return super().__call__(command, **kwargs)

    runner = SemanticRunner()
    transport = GeminiReviewerTransport(effort="medium", runner=runner)
    provider = JsonReviewerProvider(PROVIDER, MODEL, transport)
    parts = inputs()
    result = attempt(provider, *parts[:-1], prior_review=parts[-1])
    assert result.decision["format"] == "AIOS_REVIEW_DECISION"
    assert result.decision["review_candidate"]["verdict"] == "PASS"
    assert result.attribution["provider"] == PROVIDER
    assert result.attribution["model"] == MODEL
    assert result.decision["request_fingerprint"] == json.loads(runner.requests[0])["request_fingerprint"]
    assert_isolated(runner, runner.requests[0])

    def other_native(raw):
        request = json.loads(raw)
        return json.dumps({
            "semantic_response": {"request_fingerprint": request["request_fingerprint"],
                                  "semantic_body": body()},
            "attribution": {"provider": "other", "model": "other-model",
                            "session_id": None, "invocation_id": None},
        })

    other_parts = inputs()
    other = attempt(JsonReviewerProvider("other", "other-model", other_native),
                    *other_parts[:-1], prior_review=other_parts[-1])
    assert other.decision["request_fingerprint"] == result.decision["request_fingerprint"]
    assert other.decision["decision_fingerprint"] == result.decision["decision_fingerprint"]
    assert other.attribution != result.attribution


def test_invalid_p3_response_stops_at_existing_pre_review_boundary():
    class InvalidSemanticRunner(Runner):
        def __call__(self, command, **kwargs):
            request = json.loads((Path(kwargs["cwd"]) / "reviewer_request.json").read_bytes())
            invalid_body = {**body(), "verdict": "UNREVIEWED"}
            self.response = envelope({
                "request_fingerprint": request["request_fingerprint"],
                "semantic_body": invalid_body,
            })
            return super().__call__(command, **kwargs)

    runner = InvalidSemanticRunner()
    transport = GeminiReviewerTransport(effort="medium", runner=runner)
    provider = JsonReviewerProvider(PROVIDER, MODEL, transport)
    parts = inputs()
    with pytest.raises(ReviewerAttemptError) as caught:
        attempt(provider, *parts[:-1], prior_review=parts[-1])
    assert (caught.value.phase, caught.value.reason_code,
            caught.value.invocation_count) == ("RESPONSE", "PROVIDER_RESPONSE_INVALID", 1)
    assert len(runner.calls) == 1
    assert all(not path.exists() for path in runner.workspaces)


def test_authority_isolation_and_transport_only_schema():
    source = (ROOT / "src/aios_renew/gemini_reviewer_transport.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not imports & {
        "codex_adapter", "antigravity_adapter", "execution_profile", "runtime",
        "authoring_ingress", "publication", "dispatcher", "operator", "review",
        "reviewer_provider_protocol", "reviewer_provider",
    }
    schema = json.loads((ROOT / "src/aios_renew/schemas/gemini_reviewer_semantic_response.json").read_text())
    assert set(schema["properties"]) == {"request_fingerprint", "semantic_body"}
    assert schema["properties"]["semantic_body"] == {"type": "object"}
    assert "subprocess.run" in source and "JsonReviewerProvider" in source
    assert "git -" not in source and "check_output" not in source
    # This suite supplies every runner; it never starts a provider process.
