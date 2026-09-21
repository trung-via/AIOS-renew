"""Suite-wide isolation for AIOS process-control environment."""

from __future__ import annotations

import os

import pytest


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
