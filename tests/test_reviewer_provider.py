"""Fresh-fixture BP6-P4 one-call and native-adapter contract examples."""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from aios_renew.reviewer_provider import (
    JsonReviewerProvider, MappingReviewerProvider, ReviewerAttemptError,
    ReviewerProviderInvocation, ReviewerProviderResponseError, attempt,
)
from test_reviewer_provider_protocol import body, finding, material, packet, prior, scope
from aios_renew.reviewer_procedure import select_reviewer_procedure
from aios_renew.reviewer_return_contract import select_reviewer_return_contract


ROOT = Path(__file__).resolve().parents[1]


def inputs(mode="PRIMARY"):
    """Construct new mutable packet, scope, packages, bindings and prior each call."""
    selected_scope = scope(mode)
    procedure = select_reviewer_procedure(
        (ROOT / ".ai/reviewer-procedure-profiles.yaml").read_bytes(), mode)
    returns = select_reviewer_return_contract(
        (ROOT / ".ai/reviewer-return-contracts.yaml").read_bytes())
    bindings = {"review_id": "REVIEW-188-001",
                "finding_id_slots": [f"F-{i:02d}" for i in range(32)]}
    return (packet(), selected_scope, material(selected_scope), procedure, returns,
            bindings, prior() if mode == "DELTA" else None)


def run(provider, parts):
    return attempt(provider, *parts[:-1], prior_review=parts[-1])


def attribution(provider="map", model="m", session=None, invocation=None):
    return {"provider": provider, "model": model, "session_id": session,
            "invocation_id": invocation}


def semantic(request, content=None):
    return {"request_fingerprint": request["request_fingerprint"],
            "semantic_body": body() if content is None else content}


@pytest.mark.parametrize("mode,content,verdict", [
    ("PRIMARY", body(), "PASS"),
    ("PRIMARY", body("CHANGES_REQUIRED", fail=("AC1",), findings=(finding(),)),
     "CHANGES_REQUIRED"),
    ("PRIMARY", body("BLOCKED"), "BLOCKED"),
    ("DELTA", body(delta=True), "PASS"),
])
def test_one_call_success_and_transient_decision(mode, content, verdict):
    calls = []

    def native(request):
        calls.append(deepcopy(request))
        return {"semantic_response": semantic(request, content),
                "attribution": attribution(session="s", invocation="i")}

    made = run(MappingReviewerProvider("map", "m", native), inputs(mode))
    assert len(calls) == 1
    assert made.decision["format"] == "AIOS_REVIEW_DECISION"
    assert made.decision["review_candidate"]["verdict"] == verdict
    assert made.decision["request_fingerprint"] == calls[0]["request_fingerprint"]
    assert set(made.__slots__) == {"decision", "attribution"}
    with pytest.raises(TypeError):
        made.attribution["session_id"] = "changed"


@pytest.mark.parametrize("change", [
    lambda p: p[1].__setitem__("reviewed_head_sha", "x" * 40),
    lambda p: p[5].__setitem__("review_id", ""),
    lambda p: p.__setitem__(0, object()),
])
def test_invalid_caller_input_never_invokes(change):
    parts = list(inputs())
    change(parts)
    calls = []
    provider = MappingReviewerProvider("map", "m", lambda request: calls.append(request))
    with pytest.raises(ReviewerAttemptError) as caught:
        run(provider, parts)
    assert (caught.value.reason_code, caught.value.phase, caught.value.invocation_count) == (
        "PROTOCOL_INPUT_INVALID", "INPUT", 0)
    assert calls == []


@pytest.mark.parametrize("native,reason", [
    (lambda request: (_ for _ in ()).throw(OSError("offline")), "PROVIDER_TRANSPORT_FAILURE"),
    (lambda request: {"semantic_response": semantic(request),
                      "attribution": attribution(), "extra": 1}, "PROVIDER_RESPONSE_INVALID"),
    (lambda request: {"semantic_response": {"request_fingerprint": "0" * 64,
                                                   "semantic_body": body()},
                      "attribution": attribution()}, "PROVIDER_RESPONSE_INVALID"),
    (lambda request: {"semantic_response": semantic(request),
                      "attribution": attribution(provider="other")},
     "PROVIDER_ATTRIBUTION_MISMATCH"),
])
def test_failure_families_stop_after_one_call(native, reason):
    calls = []

    def counted(request):
        calls.append(1)
        return native(request)

    with pytest.raises(ReviewerAttemptError) as caught:
        run(MappingReviewerProvider("map", "m", counted), inputs())
    assert caught.value.reason_code == reason
    assert caught.value.invocation_count == len(calls) == 1
    assert not hasattr(caught.value, "decision")


def test_wrong_invocation_object_and_identity_drift():
    class Selected:
        provider = "map"
        model = "m"

        def __init__(self, response):
            self.response = response
            self.calls = 0

        def invoke(self, request):
            self.calls += 1
            if self.response == "drift":
                self.model = "changed"
            return self.response

    for value, reason in ((object(), "PROVIDER_RESPONSE_INVALID"),
                          ("drift", "PROVIDER_ATTRIBUTION_MISMATCH")):
        selected = Selected(value)
        with pytest.raises(ReviewerAttemptError) as caught:
            run(selected, inputs())
        assert (caught.value.reason_code, caught.value.invocation_count, selected.calls) == (
            reason, 1, 1)


