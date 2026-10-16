"""Shared bounded BP-V4 collection, invocation, and conformance primitives."""

from __future__ import annotations

import importlib
import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import struct
import subprocess
import sys
import time
from typing import Any, Callable, Mapping

from .verification_contract import (
    validate_observation, VerificationContractError, MAX_CANONICAL_BYTES,
    pytest_collection_identity,
    toolchain_inventory_digest,
    validate_pytest_execution_binding,
)

PYTEST_RANGE = ((8, 2), (9, 0))
XDIST_RANGE = ((3, 6), (4, 0))
PLUGIN_NAME = "bp_v4_probe_plugin"
PLUGIN_OUTPUT_ENV = "AIOS_BP_V4_PLUGIN_OUTPUT"
MAX_FAILURE_IDENTITIES = 20
MAX_NODEID_DISPLAY_CHARS = 240
FAILURE_PHASES = {"collect", "setup", "call", "teardown"}
EXECUTION_CONTRACT = "fixed-xdist-direct-git-v1"
_git_binding = None

class ProbeError(RuntimeError):
    """Fail-closed verification construction or observation error."""


def installed_git_identity(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """Identify installed Git, selecting the direct Git-for-Windows PE binary.

    PATH is an input to discovery, never an authority to execute an arbitrary
    repository-supplied git.exe. No shim, router, install or host setting is used.
    """
    environment = os.environ if environment is None else environment
    if os.name != "nt":
        executable = shutil.which("git", path=environment.get("PATH", os.defpath))
        if not executable:
            raise ProbeError("installed Git is missing")
        binary = Path(executable).resolve(strict=True)
    else:
        candidates = set()
        for entry in environment.get("PATH", "").split(os.pathsep):
            directory = Path(entry.strip('"'))
            if not directory.is_absolute():
                if (directory / "git.exe").exists():
                    raise ProbeError("untrusted relative Git search path")
                continue
            launcher = directory / "git.exe"
            if not launcher.is_file():
                if any((directory / name).exists() for name in ("git.cmd", "git.bat", "git.com")):
                    raise ProbeError("untrusted Git shell intermediary")
                continue
            directory = directory.resolve(strict=True)
            if any((parent / ".git").exists() for parent in (directory, *directory.parents)):
                raise ProbeError("repository-owned Git executable is untrusted")
            if directory.name.lower() in {"cmd", "bin"}:
                root = directory.parent
                if root.name.lower() in {"mingw64", "mingw32", "mingwarm64"}:
                    root = root.parent
            else:
                raise ProbeError("unrecognized installed Git layout")
            installation = root.resolve(strict=True)
            installed_roots = [Path(value).resolve() for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")
                               if (value := os.environ.get(key))]
            if local := os.environ.get("LOCALAPPDATA"):
                installed_roots.append((Path(local) / "Programs").resolve())
            if not any(installation.is_relative_to(base) for base in installed_roots):
                raise ProbeError("Git executable is outside supported installed program roots")
            direct = [root / arch / "bin" / "git.exe" for arch in ("mingw64", "mingw32", "mingwarm64")
                      if (root / arch / "bin" / "git.exe").is_file()]
            if len(direct) != 1 or not (root / "usr" / "bin" / "sh.exe").is_file():
                raise ProbeError("missing or ambiguous direct installed Git binary")
            binary = direct[0].resolve(strict=True)
            if not binary.is_relative_to(installation):
                raise ProbeError("direct Git executable escapes its installation")
            if not (binary.parent.parent / "libexec" / "git-core").is_dir():
                raise ProbeError("incomplete installed Git layout")
            candidates.add(binary)
        if len(candidates) != 1:
            raise ProbeError("missing or ambiguous installed Git identity")
        binary = candidates.pop()
        # Bare 'git' calls in existing production boundaries use CreateProcess.
        # Reject an executable that would shadow our process-local PATH choice.
        system = Path(environment.get("SystemRoot", os.environ.get("SystemRoot", "C:/Windows")))
        for directory in (Path(sys.executable).resolve().parent, Path.cwd(), system / "System32", system / "System", system):
            shadow = directory / "git.exe"
            if shadow.exists() and shadow.resolve() != binary:
                raise ProbeError("Git executable shadows verified process search path")
        with binary.open("rb") as stream:
            header = stream.read(64)
            if len(header) != 64 or header[:2] != b"MZ":
                raise ProbeError("direct installed Git is not a PE executable")
            stream.seek(struct.unpack_from("<I", header, 60)[0])
            if stream.read(4) != b"PE\0\0":
                raise ProbeError("direct installed Git is not a PE executable")
    before = hashlib.sha256(binary.read_bytes()).hexdigest()
    try:
        version = subprocess.run((str(binary), "--version"), env=environment, capture_output=True, check=False, text=True, encoding="utf-8")
        exec_path = subprocess.run((str(binary), "--exec-path"), env=environment, capture_output=True, check=False, text=True, encoding="utf-8")
    except OSError as exc:
        raise ProbeError(f"installed Git could not start: {exc}") from exc
    if version.returncode or exec_path.returncode or not version.stdout.startswith("git version "):
        raise ProbeError("installed Git identity command failed")
    core = Path(exec_path.stdout.strip()).resolve()
    if not core.is_dir() or (os.name == "nt" and core != (binary.parent.parent / "libexec" / "git-core").resolve()):
        raise ProbeError("conflicting installed Git exec-path")
    if hashlib.sha256(binary.read_bytes()).hexdigest() != before:
        raise ProbeError("installed Git changed during identity observation")
    return {"git_executable": str(binary), "git_executable_sha256": before,
            "git_version": version.stdout.strip(), "git_exec_path": str(core),
            "git_selection": "installed-git-for-windows-direct-v1" if os.name == "nt" else "posix-path-v1"}


def git_executable(environment: Mapping[str, str] | None = None) -> str:
    """Use an absolute direct binary on Windows; retain POSIX command lookup."""
    if os.name != "nt":
        return "git"
    global _git_binding
    environment = os.environ if environment is None else environment
    key = (environment.get("PATH"), environment.get("GIT_EXEC_PATH"))
    if _git_binding is None or _git_binding[0] != key:
        identity = installed_git_identity(environment)
        binary = Path(identity["git_executable"])
        _git_binding = (key, identity, binary.stat())
    _, identity, previous = _git_binding
    current = Path(identity["git_executable"]).stat()
    if (current.st_size, current.st_mtime_ns, current.st_ino) != (previous.st_size, previous.st_mtime_ns, previous.st_ino):
        raise ProbeError("bound Git executable changed")
    return identity["git_executable"]


def pytest_worker_options(arguments) -> dict[str, list[str]]:
    """Read worker controls without interpreting selectors or rewriting argv."""
    values = {key: [] for key in ("workers", "distribution", "restart", "unsupported", "collect")}
    arguments = list(arguments)
    index = 0
    names = {"-n": "workers", "--numprocesses": "workers", "--dist": "distribution",
             "--max-worker-restart": "restart"}
    value_options = {"-k", "-m", "-o", "--override-ini", "-p", "--basetemp", "--deselect",
                     "--ignore", "--ignore-glob", "--confcutdir", "-c", "--rootdir", "--import-mode",
                     "--tb", "--capture", "--junitxml", "--junit-xml", "--color", "--code-highlight",
                     "--durations", "--durations-min", "--maxfail", "--assert"}
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            break
        name, equals, value = token.partition("=")
        if name in names:
            if not equals:
                index += 1
                value = arguments[index] if index < len(arguments) else ""
            values[names[name]].append(value)
        elif token.startswith("-n") and not token.startswith("--") and len(token) > 2:
            values["workers"].append(token[2:])
        elif name in {"--tx", "--px", "--maxprocesses", "--looponfail", "-f", "-d"}:
            values["unsupported"].append(token)
        elif token in {"--collect-only", "--co"}:
            values["collect"].append(token)
        elif name in value_options and not equals:
            index += 1
        index += 1
    return values


def effective_pytest_profile(arguments, *, environment=None, default_workers=12) -> dict:
    """Explicit CLI pins are diagnostics; hidden contradictory controls fail."""
    environment = os.environ if environment is None else environment
    arguments = list(arguments)
    cli = pytest_worker_options(arguments)
    hidden = pytest_worker_options(shlex.split(environment.get("PYTEST_ADDOPTS", "")))
    if cli["unsupported"] or hidden["unsupported"]:
        raise ProbeError("unsupported pytest worker population override")
    for key in ("workers", "distribution", "restart"):
        if len(set(cli[key])) > 1 or len(set(hidden[key])) > 1:
            raise ProbeError("conflicting pytest worker overrides")
    pin = cli["workers"][-1] if cli["workers"] else str(default_workers)
    if not pin.isdecimal():
        raise ProbeError("pytest requires an exact numeric worker pin")
    workers = int(pin)
    expected = {"workers": pin, "distribution": "load", "restart": "0"}
    for key, wanted in expected.items():
        allowed = {"load", "no"} if key == "distribution" and workers == 0 else {wanted}
        if (hidden[key] and hidden[key][-1] not in allowed
                or key != "workers" and cli[key] and cli[key][-1] not in allowed):
            raise ProbeError("conflicting pytest worker/environment configuration")
    if environment.get("PYTEST_XDIST_AUTO_NUM_WORKERS"):
        raise ProbeError("conflicting automatic pytest worker environment")
    collect = bool(cli["collect"])
    return {"workers": 0 if collect else workers,
            "distribution": "no" if collect or workers == 0 else "load",
            "max_worker_restart": 0, "collect_only": collect}

def _version_prefix(value: str) -> tuple[int, int]:
    parts = value.split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError) as exc:
        raise ProbeError(f"incompatible package version: {value!r}") from exc


