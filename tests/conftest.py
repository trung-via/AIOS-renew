"""Suite-wide isolation for AIOS process-control environment."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def isolate_runtime_restart_marker() -> None:
    """Isolate parent Runtime state and suppress optional Git index writes."""

    marker = "AIOS_RESTART_ATTEMPTED"
    optional_locks = "GIT_OPTIONAL_LOCKS"
    previous_marker = os.environ.pop(marker, None)
    previous_optional_locks = os.environ.get(optional_locks)
    # Read-only Git commands otherwise refresh and rewrite thousands of tiny
    # fixture indexes. Required ref/index locks remain enabled by Git.
    os.environ[optional_locks] = "0"
    try:
        yield
    finally:
        if previous_marker is not None:
            os.environ[marker] = previous_marker
        if previous_optional_locks is None:
            os.environ.pop(optional_locks, None)
        else:
            os.environ[optional_locks] = previous_optional_locks
