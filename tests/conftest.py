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
        # Every fixture has complete repository-local identity/configuration.
        # Avoid re-probing system/global config and attributes for each of the
        # thousands of short-lived, test-only Git processes on Windows.
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
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