def _in_range(value: str, bounds: tuple[tuple[int, int], tuple[int, int]]) -> bool:
    parsed = _version_prefix(value)
    return bounds[0] <= parsed < bounds[1]


def load_toolchain() -> dict[str, str]:
    """Resolve the declared optional capability without installing anything."""

    try:
        pytest_version = importlib.metadata.version("pytest")
        xdist_version = importlib.metadata.version("pytest-xdist")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ProbeError(
            "parallel verification requires the optional bp-v4-measurement or parallel-verification extra"
        ) from exc
    if not _in_range(pytest_version, PYTEST_RANGE):
        raise ProbeError(f"incompatible pytest version: {pytest_version}")
    if not _in_range(xdist_version, XDIST_RANGE):
        raise ProbeError(f"incompatible pytest-xdist version: {xdist_version}")
    try:
        importlib.import_module("pytest")
        importlib.import_module("xdist.plugin")
    except (ImportError, RuntimeError) as exc:
        raise ProbeError("incompatible pytest/xdist capability") from exc
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_executable": str(Path(sys.executable).resolve()),
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "pytest_version": pytest_version,
        "pytest_xdist_version": xdist_version,
        "installed_distributions_digest": toolchain_inventory_digest(),
        **installed_git_identity(),
    }


def collection_identity(nodeids: object) -> dict[str, Any]:
    """Read historical nodeid lists or validated compact collection identities."""
    try:
        return pytest_collection_identity(nodeids)
    except (VerificationContractError, UnicodeError) as exc:
        raise ProbeError(str(exc)) from exc


