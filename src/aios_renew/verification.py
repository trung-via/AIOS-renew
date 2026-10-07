"""Deterministic Runtime-owned execution of canonical verification commands."""

from __future__ import annotations

import errno
import hashlib
import importlib.metadata
import json
import os
import platform as platform_module
import re
import shutil
import stat
import subprocess
import sys
import yaml
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator

from .artifacts import Claim, Evidence, EvidenceOutcome, EvidenceSource, Result
from .verification_contract import (
    MINIMUM_SUFFICIENT_V2, MAX_CANONICAL_BYTES, SELECTED_FULL_SUITE_COMMAND,
    VerificationContractError, attribute_failures, attributed_task_passes,
    derive_minimum_verification, failed_identities, parse_pytest_coverage,
    validate_observation, verification_digest,
    validate_evidence_binding,
    toolchain_inventory_digest,
)


VerificationRunner = Callable[..., subprocess.CompletedProcess[bytes]]
_WINDOWS_POWERSHELL_UTF8_PREAMBLE = (
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
)
_WINDOWS_TEMP_PREFIX = "aios-verification-"
_TEMP_CLEANUP_MAX_ELAPSED_SECONDS = 5.0
_TEMP_CLEANUP_INITIAL_RETRY_DELAY_SECONDS = 0.05
_TEMP_CLEANUP_MAX_RETRY_DELAY_SECONDS = 0.5


class RuntimeVerificationError(RuntimeError):
    """Raised when deterministic Runtime verification fails closed."""

    def __init__(self, message: str, *, evidence: Iterable[Evidence] = ()) -> None:
        super().__init__(message)
        self.evidence = tuple(evidence)


@contextmanager
def materialize_verification_subject(
    repository: Path,
    *,
    run_id: str,
    subject_sha: str,
    environment: Mapping[str, str] | None = None,
) -> Iterator[Path]:
    """Yield one clean, independently mutable checkout of an exact commit.

    The clone deliberately has its own Git directory rather than using a linked
    worktree.  Immutable objects may be copied from the control repository, but
    its index, refs, configuration, and other mutable Git state are isolated.
    Cleanup is subordinate: it can neither change verification's verdict nor
    replace a canonical terminal with a cleanup failure.
    """

    repository = repository.resolve()
    temp_root: Path | None = None
    try:
        exact_commit = _git(
            repository, "rev-parse", "--verify", f"{subject_sha}^{{commit}}"
        )
        if exact_commit != subject_sha:
            raise RuntimeVerificationError("verification subject commit mismatch")
        temp_root = _isolated_temp_root(
            repository=repository,
            run_id=run_id,
            environment=environment,
        )
        subject = temp_root / "subject"
        hooks = temp_root / "hooks"
        hooks.mkdir()
        empty_config = temp_root / "empty-gitconfig"
        empty_config.touch()
        empty_attributes = temp_root / "empty-attributes"
        empty_attributes.touch()
        git_environment = _materialization_git_environment(empty_config)
        origin_url = _optional_git(
            repository, "remote", "get-url", "origin", environment=git_environment
        )
        _git(
            repository,
            "-c",
            f"core.hooksPath={hooks}",
            "clone",
            "--local",
            "--no-hardlinks",
            "--no-checkout",
            "--no-tags",
            str(repository),
            str(subject),
            environment=git_environment,
        )
        # Set subject-local checkout policy before Git writes any tracked file.
        # Tracked .gitattributes still overrides these defaults where explicit.
        for key, value in (
            ("core.autocrlf", "false"),
            ("core.eol", "lf"),
            ("core.attributesFile", str(empty_attributes)),
        ):
            _git(subject, "config", "--local", key, value, environment=git_environment)
        if origin_url is not None:
            _git(
                subject,
                "remote",
                "set-url",
                "origin",
                origin_url,
                environment=git_environment,
            )
        _git(
            subject,
            "-c",
            f"core.hooksPath={hooks}",
            "checkout",
            "--detach",
            subject_sha,
            environment=git_environment,
        )
        if not (subject / ".git").is_dir():
            raise RuntimeVerificationError(
                "verification subject Git directory is unavailable"
            )
        if _git(subject, "rev-parse", "HEAD", environment=git_environment) != subject_sha:
            raise RuntimeVerificationError("verification subject HEAD mismatch")
        if _git(subject, "status", "--porcelain", environment=git_environment):
            raise RuntimeVerificationError("verification subject is initially dirty")
        (subject / ".git" / "aios").mkdir()
    except RuntimeVerificationError:
        if temp_root is not None:
            try:
                _remove_temp_root(temp_root)
            except OSError:
                pass
        raise
    except (OSError, UnicodeError, RuntimeError) as exc:
        if temp_root is not None:
            try:
                _remove_temp_root(temp_root)
            except OSError:
                pass
        raise RuntimeVerificationError(
            f"verification subject could not be materialized: {exc}"
        ) from exc
    except BaseException:
        if temp_root is not None:
            try:
                _remove_temp_root(temp_root)
            except OSError:
                pass
        raise

    try:
        yield subject
    finally:
        if temp_root is not None:
            try:
                _remove_temp_root(temp_root)
            except OSError:
                pass


