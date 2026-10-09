"""Deterministic Runtime-owned execution of canonical verification commands."""

from __future__ import annotations

import errno
import hashlib
import importlib.metadata
import json
import os
import platform as platform_module
import re
import shlex
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
    candidate_delta_projection, exact_collection_observation,
    projected_failure_attribution, pytest_collection_command, pytest_projection_command,
    validate_exact_collection, validate_exact_projection, validate_failure_reproduction,
)


VerificationRunner = Callable[..., subprocess.CompletedProcess[bytes]]
_WINDOWS_POWERSHELL_UTF8_PREAMBLE = (
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
)
_WINDOWS_TEMP_PREFIX = "aios-verification-"
_TEMP_CLEANUP_MAX_ELAPSED_SECONDS = 5.0
_TEMP_CLEANUP_INITIAL_RETRY_DELAY_SECONDS = 0.05
_TEMP_CLEANUP_MAX_RETRY_DELAY_SECONDS = 0.5
_MAX_CLEANUP_DIAGNOSTIC_CHARS = 1024
_MAX_CLEANUP_DIAGNOSTICS = 4


class RuntimeVerificationError(RuntimeError):
    """Raised when deterministic Runtime verification fails closed."""

    def __init__(self, message: str, *, evidence: Iterable[Evidence] = ()) -> None:
        super().__init__(message)
        self.evidence = tuple(evidence)
        self.cleanup_diagnostics: tuple[str, ...] = ()


def _cleanup_verification_root(
    temp_root: Path, *, primary: BaseException | None = None,
    evidence: Iterable[Evidence] = (),
) -> None:
    """Keep finite cleanup subordinate to a primary cause, otherwise block."""
    try:
        _remove_temp_root(temp_root)
    except OSError as exc:
        if primary is None:
            raise RuntimeVerificationError(_cleanup_failure_detail(exc), evidence=evidence) from exc
        _retain_cleanup_failure(primary, exc)


def _cleanup_failure_detail(exc: OSError) -> str:
    detail = f"verification environment could not be cleaned: {exc}"
    if len(detail) > _MAX_CLEANUP_DIAGNOSTIC_CHARS:
        detail = detail[:_MAX_CLEANUP_DIAGNOSTIC_CHARS - 3] + "..."
    return detail


def _retain_cleanup_failure(primary: BaseException, exc: OSError) -> None:
    detail = _cleanup_failure_detail(exc)
    if isinstance(primary, RuntimeVerificationError):
        if len(primary.cleanup_diagnostics) >= _MAX_CLEANUP_DIAGNOSTICS:
            return
        primary.cleanup_diagnostics += (detail,)
    else:
        notes = getattr(primary, "__notes__", ())
        if sum(n.startswith("verification environment could not be cleaned:")
               for n in notes) >= _MAX_CLEANUP_DIAGNOSTICS:
            return
    primary.add_note(detail)


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
    Cleanup preserves an earlier verification exception and exposes bounded
    diagnostics. With no earlier failure, unrecoverable cleanup blocks.
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
    except (OSError, UnicodeError, RuntimeError) as exc:
        primary = exc if isinstance(exc, RuntimeVerificationError) else RuntimeVerificationError(
            f"verification subject could not be materialized: {exc}"
        )
        if temp_root is not None:
            _cleanup_verification_root(temp_root, primary=primary)
        if primary is exc:
            raise
        raise primary from exc
    except BaseException as exc:
        if temp_root is not None:
            _cleanup_verification_root(temp_root, primary=exc)
        raise

    primary = None
    try:
        yield subject
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if temp_root is not None:
            _cleanup_verification_root(temp_root, primary=primary)


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
    primary: BaseException | None = None
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
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if temp_root is not None:
            try:
                _remove_temp_root(temp_root)
            except OSError as exc:
                # A raw nonzero may have been returned for later V2 attribution.
                # If cleanup now blocks that return, retain the raw failure as
                # the primary cause rather than substituting a cleanup verdict.
                pending = next((item for item in evidence if item.result.exit_code != 0), None)
                if primary is None and pending is not None:
                    primary = RuntimeVerificationError(
                        f"verification command failed with exit code "
                        f"{pending.result.exit_code}: {pending.source.command}",
                        evidence=evidence,
                    )
                    _retain_cleanup_failure(primary, exc)
                    raise primary from None
                if primary is None:
                    raise RuntimeVerificationError(
                        _cleanup_failure_detail(exc), evidence=evidence,
                    ) from exc
                _retain_cleanup_failure(primary, exc)

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