def subject_identity(repository: Path) -> dict[str, Any]:
    """Return exact Git identity when the verification subject provides it."""

    completed = subprocess.run(
        [git_executable(), "rev-parse", "--verify", "HEAD"],
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        text=True,
    )
    head = completed.stdout.strip()
    if completed.returncode == 0 and len(head) == 40 and all(
        character in "0123456789abcdef" for character in head
    ):
        status = subprocess.run(
            [git_executable(), "status", "--porcelain", "--untracked-files=all"],
            cwd=repository,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if status.returncode != 0 or status.stdout:
            raise ProbeError("verification subject is not a clean exact Git commit")
        return {"kind": "git-commit", "head_sha": head, "worktree_clean": True}
    return {"kind": "unavailable", "head_sha": None}


def _load_observation(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_CANONICAL_BYTES:
            raise ProbeError("structured pytest observation exceeds declared byte bound")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeError("missing or malformed structured pytest observation") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") != "AIOS_BP_V4_PYTEST_OBSERVATION"
        or value.get("version") != 1
        or not isinstance(value.get("exit_status"), int)
        or isinstance(value.get("exit_status"), bool)
    ):
        raise ProbeError("incompatible structured pytest observation")
    return value


def run_pytest(
    repository: Path,
    temporary_root: Path,
    *,
    label: str,
    workers: int,
    collect_only: bool,
) -> tuple[int, float, dict[str, Any]]:
    """Run one fixed pytest operation and return its structured observation."""

    toolchain = load_toolchain()
    observation = temporary_root / f"{label}.json"
    basetemp = temporary_root / f"{label}-tmp"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "xdist.plugin",
        "-p",
        PLUGIN_NAME,
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(basetemp),
        "-q",
    ]
    if type(workers) is not int or workers < 1:
        raise ProbeError("pytest runner requires an exact positive worker count")
    # A historical serial measurement is explicitly pinned; repository addopts
    # must never turn it into a second twelve-worker population.
    command.extend(["-n", "0" if collect_only or workers == 1 else str(workers),
                    "--dist", "load", "--max-worker-restart", "0"])
    if collect_only:
        command.append("--collect-only")

    environment = os.environ.copy()
    # Only this wrapper owns the export; pytest and nested runners must not inherit it.
    export = environment.pop("AIOS_V2_OBSERVATION_DIRECTORY", None)
    effective_pytest_profile(command[3:], environment=environment)
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("PYTEST_PLUGINS", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    subject_pythonpath = os.pathsep.join((str(repository / "src"), str(repository / "tests")))
    current_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        subject_pythonpath + os.pathsep + current_pythonpath
        if current_pythonpath
        else subject_pythonpath
    )
    environment[PLUGIN_OUTPUT_ENV] = str(observation)
    environment["AIOS_V2_PROFILE"] = json.dumps({
        "profile": "bounded-parallel-full-suite-v1" if workers == 12 and not collect_only else "pytest-parallel-observed-v2", "workers": 0 if collect_only or workers == 1 else workers,
        "distribution": "no" if collect_only or workers == 1 else "load", "max_worker_restart": 0,
        "collect_only": collect_only,
    }, sort_keys=True)

    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=repository,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    elapsed = time.perf_counter() - started
    try:
        loaded = _load_observation(observation)
    except ProbeError as exc:
        detail = (getattr(completed, "stderr", b"") or b"").decode("utf-8", errors="replace")
        raise ProbeError(f"{exc}; pytest exit {completed.returncode}: {detail}") from exc
    if export:
        directory = Path(export)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{label}.json"
        encoded = observation.read_bytes()
        if destination.exists():
            if destination.read_bytes() != encoded:
                raise ProbeError("conflicting immutable pytest observation")
        else:
            destination.write_bytes(encoded)
    if completed.returncode != _observation_exit_status(loaded):
        raise ProbeError("raw pytest status disagrees with observation")
    if load_toolchain() != toolchain:
        raise ProbeError("pytest toolchain changed during subprocess execution")
    validate_pytest_execution(loaded, json.loads(environment["AIOS_V2_PROFILE"]), toolchain)
    return completed.returncode, elapsed, loaded