def execute_verification(
    commands: Iterable[str],
    *,
    run_id: str,
    subject_sha: str,
    repository: Path,
    raw_directory: Path,
    runner: VerificationRunner = subprocess.run,
    platform: str = os.name,
    environment: Mapping[str, str] | None = None,
    stop_on_failure: bool = True,
    start_order: int = 1,
) -> tuple[Evidence, ...]:
    """Execute each command once, in order; default to stopping at raw failure."""

    repository = repository.resolve()
    temp_root: Path | None = None
    if platform == "nt":
        try:
            raw_directory.mkdir(parents=True, exist_ok=True)
            temp_root = _isolated_temp_root(
                repository=repository,
                run_id=run_id,
                environment=environment,
            )
        except (OSError, RuntimeError) as exc:
            raise RuntimeVerificationError(
                f"verification environment could not be isolated: {exc}"
            ) from exc
    else:
        raw_directory.mkdir(parents=True, exist_ok=True)
    evidence: list[Evidence] = []
    try:
        env = _verification_environment(
            environment=environment,
            platform=platform,
            temp_root=temp_root,
        )
        for order, command in enumerate(commands, start=start_order):
            exact_command = _strict_utf8_command(command)
            evidence_id = f"{run_id}-V{order:03d}"
            raw_path = raw_directory / f"{evidence_id}.raw"
            shell_command = _shell_command(exact_command, platform=platform)
            command_env = dict(env)
            if command == SELECTED_FULL_SUITE_COMMAND:
                command_env["AIOS_V2_OBSERVATION_DIRECTORY"] = str(raw_directory / f"{evidence_id}-observations")
            try:
                completed = runner(
                    shell_command,
                    cwd=repository,
                    env=command_env,
                    capture_output=True,
                    text=False,
                    check=False,
                )
            except OSError as exc:
                raise RuntimeVerificationError(
                    f"verification command could not start: {command}: {exc}",
                    evidence=evidence,
                ) from exc

            stdout = _require_bytes(completed.stdout, "stdout")
            stderr = _require_bytes(completed.stderr, "stderr")
            raw_path.write_bytes(b"STDOUT\n" + stdout + b"\nSTDERR\n" + stderr)
            try:
                decoded_stdout = stdout.decode("utf-8", errors="strict")
                decoded_stderr = stderr.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise RuntimeVerificationError(
                    f"verification output is not strict UTF-8: {command}",
                    evidence=evidence,
                ) from exc

            item = Evidence(
                evidence_id=evidence_id,
                run_id=run_id,
                subject_sha=subject_sha,
                type="VERIFICATION",
                source=EvidenceSource(command=command),
                result=EvidenceOutcome(
                    exit_code=completed.returncode,
                    summary=_summary(
                        completed.returncode,
                        stdout=decoded_stdout,
                        stderr=decoded_stderr,
                    ),
                ),
                raw_path=str(raw_path),
            )
            evidence.append(item)
            if completed.returncode != 0 and stop_on_failure:
                raise RuntimeVerificationError(
                    f"verification command failed with exit code "
                    f"{completed.returncode}: {command}",
                    evidence=evidence,
                )
    finally:
        if temp_root is not None:
            try:
                _remove_temp_root(temp_root)
            except OSError as exc:
                raise RuntimeVerificationError(
                    f"verification environment could not be cleaned: {exc}",
                    evidence=evidence,
                ) from exc

    return tuple(evidence)


def attach_verification_evidence(
    result: Result, evidence: Iterable[Evidence]
) -> Result:
    """Mechanically bind the complete Runtime evidence set to every claim."""

    evidence_ids = tuple(item.evidence_id for item in evidence)
    claims = tuple(
        Claim(
            id=claim.id,
            satisfies=claim.satisfies,
            claim=claim.claim,
            evidence=evidence_ids,
        )
        for claim in result.claims
    )
    return replace(result, claims=claims)


def _v2_toolchain() -> dict[str, str]:
    values = {
        "python_implementation": platform_module.python_implementation(),
        "python_version": platform_module.python_version(),
        "python_executable": str(Path(sys.executable).resolve()),
        "platform_system": platform_module.system(),
        "platform_machine": platform_module.machine(),
        "installed_distributions_digest": toolchain_inventory_digest(),
    }
    for package, key in (("pytest", "pytest_version"), ("pytest-xdist", "pytest_xdist_version")):
        try:
            values[key] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            values[key] = "absent"
    return values


