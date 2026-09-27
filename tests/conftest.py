"""Suite-wide isolation for AIOS process-control environment."""

from __future__ import annotations

import hashlib
import os

import pytest


_MAX_UNCHANGED_PARAMETER_LENGTH = 512
_MAX_NODEID_LENGTH = 8192


def pytest_make_parametrize_id(
    config: pytest.Config, val: object, argname: str
) -> str | None:
    """Keep pytest's usual ids except for oversized text and byte values."""

    if isinstance(val, str):
        if len(val) <= _MAX_UNCHANGED_PARAMETER_LENGTH:
            return None
        kind = "str"
        content = val.encode("utf-8", errors="surrogatepass")
    elif isinstance(val, bytes):
        if len(val) <= _MAX_UNCHANGED_PARAMETER_LENGTH:
            return None
        kind = "bytes"
        content = val
    else:
        return None

    return f"{kind}-len{len(val)}-sha256-{hashlib.sha256(content).hexdigest()}"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Reject final nodeids too long for the current-test environment value."""

    for index, item in enumerate(items):
        nodeid = item.nodeid
        if len(nodeid) > _MAX_NODEID_LENGTH:
            digest = hashlib.sha256(
                nodeid.encode("utf-8", errors="surrogatepass")
            ).hexdigest()
            raise pytest.UsageError(
                "collected nodeid exceeds 8192 characters: "
                f"item index {index}, length {len(nodeid)}, sha256 {digest}"
            )


@pytest.fixture(scope="session", autouse=True)
def isolate_runtime_restart_marker() -> None:
    """Isolate parent Runtime state and trim per-process Git startup work."""

    marker = "AIOS_RESTART_ATTEMPTED"
    previous_marker = os.environ.pop(marker, None)
    git_environment = {
        "GIT_OPTIONAL_LOCKS": "0",
        # System attributes are not part of the test fixtures.  Keep ordinary
        # system/global config resolution intact: historical and temporary
        # repositories may rely on it for Git identity and other semantics.
        "GIT_ATTR_NOSYSTEM": "1",
    }
    previous_git_environment = {
        name: os.environ.get(name) for name in git_environment
    }
    # Read-only Git commands otherwise refresh and rewrite thousands of tiny
    # fixture indexes. Required ref/index locks remain enabled by Git.
    os.environ.update(git_environment)
    try:
        yield
    finally:
        if previous_marker is not None:
            os.environ[marker] = previous_marker
        for name, previous in previous_git_environment.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