def validate_pytest_execution(observation: dict, profile: dict, toolchain: dict,
                             *, subject_sha: str | None = None) -> None:
    """Require actual controller/worker conditions for prospective observations."""
    if (observation.get("execution_contract") != EXECUTION_CONTRACT
            or observation.get("profile") != profile or observation.get("toolchain") != toolchain):
        raise ProbeError("missing or conflicting actual pytest execution binding")
    if subject_sha is not None and observation.get("subject_sha") != subject_sha:
        raise ProbeError("pytest observation subject binding mismatch")
    canonical = observation.get("failure_diagnostics", {}).get("canonical", {})
    if canonical.get("complete") is not True or canonical.get("unstable") is not False:
        raise ProbeError("incomplete or unstable pytest execution")
    try:
        validate_pytest_execution_binding(observation, profile=profile, toolchain=toolchain,
                                         reports=canonical["reports"], subject_sha=subject_sha)
    except (VerificationContractError, KeyError, TypeError) as exc:
        raise ProbeError(str(exc)) from exc
    workers = profile["workers"]
    if workers and not profile["collect_only"]:
        _parallel_conformance(observation, collection_identity(observation.get("controller_collection")), workers)
        for facts in observation["workers"].values():
            if facts.get("profile") != profile or facts.get("toolchain") != toolchain:
                raise ProbeError("xdist worker profile or toolchain binding mismatch")
    elif observation.get("workers") or observation.get("worker_collections"):
        raise ProbeError("collection-only or serial diagnostic launched xdist workers")