def _v2_profile(repository: Path, sha: str, command: str, environment: Mapping[str, str],
                *, affected: bool = False) -> dict:
    selected = command == SELECTED_FULL_SUITE_COMMAND
    description = {
        "profile": "bounded-parallel-full-suite-v1" if selected else "exact-affected-pytest-v2" if affected else "pytest-observed-v2",
        "workers": 12 if selected else 1, "distribution": "load",
        "max_worker_restart": 0, "collect_only": False,
    }
    if selected:
        from .verification_profile import selected_profile_identity
        description.update(selected_profile_identity(yaml.safe_load(
            _git(repository, "show", f"{sha}:.ai/verification-profiles.yaml"))))
    # Configuration and observer identity are mechanical material, not inferred
    # dependencies. An altered observer or selected policy prevents comparison.
    observer = _optional_git(repository, "rev-parse", f"{sha}:tests/bp_v4_probe_plugin.py")
    policy = _optional_git(repository, "rev-parse", f"{sha}:.ai/verification-profiles.yaml")
    tool_paths = ["src/aios_renew/verification_contract.py", "tests/conftest.py"]
    if selected:
        tool_paths.extend(("scripts/aios_parallel_full_suite.py", "src/aios_renew/parallel_verification.py",
                           "src/aios_renew/verification_profile.py"))
    owned_tools = {path: _optional_git(repository, "rev-parse", f"{sha}:{path}") for path in tool_paths}
    bound_environment = {k: v for k, v in environment.items()
                         if k.upper() not in {"TEMP", "TMP", "TMPDIR", "AIOS_BP_V4_PLUGIN_OUTPUT", "AIOS_V2_PROFILE", "AIOS_V2_OBSERVATION_DIRECTORY"}}
    return {**description, "observer_blob": observer, "selected_policy_blob": policy if selected else None,
            "repository_tool_blobs": owned_tools,
            "environment_digest": verification_digest(bound_environment),
            "isolation_policy": "clean-clone-external-temp-v1"}


def _v2_known_observation(records: Iterable[dict], *, sha: str, command: str,
                          profile: dict, toolchain: dict) -> dict | None:
    matches = []
    for record in records:
        for key in ("candidate", "base"):
            value = record.get(key)
            if isinstance(value, dict) and all(value.get(k) == v for k, v in (
                ("subject_sha", sha), ("command", command), ("profile", profile), ("toolchain", toolchain))):
                matches.append(value)
    if not matches:
        return None
    result = dict(matches[0])
    if len({verification_digest(v) for v in matches}) != 1:
        result.update(complete=False, unstable=True)
    return result


