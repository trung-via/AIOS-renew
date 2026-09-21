"""Suite-wide isolation for AIOS process-control environment."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def isolate_runtime_restart_marker() -> None:
    """Do not let the parent Runtime's restart state select an in-test path."""

    marker = "AIOS_RESTART_ATTEMPTED"
    previous = os.environ.pop(marker, None)
    try:
        yield
    finally:
        if previous is not None:
            os.environ[marker] = previous