Runner = Callable[..., tuple[int, float, dict[str, Any]]]


def _serial_conformance(
    observation: dict[str, Any], canonical: dict[str, Any]
) -> dict[str, bool]:
    identity = collection_identity(observation.get("controller_collection"))
    return {"collection_matches_canonical": identity == canonical}


def _observation_exit_status(observation: dict[str, Any]) -> int:
    value = observation.get("exit_status")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProbeError("malformed structured pytest exit status")
    return value


def _failure_diagnostics(observation: dict[str, Any]) -> dict[str, Any]:
    """Validate display bounds separately from complete canonical truth.

    Historical v1 display-only observations remain readable; they cannot supply
    V2 attribution, which independently requires a complete canonical population.
    """

    value = observation.get("failure_diagnostics")
    if not isinstance(value, dict):
        raise ProbeError("missing structured pytest failure diagnostics")
    count = value.get("reported_count")
    displayed_count = value.get("displayed_count")
    limit = value.get("display_limit")
    identities = value.get("displayed_identities")
    truncated = value.get("truncated")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or not isinstance(displayed_count, int)
        or isinstance(displayed_count, bool)
        or displayed_count < 0
        or displayed_count > count
        or limit != MAX_FAILURE_IDENTITIES
        or not isinstance(identities, list)
        or displayed_count != len(identities)
        or displayed_count > MAX_FAILURE_IDENTITIES
        or not isinstance(truncated, bool)
    ):
        raise ProbeError("malformed structured pytest failure diagnostics")

    previous: tuple[str, int, str] | None = None
    phase_order = {"collect": -1, "setup": 0, "call": 1, "teardown": 2}
    for identity in identities:
        if not isinstance(identity, dict) or set(identity) != {
            "nodeid",
            "phase",
            "clipped",
            "fingerprint",
        }:
            raise ProbeError("malformed pytest failure identity")
        nodeid = identity["nodeid"]
        phase = identity["phase"]
        clipped = identity["clipped"]
        fingerprint = identity["fingerprint"]
        if (
            not isinstance(nodeid, str)
            or not nodeid
            or "\\" in nodeid
            or len(nodeid) > MAX_NODEID_DISPLAY_CHARS
            or phase not in FAILURE_PHASES
            or not isinstance(clipped, bool)
            or (not clipped and fingerprint is not None)
            or (
                clipped
                and (
                    not isinstance(fingerprint, str)
                    or not fingerprint.startswith("sha256:")
                    or len(fingerprint) != 71
                    or any(
                        character not in "0123456789abcdef"
                        for character in fingerprint[7:]
                    )
                    or not nodeid.endswith(f"...#{fingerprint}")
                )
            )
        ):
            raise ProbeError("malformed pytest failure identity")
        key = (nodeid, phase_order[phase], fingerprint or "")
        if previous is not None and key <= previous:
            raise ProbeError("unordered or duplicate pytest failure identities")
        previous = key
    canonical = value.get("canonical")
    if canonical is not None:
        try:
            validate_observation({
                **canonical, "subject_sha": "0" * 40, "command": "pytest-observation",
                "profile": observation.get("profile"), "toolchain": observation.get("toolchain"),
                "exit_code": _observation_exit_status(observation),
            })
        except VerificationContractError as exc:
            raise ProbeError(str(exc)) from exc
        if canonical["failure_count"] != count:
            raise ProbeError("canonical failed population does not match reported count")
    return value