def _v2_cache_records(directory: Path, *, repository: Path | None = None) -> tuple[dict, ...]:
    records = []
    if not directory.is_dir():
        return ()
    for path in sorted(directory.glob("*.json")):
        try:
            if path.stat().st_size > MAX_CANONICAL_BYTES:
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("policy") != MINIMUM_SUFFICIENT_V2 or "reuse" in value:
                continue
            validate_observation(value["candidate"])
            validate_evidence_binding(value["binding"])
            if any(value["candidate"][k] != value["binding"][k] for k in ("subject_sha", "command", "profile", "toolchain")):
                continue
            if repository is not None and _git(repository, "rev-parse", f"{value['binding']['subject_sha']}^{{tree}}") != value["binding"]["tree_sha"]:
                continue
            if value.get("candidate_digest") != verification_digest(value["candidate"]):
                continue
            raw = Path(value["raw_path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != value["raw_digest"]:
                continue
            if "base" in value:
                if not isinstance(value["base"], dict) or value.get("base_digest") != verification_digest(value["base"]):
                    continue
                if value["base"].get("subject_sha") != value["binding"]["base_sha"]:
                    continue
                if value["base"].get("complete") is True:
                    validate_observation(value["base"])
                base_raw = Path(value["base_raw_path"]).read_bytes()
                if hashlib.sha256(base_raw).hexdigest() != value["base_raw_digest"]:
                    continue
            verification_digest(value)
            records.append(value)
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, RuntimeError, VerificationContractError):
            # Missing/stale/malformed evidence cannot discharge a requirement.
            continue
    return tuple(records)


def _v2_observation(item: Evidence, output: Path, *, binding: dict, selected: bool) -> dict:
    value = None
    try:
        if selected:
            raw = Path(item.raw_path).read_bytes().split(b"\nSTDERR\n", 1)[0]
            envelope = json.loads(raw.removeprefix(b"STDOUT\n").decode("utf-8"))
            result = envelope["result"]
            canonical = result["canonical"]
            observed_profile = result["observation_profile"]
            observed_toolchain = result["observation_toolchain"]
            valid_exit = result["exit_status"] == item.result.exit_code == result["pytest_exit_status"]
            valid_conditions = bool(result["conformance"]) and all(v is True for v in result["conformance"].values())
        else:
            if output.stat().st_size > MAX_CANONICAL_BYTES:
                raise VerificationContractError("canonical observation exceeds byte bound")
            envelope = json.loads(output.read_text(encoding="utf-8"))
            canonical = envelope["failure_diagnostics"]["canonical"]
            observed_profile, observed_toolchain = envelope["profile"], envelope["toolchain"]
            valid_exit = envelope["exit_status"] == item.result.exit_code
            valid_conditions = (envelope.get("schema") == "AIOS_BP_V4_PYTEST_OBSERVATION"
                                and envelope.get("version") == 1
                                and envelope["failure_diagnostics"].get("reported_count") == canonical["failure_count"])
        description = {k: binding["profile"][k] for k in (
            "profile", "workers", "distribution", "max_worker_restart", "collect_only")}
        valid_conditions = (valid_conditions and observed_profile == description
                            and isinstance(observed_toolchain, dict)
                            and binding["profile"]["observer_blob"] is not None)
        # The authored launcher may resolve a different interpreter from Runtime
        # itself. Bind the actually observed toolchain; prediction uncertainty
        # prevents cache reuse, not a truthful raw successful execution.
        binding["toolchain"] = observed_toolchain
        reports = [dict(r) for r in canonical["reports"]]
        for report in reports:
            if report["outcome"] == "FAIL":
                if report.get("profile") != observed_profile or report.get("toolchain") != observed_toolchain:
                    valid_conditions = False
                report["profile"], report["toolchain"] = binding["profile"], binding["toolchain"]
        value = {**canonical, "reports": reports, "subject_sha": item.subject_sha,
                 "command": item.source.command, "exit_code": item.result.exit_code,
                 "profile": binding["profile"], "toolchain": binding["toolchain"]}
        if not valid_exit or not valid_conditions:
            value.update(complete=False, unstable=True)
        validate_observation(value)
        return value
    except (OSError, UnicodeError, KeyError, ValueError, TypeError, VerificationContractError):
        if value is not None:
            # Preserve parsed canonical identities even when comparability fails.
            value.update(complete=False, unstable=True)
            return value
        if selected:
            # Collection/conformance errors can stop the selected wrapper before
            # it emits its envelope. Its Runtime-exported raw observations still
            # preserve the identities; they are explicitly non-comparable.
            sources = Path(item.raw_path).parent / f"{Path(item.raw_path).stem}-observations"
            reports = {}
            source_paths = []
            try:
                for path in sorted(sources.glob("*.json")):
                    if path.stat().st_size > MAX_CANONICAL_BYTES:
                        raise VerificationContractError("exported observation exceeds declared bound")
                    observed = json.loads(path.read_text(encoding="utf-8"))
                    source_paths.append(str(path))
                    for report in observed["failure_diagnostics"]["canonical"]["reports"]:
                        fact = dict(report)
                        if fact["outcome"] == "FAIL":
                            fact["observed_profile"] = fact.get("profile")
                            fact["observed_toolchain"] = fact.get("toolchain")
                            fact["profile"], fact["toolchain"] = binding["profile"], binding["toolchain"]
                        reports[(fact["nodeid"], fact["phase"])] = fact
                value = {"subject_sha": item.subject_sha, "command": item.source.command,
                         "exit_code": item.result.exit_code, "profile": binding["profile"],
                         "toolchain": binding["toolchain"], "complete": False, "unstable": True,
                         "reports": sorted(reports.values(), key=lambda r: (r["nodeid"], {"collect": -1, "setup": 0, "call": 1, "teardown": 2}[r["phase"]])),
                         "failure_count": sum(r["outcome"] == "FAIL" for r in reports.values()),
                         "observation_sources": source_paths,
                         "errors": ["selected wrapper observation is not comparable"]}
                verification_digest(value)
                return value
            except (OSError, UnicodeError, KeyError, TypeError, ValueError, VerificationContractError):
                pass
        # Candidate-only or legacy observations are diagnostic, never proof.
        return {"subject_sha": item.subject_sha, "command": item.source.command,
                "exit_code": item.result.exit_code, "profile": binding["profile"],
                "toolchain": binding["toolchain"], "complete": False, "unstable": True,
                "failure_count": 0, "reports": []}


def execute_minimum_verification(
    commands: Iterable[str], *, run_id: str, base_sha: str, subject_sha: str,
    repository: Path, raw_directory: Path, cache_directory: Path,
    modification_scope: Iterable[str], operation: str,
    runner: VerificationRunner = subprocess.run, platform: str = os.name,
    environment: Mapping[str, str] | None = None,
    subject_check: Callable[..., None] | None = None,
    correction_base_sha: str | None = None,
) -> tuple[Evidence, ...]:
    """Runtime V2 execution of the single derived plan and mandatory guards.

    Reuse is conservative (complete tracked tree equality). Exact failure
    probes run first for authorized correction test paths; authored requirements
    remain until valid bound evidence discharges them. No correction is selected.
    """
    authored = tuple(dict.fromkeys(commands))
    modification_scope = tuple(modification_scope)
    env = dict(os.environ if environment is None else environment)
    toolchain = _v2_toolchain()
    raw_directory.mkdir(parents=True, exist_ok=True)
    cache_directory.mkdir(parents=True, exist_ok=True)
    try:
        correction_base = base_sha if correction_base_sha is None else correction_base_sha
        for sha in (base_sha, subject_sha, correction_base):
            if _git(repository, "rev-parse", "--verify", f"{sha}^{{commit}}") != sha:
                raise VerificationContractError("V2 requires exact base and candidate commits")
        changed = tuple(sorted(filter(None, _git(repository, "diff", "--name-only", "--no-renames", "-z", base_sha, subject_sha).split("\0"))))
        correction_changed = changed if correction_base == base_sha else tuple(sorted(filter(None,
            _git(repository, "diff", "--name-only", "--no-renames", "-z", correction_base, subject_sha).split("\0"))))
        tree = _git(repository, "rev-parse", f"{subject_sha}^{{tree}}")
        records = _v2_cache_records(cache_directory, repository=repository)
        envelope_digest = verification_digest({"authored": authored, "operation": operation,
                                                "modification_scope": sorted(modification_scope)})
        bindings, base_known, correction_known = {}, {}, {}
        for command in authored:
            base_profile = _v2_profile(repository, base_sha, command, env)
            known = _v2_known_observation(records, sha=base_sha, command=command,
                                        profile=base_profile, toolchain=toolchain)
            base_known[command] = known
            correction_known[command] = _v2_known_observation(records, sha=correction_base, command=command,
                profile=_v2_profile(repository, correction_base, command, env), toolchain=toolchain)
            bindings[command] = {
                "subject_sha": subject_sha, "base_sha": base_sha, "tree_sha": tree,
                "command": command, "profile": _v2_profile(repository, subject_sha, command, env),
                "toolchain": toolchain, "envelope_digest": envelope_digest,
                "changed_files_digest": verification_digest(changed),
                "failure_set_digest": verification_digest(known if known is not None else []),
                "correction_base_sha": correction_base,
                "correction_changed_files_digest": verification_digest(correction_changed),
            }
        valid_records = []
        for record in records:
            # Conflicting observations on an identical tree/context invalidate
            # reuse rather than choosing the most convenient previous result.
            peers = [r for r in records if {k: v for k, v in r["binding"].items() if k != "subject_sha"}
                     == {k: v for k, v in record["binding"].items() if k != "subject_sha"}]
            if len({verification_digest({k: v for k, v in r["candidate"].items() if k != "subject_sha"}) for r in peers}) == 1:
                valid_records.append(record)
        plan = derive_minimum_verification(
            authored, base_sha=base_sha, candidate_sha=subject_sha, changed_files=changed,
            modification_scope=tuple(modification_scope), operation=operation, bindings=bindings,
            failed_observations=tuple(v for v in correction_known.values() if v is not None),
            still_valid_evidence=valid_records,
            correction_base_sha=correction_base, correction_changed_files=correction_changed,
        )
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeVerificationError(f"V2 derivation failed closed: {exc}") from exc

    evidence: list[Evidence] = []
    audit = {"policy": MINIMUM_SUFFICIENT_V2, "base_sha": base_sha, "candidate_sha": subject_sha,
             "authored": authored, "changed_files": changed, "modification_scope": sorted(modification_scope),
             "correction_base_sha": correction_base, "correction_changed_files": correction_changed,
             "affected_first": plan.affected_first, "fallback": plan.fallback, "records": []}
    blocking = []
    with ExitStack() as subjects:
        candidate_repository = None
        base_repository = None

        def run_once(command: str, *, sha: str, base: bool, affected: bool = False) -> tuple[Evidence, dict, dict]:
            nonlocal candidate_repository, base_repository
            if base:
                if base_repository is None:
                    base_repository = subjects.enter_context(materialize_verification_subject(
                        repository, run_id=run_id + "-base", subject_sha=base_sha))
                checkout = base_repository
            else:
                if candidate_repository is None:
                    candidate_repository = subjects.enter_context(materialize_verification_subject(
                        repository, run_id=run_id, subject_sha=subject_sha))
                checkout = candidate_repository
            order = len(evidence) + 1
            directory = raw_directory / ("base" if base else "candidate")
            output = directory / f"observation-{order:03d}.json"
            profile = _v2_profile(repository, sha, command, env, affected=affected)
            binding = {**bindings.get(command, {}), "subject_sha": sha, "command": command,
                       "profile": profile, "toolchain": toolchain}
            selected = command == SELECTED_FULL_SUITE_COMMAND
            command_env = dict(env)
            command_env.update(PYTHONDONTWRITEBYTECODE="1", AIOS_BP_V4_PLUGIN_OUTPUT=str(output),
                AIOS_V2_PROFILE=json.dumps({k: profile[k] for k in (
                    "profile", "workers", "distribution", "max_worker_restart", "collect_only")}))
            command_env["PYTHONPATH"] = os.pathsep.join((str(checkout / "src"), str(checkout / "tests"), env.get("PYTHONPATH", "")))
            coverage = parse_pytest_coverage(command)
            observed = selected or affected or (coverage is not None and not coverage.measurement)
            if observed and not selected:
                # Hidden environment selection/maxfail flags cannot narrow an
                # authored requirement. Repository configuration is still bound
                # to the exact tree and completion is checked by the observer.
                command_env.pop("PYTEST_ADDOPTS", None)
                command_env["PYTEST_PLUGINS"] = ",".join(filter(None, (env.get("PYTEST_PLUGINS", ""), "bp_v4_probe_plugin")))
            actual_runner = runner
            if affected and platform == "nt":
                # Generated probes use POSIX canonical quoting in their audit
                # command, then mechanically encode the same argv for PowerShell.
                import shlex
                argv = shlex.split(command)
                encoded = " ".join("'" + a.replace("'", "''") + "'" for a in argv)
                def actual_runner(_command, **kwargs):
                    return runner(_shell_command("& " + encoded, platform=platform), **kwargs)
            raw_id = run_id + "-BASE" if base else run_id
            if (directory / f"{raw_id}-V{order:03d}.raw").exists():
                raise RuntimeVerificationError("V2 raw evidence already exists; refusing overwrite", evidence=evidence)
            items = execute_verification((command,), run_id=run_id + "-BASE" if base else run_id,
                subject_sha=sha, repository=checkout, raw_directory=directory, runner=actual_runner,
                platform=platform, environment=command_env, stop_on_failure=False, start_order=order)
            if subject_check is not None:
                subject_check(checkout, expected_head=sha, evidence=tuple(evidence) + items)
            elif _git(checkout, "rev-parse", "HEAD") != sha or _git(checkout, "status", "--porcelain"):
                raise RuntimeVerificationError("V2 verification changed its exact subject", evidence=tuple(evidence) + items)
            item = items[0]
            observation = _v2_observation(item, output, binding=binding, selected=selected) if observed else None
            if observation is not None:
                previous = _v2_known_observation(records, sha=sha, command=command, profile=profile, toolchain=binding["toolchain"])
                if previous is not None and verification_digest(previous) != verification_digest(observation):
                    observation.update(complete=False, unstable=True)
            return item, observation, binding

        try:
            for previous in plan.reused:
                command = previous["binding"]["command"]
                record = {**previous, "disposition": "REUSED", "reuse": {"binding": bindings[command], "source_evidence_id": previous["evidence_id"]}}
                item = Evidence(f"{run_id}-V{len(evidence) + 1:03d}", run_id, subject_sha, "VERIFICATION",
                    EvidenceSource(command), EvidenceOutcome(previous["candidate"]["exit_code"], "reused bound Runtime verification"),
                    previous["raw_path"], record)
                evidence.append(item)
                audit["records"].append({"evidence_id": item.evidence_id, "disposition": "REUSED", "source_evidence_id": previous["evidence_id"]})
            for command in (*plan.affected_first, *plan.commands):
                affected = command in plan.affected_first and command not in authored
                item, candidate, binding = run_once(command, sha=subject_sha, base=False, affected=affected)
                evidence.append(item)
                record = None
                if candidate is not None:
                    record = {"policy": MINIMUM_SUFFICIENT_V2, "disposition": "EXECUTED", "binding": binding, "candidate": candidate,
                              "candidate_digest": verification_digest(candidate),
                              "evidence_id": item.evidence_id, "run_id": run_id, "raw_path": item.raw_path,
                              "raw_digest": hashlib.sha256(Path(item.raw_path).read_bytes()).hexdigest()}
                    if item.result.exit_code != 0:
                        known = None if affected else base_known.get(command)
                        if known is None and not affected:
                            known = _v2_known_observation(records, sha=base_sha, command=command,
                                profile=_v2_profile(repository, base_sha, command, env), toolchain=binding["toolchain"])
                        # Evidence with a broken raw binding is already excluded
                        # from records. Reuse exact base observations when present.
                        if known is None or known.get("complete") is not True:
                            base_item, base_observation, _ = run_once(command, sha=base_sha, base=True, affected=affected)
                            record.update(base=base_observation, base_raw_path=base_item.raw_path,
                                          base_raw_digest=hashlib.sha256(Path(base_item.raw_path).read_bytes()).hexdigest())
                            if known is not None:
                                record["base"].update(complete=False, unstable=True)
                        else:
                            record["base"] = known
                            source = next(r for r in records if r.get("base") == known or r.get("candidate") == known)
                            source_path = source["base_raw_path"] if source.get("base") == known else source["raw_path"]
                            record.update(base_raw_path=source_path,
                                          base_raw_digest=hashlib.sha256(Path(source_path).read_bytes()).hexdigest())
                        record["binding"]["failure_set_digest"] = verification_digest(record["base"])
                        record["base_digest"] = verification_digest(record["base"])
                        record["attribution"] = attribute_failures(record["base"], candidate,
                            base_sha=base_sha, candidate_sha=subject_sha)
                    if affected:
                        record["binding"].update(base_sha=base_sha, tree_sha=tree,
                            envelope_digest=envelope_digest, changed_files_digest=verification_digest(changed),
                            failure_set_digest=record["binding"].get("failure_set_digest", verification_digest([])))
                        record["binding"].update(correction_base_sha=correction_base,
                            correction_changed_files_digest=verification_digest(correction_changed))
                    item = replace(item, verification=record)
                    evidence[-1] = item
                audit["records"].append({"evidence_id": item.evidence_id, "command": command, "disposition": "EXECUTED",
                                         "raw_exit_code": item.result.exit_code,
                                         "attribution": record.get("attribution", []) if record else []})
                passes = item.result.exit_code == 0 if record is None else attributed_task_passes(
                    record, subject_sha=subject_sha, command=command, exit_code=item.result.exit_code)
                if not passes:
                    blocking.append(command)
                if record is not None:
                    # Invalid/incomplete observations remain in the audit and raw
                    # evidence, but cannot enter the reusable proof population.
                    try:
                        validate_observation(candidate)
                        verification_digest(record)
                        path = cache_directory / f"{item.evidence_id}.json"
                        encoded = json.dumps(record, sort_keys=True, indent=2) + "\n"
                        if path.exists() and path.read_text(encoding="utf-8") != encoded:
                            raise RuntimeVerificationError("conflicting V2 canonical cache record", evidence=evidence)
                        path.write_text(encoded, encoding="utf-8")
                    except VerificationContractError:
                        pass
        except RuntimeVerificationError:
            raise
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
            raise RuntimeVerificationError(f"V2 execution failed closed: {exc}", evidence=evidence) from exc
        finally:
            audit_path = raw_directory / "minimum-sufficient-v2-plan.json"
            encoded = json.dumps(audit, sort_keys=True, indent=2) + "\n"
            if audit_path.exists() and audit_path.read_text(encoding="utf-8") != encoded:
                raise RuntimeVerificationError("conflicting V2 derivation audit", evidence=evidence)
            audit_path.write_text(encoded, encoding="utf-8")
    if blocking:
        raise RuntimeVerificationError("V2 verification remains blocking: " + ", ".join(blocking), evidence=evidence)
    return tuple(evidence)


def _shell_command(command: str, *, platform: str) -> tuple[str, ...]:
    if platform == "nt":
        return (
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"{_WINDOWS_POWERSHELL_UTF8_PREAMBLE}& {{ {command} }}; exit $LASTEXITCODE",
        )
    return ("/bin/sh", "-c", command)


def _verification_environment(
    *,
    environment: Mapping[str, str] | None,
    platform: str,
    temp_root: Path | None,
) -> dict[str, str]:
    env = dict(os.environ if environment is None else environment)
    if platform == "nt":
        if temp_root is None:
            raise RuntimeVerificationError(
                "Windows verification requires an isolated temporary root"
            )
        isolated = str(temp_root)
        overrides = {
            "TEMP": isolated,
            "TMP": isolated,
            "TMPDIR": isolated,
            "PYTHONIOENCODING": "utf-8",
        }
        for name in tuple(env):
            if name.upper() in overrides:
                del env[name]
        env.update(overrides)
    return env


def _isolated_temp_root(
    *,
    repository: Path,
    run_id: str,
    environment: Mapping[str, str] | None,
) -> Path:
    """Allocate one execution root outside the verified repository's Git ancestry."""

    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]", "-", run_id)
    prefix = f"{_WINDOWS_TEMP_PREFIX}{safe_run_id}-"
    errors: list[str] = []
    for base in _windows_temp_bases(repository, environment):
        temp_root: Path | None = None
        try:
            if (
                not base.is_dir()
                or base.is_relative_to(repository)
                or _has_git_ancestor(base)
            ):
                continue
            temp_root = Path(tempfile.mkdtemp(prefix=prefix, dir=base))
            temp_root = temp_root.resolve(strict=True)
            if temp_root.is_relative_to(repository) or _has_git_ancestor(temp_root):
                _remove_temp_root(temp_root)
                continue
            return temp_root
        except OSError as exc:
            if temp_root is not None and temp_root.exists():
                try:
                    _remove_temp_root(temp_root)
                except OSError as cleanup_exc:
                    raise RuntimeError(
                        f"temporary root cleanup after allocation failure failed: "
                        f"{cleanup_exc}"
                    ) from cleanup_exc
            errors.append(f"{base}: {exc}")
    detail = "; ".join(errors) or "no external temporary base is available"
    raise RuntimeError(detail)