def test_identity_drift_before_call_is_zero_call_mismatch():
    class Changing:
        model = "m"
        calls = 0

        def __init__(self):
            self.reads = 0

        @property
        def provider(self):
            self.reads += 1
            return "map" if self.reads == 1 else "changed"

        def invoke(self, request):
            self.calls += 1

    selected = Changing()
    with pytest.raises(ReviewerAttemptError) as caught:
        run(selected, inputs())
    assert (caught.value.reason_code, caught.value.invocation_count, selected.calls) == (
        "PROVIDER_ATTRIBUTION_MISMATCH", 0, 0)


def test_adapters_preserve_semantic_identity_without_session_input():
    seen = []

    def mapping_native(request):
        seen.append(deepcopy(request))
        return {"semantic_response": semantic(request),
                "attribution": attribution("map", "m1", "session-a", "call-a")}

    def json_native(encoded):
        request = json.loads(encoded)
        seen.append(request)
        return json.dumps({"semantic_response": semantic(request),
                           "attribution": attribution("json", "m2", "session-b", "call-b")})

    first = run(MappingReviewerProvider("map", "m1", mapping_native), inputs())
    second = run(JsonReviewerProvider("json", "m2", json_native), inputs())
    assert len(seen) == 2 and seen[0] == seen[1]
    assert first.decision["request_fingerprint"] == second.decision["request_fingerprint"]
    assert first.decision["decision_fingerprint"] == second.decision["decision_fingerprint"]
    for request in seen:
        assert set(request) == {"format", "version", "kind", "decision_packet",
                                "review_scope", "review_material_package",
                                "reviewer_procedure_package", "reviewer_return_contract_package",
                                "prior_review", "external_bindings", "request_fingerprint"}
        assert not any(key in request for key in ("provider", "model", "session_id",
                                                   "invocation_id", "history", "continuation"))
    assert first.attribution != second.attribution


@pytest.mark.parametrize("wrapper", [
    None, [], {"semantic_response": {}, "attribution": attribution(), "extra": 1},
    {"semantic_response": [], "attribution": attribution()},
    {"semantic_response": {}, "attribution": {**attribution(), "extra": 1}},
    {"semantic_response": {}, "attribution": attribution(session="x" * 2049)},
    {"semantic_response": {}, "attribution": attribution(provider="x" * 513)},
    {"semantic_response": {"bad": float("nan")}, "attribution": attribution()},
    {"semantic_response": {"bad": object()}, "attribution": attribution()},
    {"semantic_response": {"bad": "\ud800"}, "attribution": attribution()},
    {"semantic_response": {"bad": [[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]},
     "attribution": attribution()},
    {"semantic_response": {"large": "x" * 262144}, "attribution": attribution()},
])
def test_mapping_native_rejects_malformed_and_oversized(wrapper):
    provider = MappingReviewerProvider("map", "m", lambda request: deepcopy(wrapper))
    with pytest.raises(ReviewerProviderResponseError):
        provider.invoke({"format": "AIOS_REVIEW_REQUEST"})


@pytest.mark.parametrize("native", [
    b"\xff", "\ud800", b"{}", b"[]", b"NaN",
    b'{"semantic_response":{},"semantic_response":{},"attribution":{}}',
    b'{"semantic_response":{},"attribution":{"provider":"map","model":"m",'
    b'"session_id":null,"invocation_id":null,"model":"m"}}',
    b'{"semantic_response":{"x":Infinity},"attribution":{}}',
    b'{"semantic_response":{"x":1e999},"attribution":{}}',
    b"x" * 262145,
])
def test_json_native_rejects_malformed_duplicate_utf8_and_bounds(native):
    provider = JsonReviewerProvider("map", "m", lambda request: native)
    with pytest.raises(ReviewerProviderResponseError):
        provider.invoke({"format": "AIOS_REVIEW_REQUEST"})


def test_complete_attribution_ceiling_in_both_adapters():
    overly_escaped = attribution(session="\u0000" * 2048, invocation="\u0000" * 2048)
    mapping = MappingReviewerProvider("map", "m", lambda request: {
        "semantic_response": {}, "attribution": overly_escaped})
    encoded = json.dumps({"semantic_response": {}, "attribution": overly_escaped})
    as_json = JsonReviewerProvider("map", "m", lambda request: encoded)
    for provider in (mapping, as_json):
        with pytest.raises(ReviewerProviderResponseError):
            provider.invoke({"request_fingerprint": "x"})


def test_adapter_identity_frozen_and_no_raw_payload_retention():
    for provider in (
        MappingReviewerProvider("map", "m", lambda request: {
            "semantic_response": {}, "attribution": attribution()}),
        JsonReviewerProvider("map", "m", lambda request: json.dumps({
            "semantic_response": {}, "attribution": attribution()})),
    ):
        with pytest.raises(FrozenInstanceError):
            provider.model = "changed"
        extracted = provider.invoke({"request_fingerprint": "x"})
        assert type(extracted) is ReviewerProviderInvocation
        assert set(extracted.semantic_response) == set()
        assert not any(key in provider.__slots__ for key in ("native", "raw", "history", "session"))


def test_no_forbidden_import_or_second_call_authority():
    source = (ROOT / "src/aios_renew/reviewer_provider.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not imports & {
        "brain_provider", "brain_provider_protocol", "brain_audit", "codex_adapter",
        "antigravity_adapter", "antigravity_minimax_adapter", "execution_profile",
        "authoring_ingress", "operator", "runtime", "publication",
    }
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in {"open", "exec", "eval"} for node in ast.walk(tree))
    attempt_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "attempt")
    assert sum(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "invoke" for node in ast.walk(attempt_node)) == 1