def _profile_result(
    *,
    mode: str,
    workers: int,
    elapsed: float,
    status: int,
    observation: dict[str, Any],
    conformance: dict[str, bool],
) -> dict[str, Any]:
    diagnostics = _failure_diagnostics(observation)
    result: dict[str, Any] = {
        "mode": mode,
        "workers": workers,
        "elapsed_seconds": round(elapsed, 6),
        "exit_status": status,
        "pytest_exit_status": _observation_exit_status(observation),
        "conformance": conformance,
    }
    if "canonical" in diagnostics:
        result["canonical"] = diagnostics["canonical"]
        result["observation_profile"] = observation["profile"]
        result["observation_toolchain"] = observation["toolchain"]
    if observation.get("execution_contract") == EXECUTION_CONTRACT:
        result["execution"] = {key: observation[key] for key in (
            "execution_contract", "subject_sha", "workers", "worker_collections", "controller_collection")}
    if status != 0 or result["pytest_exit_status"] != 0:
        result["failure_diagnostics"] = diagnostics
    elif diagnostics["reported_count"] != 0:
        raise ProbeError("successful pytest observation reports failures")
    return result


def _parallel_conformance(
    observation: dict[str, Any], canonical: dict[str, Any], expected: int
) -> dict[str, bool]:
    collections = observation.get("worker_collections")
    workers = observation.get("workers")
    if not isinstance(collections, dict) or not isinstance(workers, dict):
        raise ProbeError("malformed structured xdist worker data")
    if (set(collections) != set(workers) or len(workers) != expected
            or any(not isinstance(worker_id, str) or not worker_id for worker_id in workers)):
        raise ProbeError("missing or unexpected xdist worker data")
    canonical = collection_identity(canonical)
    controller = observation.get("controller_collection")
    # Historical list observations had no controller collection in xdist mode.
    # New compact observations must preserve the controller's own identity.
    if controller is not None or any(isinstance(c, dict) for c in collections.values()):
        if collection_identity(controller) != canonical:
            raise ProbeError("controller collection does not match canonical collection")

    temp_roots: list[str] = []
    git_roots: list[str] = []
    process_ids: list[int] = []
    for worker_id in sorted(workers):
        worker = workers[worker_id]
        if not isinstance(worker, dict) or worker.get("worker_id") != worker_id:
            raise ProbeError("malformed structured xdist worker identity")
        identity = collection_identity(collections[worker_id])
        if collection_identity(worker.get("collection")) != identity:
            raise ProbeError("inconsistent structured xdist worker collection")
        if identity != canonical:
            raise ProbeError("xdist worker collection does not match canonical collection")
        temp_root = worker.get("temporary_root")
        git_root = worker.get("git_fixture_cache_root")
        process_id = worker.get("process_id")
        if (
            not isinstance(temp_root, str)
            or not temp_root
            or not isinstance(git_root, str)
            or not git_root
            or not isinstance(process_id, int)
            or isinstance(process_id, bool)
        ):
            raise ProbeError("missing structured xdist worker isolation data")
        temp_roots.append(os.path.normcase(os.path.abspath(temp_root)))
        git_roots.append(os.path.normcase(os.path.abspath(git_root)))
        process_ids.append(process_id)

    if (
        len(set(temp_roots)) != expected
        or len(set(git_roots)) != expected
        or len(set(process_ids)) != expected
    ):
        raise ProbeError("xdist workers share mutable roots or process identity")
    for left in range(expected):
        for right in range(left + 1, expected):
            for first in (Path(temp_roots[left]), Path(git_roots[left])):
                for second in (Path(temp_roots[right]), Path(git_roots[right])):
                    if first.is_relative_to(second) or second.is_relative_to(first):
                        raise ProbeError("xdist worker writable roots overlap")
    return {
        "all_workers_reported": True,
        "collections_match_canonical": True,
        "temporary_roots_isolated": True,
        "git_fixture_cache_roots_isolated": True,
        "processes_distinct": True,
    }