def _windows_temp_bases(
    repository: Path, environment: Mapping[str, str] | None
) -> tuple[Path, ...]:
    source = os.environ if environment is None else environment
    normalized = {name.upper(): value for name, value in source.items()}
    candidates = [Path(tempfile.gettempdir())]
    if normalized.get("LOCALAPPDATA"):
        candidates.append(Path(normalized["LOCALAPPDATA"]) / "Temp")
    if normalized.get("USERPROFILE"):
        candidates.extend(
            [
                Path(normalized["USERPROFILE"])
                / "AppData"
                / "Local"
                / "Temp",
                Path(normalized["USERPROFILE"]),
            ]
        )
    if normalized.get("SYSTEMROOT"):
        candidates.append(Path(normalized["SYSTEMROOT"]) / "Temp")
    candidates.append(Path(repository.anchor))

    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return tuple(unique)


def _has_git_ancestor(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in (path, *path.parents))


def _materialization_git_environment(empty_config: Path) -> dict[str, str]:
    """Keep host Git configuration out of the transient subject's creation."""

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    env.update(
        GIT_CONFIG_GLOBAL=str(empty_config),
        GIT_CONFIG_SYSTEM=str(empty_config),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_ATTR_NOSYSTEM="1",
    )
    return env


def _git(
    repository: Path, *args: str, environment: Mapping[str, str] | None = None
) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repository), *args),
            capture_output=True,
            text=False,
            check=False,
            env=environment,
        )
        stdout = completed.stdout.decode("utf-8", errors="strict")
        stderr = completed.stderr.decode("utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Git invocation failed: {exc}") from exc
    if completed.returncode != 0:
        detail = stderr.strip() or stdout.strip()
        raise RuntimeError(f"Git command failed: {detail}")
    return stdout.strip()


