"""Regressions for suite-wide pytest parameter and nodeid bounds."""

from __future__ import annotations

import hashlib
import os
from types import SimpleNamespace

import pytest

from conftest import pytest_collection_modifyitems, pytest_make_parametrize_id


@pytest.mark.parametrize("payload", [b"x" * 262145])
def test_oversized_auto_parametrized_bytes_reach_body(payload, request):
    assert len(payload) > 262144
    expected_id = (
        f"bytes-len{len(payload)}-sha256-{hashlib.sha256(payload).hexdigest()}"
    )
    assert request.node.nodeid.endswith(f"[{expected_id}]")
    current_test = os.environ["PYTEST_CURRENT_TEST"]
    assert current_test.startswith(request.node.nodeid)
    assert current_test.endswith(" (call)")
    assert len(current_test) < 8200


@pytest.mark.parametrize("payload", ["x" * 513])
def test_oversized_auto_parametrized_string_reaches_body(payload, request):
    expected_id = (
        f"str-len{len(payload)}-sha256-"
        f"{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"
    )
    assert request.node.nodeid.endswith(f"[{expected_id}]")


def test_oversized_ids_are_repeatable_and_distinguish_same_length_values():
    first = b"a" * 513
    second = b"b" * 513
    first_id = pytest_make_parametrize_id(None, first, "payload")
    assert first_id == pytest_make_parametrize_id(None, first, "other_argument")
    assert first_id != pytest_make_parametrize_id(None, second, "payload")
    assert first_id is not None
    assert len(first_id) < 100
    assert "a" * 513 not in first_id


def test_short_values_and_opaque_objects_keep_pytest_identity_path():
    class Opaque:
        def __repr__(self):
            raise AssertionError("repr must not be called by the hook")

        def __str__(self):
            raise AssertionError("str must not be called by the hook")

    for value in ("s" * 512, b"b" * 512, Opaque(), 123):
        assert pytest_make_parametrize_id(None, value, "payload") is None


def test_final_nodeid_guard_rejects_explicit_overlong_id_without_leaking_it():
    secret = "sensitive-parameter-value-" * 400
    nodeid = f"tests/test_example.py::test_case[{secret}]"
    ordinary = SimpleNamespace(nodeid="tests/test_example.py::test_other[short]")
    overlong = SimpleNamespace(nodeid=nodeid)
    items = [ordinary, overlong]

    with pytest.raises(pytest.UsageError) as caught:
        pytest_collection_modifyitems(items)

    message = str(caught.value)
    assert len(message) < 200
    assert secret not in message
    assert "sensitive-parameter-value" not in message
    assert f"length {len(nodeid)}" in message
    assert hashlib.sha256(nodeid.encode("utf-8")).hexdigest() in message
    assert items == [ordinary, overlong]
    assert overlong.nodeid == nodeid


def test_final_nodeid_guard_preserves_nodeids_at_and_below_limit():
    boundary = SimpleNamespace(nodeid="n" * 8192)
    short = SimpleNamespace(nodeid="tests/test_example.py::test_case[short]")
    items = [boundary, short]

    pytest_collection_modifyitems(items)

    assert items == [boundary, short]
    assert boundary.nodeid == "n" * 8192
    assert short.nodeid == "tests/test_example.py::test_case[short]"