def execute_publication_verification(*, task, run, plan, source_run,
        source_package, subject_sha: str, repository: Path, raw_directory: Path,
        cache_directory: Path, runner: VerificationRunner = subprocess.run,
        environment: Mapping[str, str] | None = None) -> tuple[tuple[Evidence, ...], dict]:
    """Bounded Runtime decision for a publication source continuation.

    No dependency inference and no parallel scheduler. The entire tracked tree
    is the conservative read set. A changed planning file alone does not prove
    independence: absent an established equivalent context, execute the authored
    bounded requirements through the existing Runtime strategy. Zero-failure
    proof can be reused only with exact tree, observer, helper/config, environment,
    toolchain, policy/envelope and immutable raw provenance. Base-attributed
    failures are never transplanted to another integration base.
    """
    from .artifacts import ResultPackage, validate_evidence, verification_requirement_passes
    from .correction_integration import publication_tree_entries
    from .runtime import result_package_data
    env = dict(os.environ if environment is None else environment)
    raw_directory.mkdir(parents=True, exist_ok=True)
    tree_equal = (publication_tree_entries(repository, plan.reviewed_sha) == publication_tree_entries(repository, subject_sha)
                  and publication_tree_entries(repository, source_run.base_sha) == publication_tree_entries(repository, run.base_sha))
    toolchain = _v2_toolchain()
    source_items = {command: [item for item in source_package.evidence
                             if item.source.command == command]
                    for command in task.verification.required}
    audit = {"format": "AIOS_PUBLICATION_EVIDENCE_DECISION", "version": 1,
        "recovery_identity": plan.identity, "source_run_id": source_run.run_id,
        "source_artifacts_sha": plan.artifacts_sha, "source_decision_sha": plan.decision_sha,
        "source_sha": plan.reviewed_sha, "candidate_sha": subject_sha,
        "main_sha": plan.main_sha, "read_set": "ENTIRE_TRACKED_TREE",
        "records": [], "integration_obligation": "EXACT_SOURCE_AND_MAIN_PRESERVATION"}
    reused, execute = [], []
    for order, command in enumerate(dict.fromkeys(task.verification.required), 1):
        matches = source_items[command]
        item = matches[0] if len(matches) == 1 else None
        disposition = "UNKNOWN"
        if not tree_equal:
            disposition = "INVALIDATED"
        record = None if item is None else item.verification
        try:
            if (item is None or record is None or task.verification.policy != MINIMUM_SUFFICIENT_V2
                    or "reuse" in record or "base" in record or "failure_projection" in record
                    or item.run_id != source_run.run_id or item.subject_sha != plan.reviewed_sha):
                raise ValueError("no uniquely comparable original proof")
            validate_evidence(result_package_data(ResultPackage(Result("unused", (), (), ()), (item,)))["evidence"][0])
            validate_observation(record["candidate"])
            binding = record["binding"]
            profile = _v2_profile(repository, subject_sha, command, env)
            envelope = verification_digest({"authored": tuple(dict.fromkeys(task.verification.required)),
                "operation": "PRIMARY", "modification_scope": sorted(task.scope.modify)})
            if (not tree_equal or item.result.exit_code != 0
                    or record["candidate"]["failure_count"] != 0
                    or not verification_requirement_passes(item, policy=task.verification.policy)
                    or binding["subject_sha"] != plan.reviewed_sha
                    or binding["base_sha"] != source_run.base_sha
                    or binding["changed_files_digest"] != verification_digest(tuple(sorted(path for path, _, _ in plan.delta)))
                    or binding["tree_sha"] != _git(repository, "rev-parse", f"{plan.reviewed_sha}^{{tree}}")
                    or binding["command"] != command or binding["profile"] != profile
                    or binding["toolchain"] != toolchain or binding["envelope_digest"] != envelope):
                disposition = "INVALIDATED"
                raise ValueError("changed proof conditions")
            original_raw = Path(item.raw_path).read_bytes()
            if hashlib.sha256(original_raw).hexdigest() != record["raw_digest"]:
                raise ValueError("missing immutable raw provenance")
            disposition = "VALID"
            evidence_id = f"{run.run_id}-REUSE-{order:03d}"
            path = raw_directory / f"{evidence_id}.json"
            receipt = {"decision": "REUSED", "source_evidence_id": item.evidence_id,
                       "source_run_id": item.run_id, "source_artifacts_sha": plan.artifacts_sha,
                       "source_verification": record, "source_raw_digest": record["raw_digest"],
                       "candidate_sha": subject_sha, "conditions": "EXACT_CANDIDATE_AND_BASE_TREES_AND_CONTEXT"}
            if path.exists():
                raise RuntimeVerificationError("publication reuse receipt already exists; no proof replay")
            path.write_bytes((json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8"))
            changed = tuple(sorted(path for path, _, _ in plan.delta))
            current_binding = {**binding, "subject_sha": subject_sha, "base_sha": run.base_sha,
                "tree_sha": plan.tree_sha, "correction_base_sha": run.base_sha,
                "changed_files_digest": verification_digest(changed),
                "correction_changed_files_digest": verification_digest(changed),
                "failure_set_digest": verification_digest([])}
            candidate = {**record["candidate"], "subject_sha": subject_sha}
            # This is explicitly a Runtime-derived validity observation. Its raw
            # source is the reuse receipt, retaining the actual executed subject.
            derived = {"policy": MINIMUM_SUFFICIENT_V2, "evidence_id": evidence_id,
                "binding": current_binding, "candidate": candidate,
                "candidate_digest": verification_digest(candidate),
                "raw_path": str(path), "raw_digest": hashlib.sha256(path.read_bytes()).hexdigest()}
            reused.append(Evidence(evidence_id, run.run_id, subject_sha, "VERIFICATION",
                EvidenceSource(command), EvidenceOutcome(0, "Runtime VALID: reused " + item.evidence_id),
                str(path), derived))
        except (OSError, KeyError, ValueError, TypeError, RuntimeError):
            # Unknown/invalid proof never becomes PASS through tree equality.
            if disposition == "VALID":
                raise
            execute.append(command)
        audit["records"].append({"command": command, "validity": disposition,
            "disposition": "REUSED" if disposition == "VALID" else "EXECUTED",
            "source_evidence_ids": [item.evidence_id for item in matches]})
    executed = ()
    if execute:
        if task.verification.policy == MINIMUM_SUFFICIENT_V2:
            executed = execute_minimum_verification(execute, run_id=run.run_id,
                base_sha=run.base_sha, subject_sha=subject_sha, repository=repository,
                raw_directory=raw_directory / "executed", cache_directory=cache_directory,
                modification_scope=task.scope.modify, operation="PRIMARY", runner=runner,
                environment=env)
        else:
            with materialize_verification_subject(repository, run_id=run.run_id,
                                                   subject_sha=subject_sha) as subject:
                executed = execute_verification(execute, run_id=run.run_id,
                    subject_sha=subject_sha, repository=subject,
                    raw_directory=raw_directory / "executed", runner=runner, environment=env)
    # Distinct integration proof is mandatory even when every authored proof was
    # reused. This guard establishes source/blob preservation, never semantic PASS.
    parents = _git(repository, "rev-parse", f"{subject_sha}^@").splitlines()
    entries = publication_tree_entries(repository, subject_sha)
    if (parents != [plan.main_sha, plan.reviewed_sha]
            or _git(repository, "rev-parse", f"{subject_sha}^{{tree}}") != plan.tree_sha
            or any(entries.get(path) != (None if mode is None else (mode, blob))
                   for path, mode, blob in plan.delta)):
        raise RuntimeVerificationError("publication integration guard failed", evidence=(*reused, *executed))
    path = raw_directory / "publication-integration.json"
    path.write_text(json.dumps(audit, sort_keys=True) + "\n", encoding="utf-8")
    guard = Evidence(run.run_id + "-INTEGRATION", run.run_id, subject_sha,
        "PUBLICATION_INTEGRATION", EvidenceSource("aios-publication-source-preservation-v1"),
        EvidenceOutcome(0, "Exact original blobs and unrelated current-main content preserved"), str(path))
    return (*reused, *executed, guard), audit


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
                         if k.upper() not in {"TEMP", "TMP", "TMPDIR", "AIOS_BP_V4_PLUGIN_OUTPUT", "AIOS_V2_PROFILE", "AIOS_V2_OBSERVATION_DIRECTORY", "AIOS_V2_EXACT_COLLECTION"}}
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
            if "early_probe" in value:
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
            if "failure_projection" in value:
                projection = value["failure_projection"]
                if (value.get("failure_projection_digest") != verification_digest(projection)
                        or value["binding"]["failure_set_digest"] != value["failure_projection_digest"]):
                    continue
                for key in ("candidate", "base_collection", "base"):
                    if key not in projection:
                        continue
                    validate_observation(projection[key])
                    if projection.get(key + "_digest") != verification_digest(projection[key]):
                        raise VerificationContractError("cached projection digest mismatch")
                    projection_raw = Path(projection[key + "_raw_path"]).read_bytes()
                    if hashlib.sha256(projection_raw).hexdigest() != projection[key + "_raw_digest"]:
                        raise VerificationContractError("cached projection raw binding mismatch")
                classifications = projected_failure_attribution(projection, value["candidate"],
                    command=value["binding"]["command"], base_sha=value["binding"]["base_sha"],
                    candidate_sha=value["binding"]["subject_sha"])
                if tuple(value.get("attribution", ())) != classifications:
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
        if not selected and "exact_collection" in envelope:
            value["collection"] = envelope["exact_collection"]
            validate_exact_collection(value["collection"])
            if value["collection"]["identity"] != envelope.get("controller_collection"):
                valid_conditions = False
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

    Reuse is conservative (complete tracked tree equality). Bounded candidate
    deltas and exact previous failures are early failure gates; their success
    never discharges authored proof. Reproduced exact failures prefer projected
    base attribution, with the existing broad path as conservative fallback.
    No correction or integration guard is selected by this execution strategy.
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
             "affected_first": plan.affected_first, "fallback": plan.fallback,
             "records": [], "probes": [], "projection_fallback": []}
    blocking = []
    with ExitStack() as subjects:
        candidate_repository = None
        base_repository = None
        execution_order = 0
        observations = []
        collections = {}

        def run_once(command: str, *, sha: str, base: bool, affected: bool = False,
                     authored_command: str | None = None, collect_only: bool = False) -> tuple[Evidence, dict | None, dict]:
            nonlocal candidate_repository, base_repository, execution_order
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
            execution_order = max(execution_order, len(evidence)) + 1
            order = execution_order
            directory = raw_directory / ("base" if base else "candidate")
            output = directory / f"observation-{order:03d}.json"
            profile = _v2_profile(repository, sha, authored_command or command, env, affected=affected)
            profile["collect_only"] = collect_only
            binding = {**bindings.get(authored_command or command, {}),
                       "base_sha": base_sha, "tree_sha": _git(repository, "rev-parse", f"{sha}^{{tree}}"),
                       "envelope_digest": envelope_digest, "changed_files_digest": verification_digest(changed),
                       "failure_set_digest": bindings.get(authored_command or command, {}).get("failure_set_digest", verification_digest([])),
                       "correction_base_sha": correction_base,
                       "correction_changed_files_digest": verification_digest(correction_changed),
                       "subject_sha": sha, "command": command,
                       "profile": profile, "toolchain": toolchain}
            if authored_command is not None:
                binding["projection_of"] = authored_command
            elif affected:
                binding["projection_of"] = dict(plan.affected_requirements)[command]
            selected = command == SELECTED_FULL_SUITE_COMMAND
            command_env = dict(env)
            command_env.update(PYTHONDONTWRITEBYTECODE="1", AIOS_BP_V4_PLUGIN_OUTPUT=str(output),
                AIOS_V2_PROFILE=json.dumps({k: profile[k] for k in (
                    "profile", "workers", "distribution", "max_worker_restart", "collect_only")}))
            command_env["PYTHONPATH"] = os.pathsep.join((str(checkout / "src"), str(checkout / "tests"), env.get("PYTHONPATH", "")))
            coverage = parse_pytest_coverage(command)
            observed = selected or affected or collect_only or authored_command is not None or (coverage is not None and not coverage.measurement)
            if observed and not selected:
                # Hidden environment selection/maxfail flags cannot narrow an
                # authored requirement. Repository configuration is still bound
                # to the exact tree and completion is checked by the observer.
                command_env.pop("PYTEST_ADDOPTS", None)
                command_env["PYTEST_PLUGINS"] = ",".join(filter(None, (env.get("PYTEST_PLUGINS", ""), "bp_v4_probe_plugin")))
                command_env["AIOS_V2_EXACT_COLLECTION"] = "1"
            else:
                command_env.pop("AIOS_V2_EXACT_COLLECTION", None)
            actual_runner = runner
            if (affected or collect_only or authored_command is not None) and platform == "nt":
                # Windows PowerShell's legacy native argument marshalling loses
                # embedded double quotes. Submit the canonical generated argv
                # directly; subprocess encodes it losslessly for CreateProcess.
                argv = tuple(shlex.split(command))
                def actual_runner(_command, **kwargs):
                    return runner(argv, **kwargs)
            raw_id = run_id + "-BASE" if base else run_id
            if output.exists() or (directory / f"{raw_id}-V{order:03d}.raw").exists():
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
                for previous in observations:
                    if (all(previous.get(k) == observation.get(k) for k in ("subject_sha", "command", "profile", "toolchain"))
                            and verification_digest(previous) != verification_digest(observation)):
                        observation.update(complete=False, unstable=True)
                observations.append(observation)
            return item, observation, binding

        def observed_record(item: Evidence, observation: dict, binding: dict, *, early: bool = False) -> dict:
            record = {"policy": MINIMUM_SUFFICIENT_V2, "disposition": "EXECUTED",
                      "binding": binding, "candidate": observation,
                      "candidate_digest": verification_digest(observation),
                      "evidence_id": item.evidence_id, "run_id": run_id, "raw_path": item.raw_path,
                      "raw_digest": hashlib.sha256(Path(item.raw_path).read_bytes()).hexdigest()}
            if early:
                record["early_probe"] = True
            return record

        def probe_record(item: Evidence, observation: dict, binding: dict, *, kind: str) -> dict:
            record = observed_record(item, observation, binding, early=True)
            # Keep one bounded, immutable canonical population per probe. The
            # plan links it by digest instead of duplicating whole collections
            # and phase populations in an unbounded aggregate audit.
            verification_digest(record)
            directory = raw_directory / "projections"
            directory.mkdir(exist_ok=True)
            path = directory / f"{Path(item.raw_path).stem}.json"
            encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            if path.exists() and path.read_bytes() != encoded:
                raise RuntimeVerificationError("conflicting V2 projection record", evidence=evidence)
            path.write_bytes(encoded)
            audit["probes"].append({"evidence_id": item.evidence_id, "kind": kind, "early_probe": True,
                "command": item.source.command, "record_path": str(path),
                "record_digest": hashlib.sha256(encoded).hexdigest()})
            return record

        def projection_material(item: Evidence, observation: dict, *, key: str) -> dict:
            return {key: observation, key + "_digest": verification_digest(observation),
                    key + "_raw_path": item.raw_path,
                    key + "_raw_digest": hashlib.sha256(Path(item.raw_path).read_bytes()).hexdigest()}

        def collect(command: str, *, base: bool) -> tuple[Evidence, dict]:
            sha = base_sha if base else subject_sha
            item, observation, binding = run_once(pytest_collection_command(command), sha=sha, base=base,
                                                authored_command=command, collect_only=True)
            probe_record(item, observation, binding, kind="EXACT_COLLECTION")
            exact_collection_observation(observation, command=command, subject_sha=sha)
            collections.setdefault(command, {})["base" if base else "candidate"] = (item, observation)
            return item, observation

        def delta_first(command: str) -> None:
            coverage = parse_pytest_coverage(command)
            if (operation != "PRIMARY" or coverage is None or coverage.is_full_suite
                    or coverage.measurement or coverage.selected_parallel or coverage.filter_expression is not None):
                return
            try:
                # A known condition mismatch already makes exact comparison
                # ineligible; no collection subprocess is needed to rediscover it.
                if bindings[command]["profile"] != _v2_profile(repository, base_sha, command, env):
                    raise VerificationContractError("base/candidate execution profiles differ")
                candidate_item, candidate_collection = collect(command, base=False)
                base_item, base_collection = collect(command, base=True)
                nodes = candidate_delta_projection(base_collection, candidate_collection, command=command,
                                                   base_sha=base_sha, candidate_sha=subject_sha)
                probe = pytest_projection_command(command, nodes)
                item, candidate, binding = run_once(probe, sha=subject_sha, base=False, authored_command=command)
                record = probe_record(item, candidate, binding, kind="CANDIDATE_DELTA")
                validate_exact_projection(candidate, command=command, nodeids=nodes, subject_sha=subject_sha)
                if (candidate["profile"] != {**candidate_collection["profile"], "collect_only": False}
                        or candidate["toolchain"] != candidate_collection["toolchain"]):
                    raise VerificationContractError("delta probe conditions changed after collection")
                if item.result.exit_code == 1:
                    record["delta"] = {"nodeids": list(nodes),
                        **projection_material(base_item, base_collection, key="base_collection"),
                        **projection_material(candidate_item, candidate_collection, key="candidate_collection")}
                    record["binding"]["failure_set_digest"] = verification_digest(record["delta"])
                    record["attribution"] = tuple({"nodeid": node, "phase": phase, "classification": "CANDIDATE_ONLY"}
                                                  for node, phase in failed_identities(candidate))
                    evidence.append(replace(item, verification=record))
                    audit["records"].append({"evidence_id": item.evidence_id, "command": probe,
                        "disposition": "EARLY_BLOCK", "raw_exit_code": item.result.exit_code,
                        "attribution": record["attribution"]})
                    raise RuntimeVerificationError("V2 candidate-only delta remains blocking: " + command, evidence=evidence)
            except VerificationContractError as exc:
                audit["projection_fallback"].append({"command": command, "kind": "CANDIDATE_DELTA", "reason": str(exc)})
            # Neither success nor ineligibility discharges the authored command.

        def failure_projection(command: str, candidate: dict) -> dict | None:
            try:
                validate_observation(candidate)
                if candidate["exit_code"] != 1:
                    raise VerificationContractError("candidate failure is not a complete runtest failure")
                nodes = tuple(sorted({node for node, _ in failed_identities(candidate)}))
                probe = pytest_projection_command(command, nodes)
                # Selected parallel/measurement profiles deliberately use the
                # existing broad path; serial exact probes cannot replace them.
                validate_exact_collection(candidate.get("collection"))
                item, reproduced, binding = run_once(probe, sha=subject_sha, base=False, authored_command=command)
                probe_record(item, reproduced, binding, kind="FAILURE_REPRODUCTION")
                validate_failure_reproduction(candidate, reproduced, command=command, candidate_sha=subject_sha)
                projection = {"command": probe, "nodeids": list(nodes),
                              **projection_material(item, reproduced, key="candidate")}
                base_item, base_collection = collections.get(command, {}).get("base", (None, None))
                if base_collection is None:
                    base_item, base_collection = collect(command, base=True)
                projection.update(projection_material(base_item, base_collection, key="base_collection"))
                before = exact_collection_observation(base_collection, command=command, subject_sha=base_sha)
                if (reproduced["profile"] != {**base_collection["profile"], "collect_only": False}
                        or reproduced["toolchain"] != base_collection["toolchain"]):
                    raise VerificationContractError("base collection conditions are not comparable to candidate reproduction")
                if not set(nodes).difference(before):
                    base_item, base_observation, binding = run_once(probe, sha=base_sha, base=True, authored_command=command)
                    probe_record(base_item, base_observation, binding, kind="EXACT_BASE_ATTRIBUTION")
                    projection.update(projection_material(base_item, base_observation, key="base"))
                # This recomputes conditions, collection agreement, exact
                # reproduction and fingerprints before authorizing any outcome.
                projected_failure_attribution(projection, candidate, command=command,
                                              base_sha=base_sha, candidate_sha=subject_sha)
                return projection
            except (VerificationContractError, KeyError, TypeError, ValueError) as exc:
                audit["projection_fallback"].append({"command": command, "kind": "FAILURE_ATTRIBUTION", "reason": str(exc)})
                return None

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
                if not affected:
                    delta_first(command)
                item, candidate, binding = run_once(command, sha=subject_sha, base=False, affected=affected)
                if affected:
                    record = probe_record(item, candidate, binding, kind="PREVIOUS_FAILURE")
                    if item.result.exit_code != 0:
                        # A validly bound correction probe is a failure gate.
                        # Even incomplete diagnostics cannot make its raw
                        # nonzero status permissive or require broad replay.
                        record["binding"]["failure_set_digest"] = verification_digest(
                            correction_known[dict(plan.affected_requirements)[command]])
                        evidence.append(replace(item, verification=record))
                        audit["records"].append({"evidence_id": item.evidence_id, "command": command,
                            "disposition": "EARLY_BLOCK", "raw_exit_code": item.result.exit_code})
                        raise RuntimeVerificationError("V2 previous-failure probe remains blocking: " + command, evidence=evidence)
                    continue
                collected = collections.get(command, {}).get("candidate", (None, None))[1]
                if candidate is not None and collected is not None:
                    if (candidate["profile"] != {**collected["profile"], "collect_only": False}
                            or candidate["toolchain"] != collected["toolchain"]
                            or ("collection" in candidate and candidate["collection"] != collected["collection"])):
                        candidate.update(complete=False, unstable=True)
                evidence.append(item)
                record = None
                if candidate is not None:
                    record = observed_record(item, candidate, binding)
                    if command in plan.affected_first and item.result.exit_code != 0:
                        evidence[-1] = replace(item, verification=record)
                        audit["records"].append({"evidence_id": item.evidence_id, "command": command,
                            "disposition": "EARLY_BLOCK", "raw_exit_code": item.result.exit_code})
                        raise RuntimeVerificationError("V2 authored previous-failure requirement remains blocking: " + command, evidence=evidence)
                    if item.result.exit_code != 0:
                        known = base_known.get(command)
                        if known is None:
                            known = _v2_known_observation(records, sha=base_sha, command=command,
                                profile=_v2_profile(repository, base_sha, command, env), toolchain=binding["toolchain"])
                        projection = failure_projection(command, candidate) if known is None else None
                        if projection is not None:
                            record["failure_projection"] = projection
                            digest = verification_digest(projection)
                            record["failure_projection_digest"] = digest
                            record["binding"]["failure_set_digest"] = digest
                            record["attribution"] = projected_failure_attribution(projection, candidate,
                                command=command, base_sha=base_sha, candidate_sha=subject_sha)
                        # Evidence with a broken raw binding is already excluded
                        # from records. Reuse exact base observations when present.
                        elif known is None or known.get("complete") is not True:
                            base_item, base_observation, _ = run_once(command, sha=base_sha, base=True, affected=affected)
                            record.update(base=base_observation, base_raw_path=base_item.raw_path,
                                          base_raw_digest=hashlib.sha256(Path(base_item.raw_path).read_bytes()).hexdigest())
                            if known is not None:
                                record["base"].update(complete=False, unstable=True)
                            collected_base = collections.get(command, {}).get("base", (None, None))[1]
                            if collected_base is not None and (
                                    record["base"]["profile"] != {**collected_base["profile"], "collect_only": False}
                                    or record["base"]["toolchain"] != collected_base["toolchain"]
                                    or ("collection" in record["base"] and record["base"]["collection"] != collected_base["collection"])):
                                record["base"].update(complete=False, unstable=True)
                        elif projection is None:
                            record["base"] = known
                            source = next(r for r in records if r.get("base") == known or r.get("candidate") == known)
                            source_path = source["base_raw_path"] if source.get("base") == known else source["raw_path"]
                            record.update(base_raw_path=source_path,
                                          base_raw_digest=hashlib.sha256(Path(source_path).read_bytes()).hexdigest())
                        if projection is None:
                            record["binding"]["failure_set_digest"] = verification_digest(record["base"])
                            record["base_digest"] = verification_digest(record["base"])
                            record["attribution"] = attribute_failures(record["base"], candidate,
                                base_sha=base_sha, candidate_sha=subject_sha)
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
            # Raise before the subject contexts unwind so their cleanup failures
            # remain subordinate to this established blocking verification cause.
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