def _optional_git(
    repository: Path, *args: str, environment: Mapping[str, str] | None = None
) -> str | None:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repository), *args),
            capture_output=True,
            text=False,
            check=False,
            env=environment,
        )
        stdout = completed.stdout.decode("utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Git invocation failed: {exc}") from exc
    return stdout.strip() if completed.returncode == 0 else None


def _remove_temp_root(temp_root: Path) -> None:
    deadline = time.monotonic() + _TEMP_CLEANUP_MAX_ELAPSED_SECONDS
    attempt = 0

    def make_writable_and_retry(remove, path: str, exc_info) -> None:
        _make_writable_and_retry(remove, path, exc_info, deadline=deadline)

    while True:
        try:
            shutil.rmtree(temp_root, onerror=make_writable_and_retry)
            break
        except OSError as exc:
            if not temp_root.exists():
                break
            if not _is_permission_error(exc):
                raise
            delay = _temp_cleanup_retry_delay(attempt, deadline=deadline)
            if delay is None:
                raise
            time.sleep(delay)
            attempt += 1
    if temp_root.exists():
        raise OSError(f"temporary root still exists: {temp_root}")


def _make_writable_and_retry(remove, path: str, exc_info, *, deadline: float) -> None:
    error = exc_info[1]
    if not _is_permission_error(error):
        raise error
    current_mode = os.stat(path, follow_symlinks=False).st_mode
    writable_mode = current_mode | stat.S_IREAD | stat.S_IWRITE
    if stat.S_ISDIR(current_mode):
        writable_mode |= stat.S_IEXEC
    os.chmod(path, writable_mode)
    attempt = 0
    while True:
        try:
            remove(path)
            return
        except OSError as exc:
            if not _is_permission_error(exc):
                raise
            delay = _temp_cleanup_retry_delay(attempt, deadline=deadline)
            if delay is None:
                raise
            time.sleep(delay)
            attempt += 1


