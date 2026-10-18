"""Suite-wide isolation for AIOS process-control environment."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import sys

import pytest

from aios_renew.parallel_verification import (
    ProbeError, effective_pytest_profile, installed_git_identity, load_toolchain,
)


_MAX_UNCHANGED_PARAMETER_LENGTH = 512
_MAX_NODEID_LENGTH = 8192


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Enforce the effective population even when addopts is overridden."""
    try:
        load_toolchain()
        if not (config.pluginmanager.hasplugin("xdist") or config.pluginmanager.hasplugin("xdist.plugin")):
            raise ProbeError("pytest requires the active compatible xdist plugin")
        if any(os.environ.get(name) for name in (
            "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")):
            raise ProbeError("pytest inherited shared writable Git repository state")
        expected = effective_pytest_profile(config.invocation_params.args)
        if bool(config.option.collectonly) != expected["collect_only"]:
            raise ProbeError("hidden collection-only override")
        worker = getattr(config, "workerinput", None)
        if worker is None and not expected["collect_only"]:
            if (config.getoption("numprocesses", default=None) != expected["workers"]
                    or config.getoption("dist", default=None) != expected["distribution"]
                    or str(config.getoption("maxworkerrestart", default=None)) != "0"
                    or config.getoption("maxprocesses", default=None) is not None):
                raise ProbeError("actual pytest options disagree with fixed worker profile")
        elif worker is not None and worker.get("workercount") != expected["workers"]:
            raise ProbeError("actual xdist worker population disagrees with profile")
        config._aios_execution_profile = expected
        identity = installed_git_identity()
        config._aios_git_identity = identity
        if os.name == "nt":
            previous = os.environ.get("PATH")
            os.environ["PATH"] = str(Path(identity["git_executable"]).parent) + os.pathsep + (previous or "")
            def restore_path():
                if previous is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = previous
            config.add_cleanup(restore_path)
        # Always observe controller completion/crashes, including ordinary CLI.
        # Keep the top-level module separate from tests' unit-hook instances.
        name = "bp_v4_probe_plugin"
        path = Path(__file__).with_name(name + ".py").resolve()
        plugin = config.pluginmanager.getplugin(name) or sys.modules.get(name)
        if plugin is not None and Path(plugin.__file__).resolve() != path:
            raise ProbeError("conflicting pytest observer source")
        if not config.pluginmanager.hasplugin(name):
            if plugin is None:
                spec = importlib.util.spec_from_file_location(name, path)
                if spec is None or spec.loader is None:
                    raise ProbeError("missing pytest execution observer")
                plugin = importlib.util.module_from_spec(spec)
                sys.modules[name] = plugin
                spec.loader.exec_module(plugin)
            config.pluginmanager.register(plugin, name)
    except (ProbeError, OSError, ValueError) as exc:
        raise pytest.UsageError(str(exc)) from exc


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
    """Isolate the parent Runtime marker without changing Git semantics."""

    marker = "AIOS_RESTART_ATTEMPTED"
    previous_marker = os.environ.pop(marker, None)
    try:
        yield
    finally:
        if previous_marker is not None:
            os.environ[marker] = previous_marker