def _temp_cleanup_retry_delay(attempt: int, *, deadline: float) -> float | None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    backoff = min(
        _TEMP_CLEANUP_INITIAL_RETRY_DELAY_SECONDS * (2 ** min(attempt, 30)),
        _TEMP_CLEANUP_MAX_RETRY_DELAY_SECONDS,
    )
    return min(backoff, remaining)


def _is_permission_error(error: BaseException) -> bool:
    return isinstance(error, PermissionError) or (
        isinstance(error, OSError) and error.errno in {errno.EACCES, errno.EPERM}
    )


def _require_bytes(value: bytes | str, stream: str) -> bytes:
    if not isinstance(value, bytes):
        raise RuntimeVerificationError(
            f"verification {stream} must be captured as bytes"
        )
    return value


def _strict_utf8_command(command: str) -> str:
    try:
        return command.encode("utf-8", errors="strict").decode(
            "utf-8", errors="strict"
        )
    except (AttributeError, UnicodeError) as exc:
        raise RuntimeVerificationError(
            "verification command must be strict UTF-8 text"
        ) from exc


def _summary(returncode: int, *, stdout: str, stderr: str) -> str:
    detail = stdout.strip() or stderr.strip()
    if detail:
        return detail
    return "verification passed" if returncode == 0 else "verification failed"
