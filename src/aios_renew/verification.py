"""Deterministic Runtime-owned execution of canonical verification commands."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import yaml
import tempfile
import time
import tomllib
import threading
from collections.abc import Callable, Iterable, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator

from .artifacts import Claim, Evidence, EvidenceOutcome, EvidenceSource, Result
from .verification_contract import (
    MINIMUM_SUFFICIENT_V2, MAX_CANONICAL_BYTES, SELECTED_FULL_SUITE_COMMAND,
    VerificationContractError, attribute_failures, attributed_task_passes,
    derive_minimum_verification, failed_identities, parse_pytest_coverage,
    validate_observation, verification_digest,
    validate_evidence_binding,
    candidate_delta_projection, exact_collection_observation,
    projected_failure_attribution, pytest_collection_command, pytest_projection_command,
    validate_exact_collection, validate_exact_projection, validate_failure_reproduction,
    pytest_execution_profile,
    OriginalEvidenceRequest, OriginalProofDimension, OriginalProofProvenance,
    ORIGINAL_PROOF_DIMENSIONS, MAX_ORIGINAL_PROOF_BYTES, MAX_ORIGINAL_RAW_BYTES,
    _original_decode, _original_json, format_original_proof,
    APPLICABILITY_VERSION, APPLICABILITY_DIMENSIONS, APPLICABILITY_GIT_DIMENSIONS,
    APPLICABILITY_CONDITIONS_FORMAT, MAX_APPLICABILITY_PATHS,
    ApplicabilityCondition, ApplicabilityDimension, ApplicabilityAuthority,
    ApplicabilityTargetProvenance, ProofApplicabilityRequest, ProofApplicabilityWitness,
    _app_exact, _app_fields, _app_path, _original_text, _original_hash,
    applicability_target_data, format_applicability,
    applicability_source_snapshot,
    ProofReuseRequest, ProofReuseWitness, format_reuse, _reuse_primary_bound,
    _reuse_reviewed,
    ProofSchedulerRequest, ProofSchedulerPlan,
)
from .parallel_verification import (
    EXECUTION_CONTRACT, ProbeError, effective_pytest_profile, git_executable,
    load_toolchain, pytest_worker_options, validate_pytest_execution,
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
            if raw_path.exists():
                raise RuntimeVerificationError("verification raw evidence already exists; refusing overwrite", evidence=evidence)
            shell_command = _shell_command(exact_command, platform=platform)
            command_env = dict(env)
            try:
                tokens = shlex.split(exact_command)
            except ValueError:
                tokens = []  # The unchanged shell command retains its raw syntax failure.
            if tokens[:3] == ["python", "-m", "pytest"] or tokens[:1] == ["pytest"]:
                arguments = tokens[3:] if tokens[:3] == ["python", "-m", "pytest"] else tokens[1:]
                try:
                    effective_pytest_profile(arguments, environment=command_env)
                except ProbeError as exc:
                    raise RuntimeVerificationError(f"conflicting pytest execution environment: {exc}", evidence=evidence) from exc
                # Hidden selectors cannot narrow an exact authored subset.
                # Reject conflicting worker controls before removing addopts.
                command_env.pop("PYTEST_ADDOPTS", None)
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
            with raw_path.open("xb") as stream:
                stream.write(b"STDOUT\n" + stdout + b"\nSTDERR\n" + stderr)
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
    return load_toolchain()


def _v2_profile(repository: Path, sha: str, command: str, environment: Mapping[str, str],
                *, affected: bool = False) -> dict:
    selected = command == SELECTED_FULL_SUITE_COMMAND
    options = {"workers": 12, "distribution": "load", "max_worker_restart": 0, "collect_only": False}
    if not selected:
        configuration = _optional_git(repository, "show", f"{sha}:pyproject.toml")
        settings = tomllib.loads(configuration or "").get("tool", {}).get("pytest", {}).get("ini_options", {})
        addopts = settings.get("addopts", "")
        configured = pytest_worker_options(shlex.split(addopts) if isinstance(addopts, str) else addopts)
        # Historical subjects without a default really execute in-process.
        default = configured["workers"][-1] if configured["workers"] else "0"
        if not default.isdecimal() or any(len(set(configured[k])) > 1 for k in ("workers", "distribution", "restart")):
            raise RuntimeVerificationError("conflicting committed pytest worker configuration")
        if (configured["unsupported"] or configured["distribution"] not in ([], ["load"])
                or configured["restart"] not in ([], ["0"])):
            raise RuntimeVerificationError("incompatible committed pytest worker configuration")
        tokens = shlex.split(command)
        arguments = tokens[3:] if tokens[:3] == ["python", "-m", "pytest"] else tokens[1:]
        options = effective_pytest_profile(arguments, environment=environment, default_workers=int(default))
    description = {
        "profile": "bounded-parallel-full-suite-v1" if selected else "exact-affected-pytest-v2" if affected else "pytest-observed-v2",
        **options, "execution_contract": EXECUTION_CONTRACT,
    }
    if selected:
        from .verification_profile import selected_profile_identity
        description.update(selected_profile_identity(yaml.safe_load(
            _git(repository, "show", f"{sha}:.ai/verification-profiles.yaml"))))
    # Configuration and observer identity are mechanical material, not inferred
    # dependencies. An altered observer or selected policy prevents comparison.
    observer = _optional_git(repository, "rev-parse", f"{sha}:tests/bp_v4_probe_plugin.py")
    policy = _optional_git(repository, "rev-parse", f"{sha}:.ai/verification-profiles.yaml")
    tool_paths = ["pyproject.toml", "src/aios_renew/verification_contract.py", "tests/conftest.py",
                  "src/aios_renew/parallel_verification.py", "tests/git_fixture_support.py"]
    if selected:
        tool_paths.extend(("scripts/aios_parallel_full_suite.py",
                           "src/aios_renew/verification_profile.py"))
    owned_tools = {path: _optional_git(repository, "rev-parse", f"{sha}:{path}") for path in tool_paths}
    bound_environment = {k: v for k, v in environment.items()
                         if k.upper() not in {"TEMP", "TMP", "TMPDIR", "AIOS_BP_V4_PLUGIN_OUTPUT", "AIOS_V2_PROFILE", "AIOS_V2_OBSERVATION_DIRECTORY", "AIOS_V2_EXACT_COLLECTION", "AIOS_VERIFY_SUBJECT_SHA"}}
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
    try:
        for value in matches:
            validate_observation(value)
    except VerificationContractError:
        result.update(complete=False, unstable=True)
    if len({_v2_observation_truth(v) for v in matches}) != 1:
        result.update(complete=False, unstable=True)
    return result


def _v2_observation_truth(value: dict) -> str:
    # Process IDs and writable roots are per-execution proof, not comparable
    # workload conditions. Validate that proof before comparing phase truth;
    # the immutable record digest still covers every original process fact.
    if value.get("profile", {}).get("execution_contract") == EXECUTION_CONTRACT:
        try:
            validate_observation(value)
        except VerificationContractError:
            return verification_digest(value)
        return verification_digest({key: item for key, item in value.items() if key != "execution"})
    return verification_digest(value)


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
    observed_toolchain = None
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
            execution = {**result["execution"], "profile": observed_profile, "toolchain": observed_toolchain,
                         "failure_diagnostics": {"canonical": canonical}}
            validate_pytest_execution(execution, observed_profile, binding["toolchain"], subject_sha=item.subject_sha)
            valid_conditions = (valid_conditions and envelope["subject"].get("head_sha") == item.subject_sha
                                and envelope["toolchain"] == observed_toolchain)
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
            validate_pytest_execution(envelope, observed_profile, binding["toolchain"], subject_sha=item.subject_sha)
        description = {k: binding["profile"][k] for k in (
            "profile", "workers", "distribution", "max_worker_restart", "collect_only")}
        valid_conditions = (valid_conditions and observed_profile == description
                            and isinstance(observed_toolchain, dict)
                            and binding["profile"]["observer_blob"] is not None)
        # A different launcher/toolchain is raw diagnostic truth, never a
        # comparable PASS or a reason to relabel the originally bound proof.
        valid_conditions = valid_conditions and observed_toolchain == binding["toolchain"]
        reports = [dict(r) for r in canonical["reports"]]
        for report in reports:
            if report["outcome"] == "FAIL":
                if report.get("profile") != observed_profile or report.get("toolchain") != observed_toolchain:
                    valid_conditions = False
                report["profile"], report["toolchain"] = binding["profile"], binding["toolchain"]
        value = {**canonical, "reports": reports, "subject_sha": item.subject_sha,
                 "command": item.source.command, "exit_code": item.result.exit_code,
                 "profile": binding["profile"], "toolchain": binding["toolchain"],
                 "execution": result["execution"] if selected else {key: envelope[key] for key in (
                     "execution_contract", "subject_sha", "workers", "worker_collections", "controller_collection")}}
        if not selected and "exact_collection" in envelope:
            value["collection"] = envelope["exact_collection"]
            validate_exact_collection(value["collection"])
            if value["collection"]["identity"] != envelope.get("controller_collection"):
                valid_conditions = False
        if not valid_exit or not valid_conditions:
            value.update(complete=False, unstable=True)
        validate_observation(value)
        return value
    except (OSError, UnicodeError, KeyError, ValueError, TypeError, ProbeError, VerificationContractError):
        # Rejected proof must retain an observed condition conflict. Otherwise
        # broad fallback could erase collection-time toolchain/Git drift while
        # leaving the original expected binding looking comparable.
        diagnostics = ({"observed_toolchain": observed_toolchain}
                       if observed_toolchain is not None and observed_toolchain != binding["toolchain"] else {})
        if value is not None:
            # Preserve parsed canonical identities even when comparability fails.
            value.update(complete=False, unstable=True, **diagnostics)
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
                         "errors": ["selected wrapper observation is not comparable"], **diagnostics}
                verification_digest(value)
                return value
            except (OSError, UnicodeError, KeyError, TypeError, ValueError, VerificationContractError):
                pass
        # Candidate-only or legacy observations are diagnostic, never proof.
        return {"subject_sha": item.subject_sha, "command": item.source.command,
                "exit_code": item.result.exit_code, "profile": binding["profile"],
                "toolchain": binding["toolchain"], "complete": False, "unstable": True,
                "failure_count": 0, "reports": [], **diagnostics}


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
            if collect_only:
                profile.update(collected_for_workers=profile["workers"], collected_for_distribution=profile["distribution"],
                               workers=0, distribution="no")
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
                AIOS_VERIFY_SUBJECT_SHA=sha,
                AIOS_V2_PROFILE=json.dumps({k: profile[k] for k in (
                    "profile", "workers", "distribution", "max_worker_restart", "collect_only")}))
            command_env["PYTHONPATH"] = os.pathsep.join((str(checkout / "src"), str(checkout / "tests"), env.get("PYTHONPATH", "")))
            coverage = parse_pytest_coverage(command)
            observed = selected or affected or collect_only or authored_command is not None or (coverage is not None and not coverage.measurement)
            if observed and not selected:
                # Hidden environment selection/maxfail flags cannot narrow an
                # authored requirement. Repository configuration is still bound
                # to the exact tree and completion is checked by the observer.
                effective_pytest_profile(shlex.split(command)[3:] if command.startswith("python -m pytest") else shlex.split(command)[1:],
                                         environment=command_env, default_workers=profile.get("collected_for_workers", profile["workers"]))
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
                if previous is not None and _v2_observation_truth(previous) != _v2_observation_truth(observation):
                    observation.update(complete=False, unstable=True)
                for previous in observations:
                    if (all(previous.get(k) == observation.get(k) for k in ("subject_sha", "command", "profile", "toolchain"))
                            and _v2_observation_truth(previous) != _v2_observation_truth(observation)):
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
            # Retain rejected collection diagnostics for the covering command's
            # condition check; this does not make them eligible collection proof.
            collections.setdefault(command, {})["base" if base else "candidate"] = (item, observation)
            exact_collection_observation(observation, command=command, subject_sha=sha)
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
                if (pytest_execution_profile(candidate["profile"]) != pytest_execution_profile(candidate_collection["profile"])
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
                if (pytest_execution_profile(reproduced["profile"]) != pytest_execution_profile(base_collection["profile"])
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
                    if (pytest_execution_profile(candidate["profile"]) != pytest_execution_profile(collected["profile"])
                            or candidate["toolchain"] != collected.get("observed_toolchain", collected["toolchain"])
                            or ("collection" in candidate and "collection" in collected
                                and candidate["collection"] != collected["collection"])):
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
                                    pytest_execution_profile(record["base"]["profile"]) != pytest_execution_profile(collected_base["profile"])
                                    or record["base"]["toolchain"] != collected_base.get("observed_toolchain", collected_base["toolchain"])
                                    or ("collection" in record["base"] and "collection" in collected_base
                                        and record["base"]["collection"] != collected_base["collection"])):
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
            (git_executable(environment), "-C", str(repository), *args),
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
            (git_executable(environment), "-C", str(repository), *args),
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
    # Preserve both captures from this one execution. The protected raw file
    # retains the exact STDOUT/STDERR byte framing; this summary is no proof of
    # interleaving, completeness, or a test population.
    detail = "\n".join(stream.strip() for stream in (stdout, stderr) if stream.strip())
    if detail:
        return detail
    return "verification passed" if returncode == 0 else "verification failed"


@dataclass(frozen=True, slots=True)
class OriginalEvidenceObservation:
    """L2-01 read result. Authentication is separate from satisfactory proof."""

    status: str
    reason: str
    provenance: OriginalProofProvenance | None

    @property
    def disposition(self) -> str:
        return "BLOCK" if self.provenance is None else "SOURCE_OBSERVED_ONLY"


def _original_git(repository: Path, *arguments: str, limit: int = 65536) -> bytes:
    """Only used with fixed read commands below; bounded output, no lazy fetch."""
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
                       GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    command = (git_executable(environment), "--no-replace-objects", "-C", str(repository),
               "-c", "core.fsmonitor=false", "-c", "protocol.ext.allow=never", *arguments)
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          env=environment) as process:
        output = []
        def read():
            output.append(process.stdout.read(limit + 1))
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(10)
        if reader.is_alive() or not output or len(output[0]) > limit:
            process.kill()
            process.wait()
            reader.join(1)
            raise VerificationContractError("SOURCE_READ_UNAVAILABLE_OR_OVERSIZED")
        try:
            code = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise VerificationContractError("SOURCE_READ_TIMEOUT") from None
        if code:
            raise VerificationContractError("SOURCE_READ_UNAVAILABLE")
    return output[0]


def _original_object(repository: Path, sha: str, kind: str, limit: int) -> bytes:
    # Recompute the object identity; replace refs/grafts cannot supply lineage.
    size = _original_git(repository, "cat-file", "-s", sha).strip()
    if not size.isdigit() or not 0 < int(size) <= limit:
        raise VerificationContractError("SOURCE_OBJECT_SIZE")
    content = _original_git(repository, "cat-file", kind, sha, limit=limit)
    if (len(content) != int(size)
            or hashlib.sha1(f"{kind} {len(content)}\0".encode("ascii") + content).hexdigest() != sha):
        raise VerificationContractError("SOURCE_OBJECT_IDENTITY")
    return content


def _original_commit(repository: Path, sha: str) -> tuple[str, tuple[str, ...]]:
    content = _original_object(repository, sha, "commit", 65536)
    header, separator, _ = content.partition(b"\n\n")
    lines = header.split(b"\n")
    if not separator or not lines[0].startswith(b"tree "):
        raise VerificationContractError("SOURCE_COMMIT_SCHEMA")
    tree = lines[0][5:].decode("ascii")
    parents, index = [], 1
    while index < len(lines) and lines[index].startswith(b"parent "):
        parents.append(lines[index][7:].decode("ascii"))
        index += 1
    if (len(parents) > 16 or any(line.startswith((b"tree ", b"parent ")) for line in lines[index:])
            or any(re.fullmatch(r"[0-9a-f]{40}", value) is None for value in (tree, *parents))):
        raise VerificationContractError("SOURCE_COMMIT_SCHEMA")
    return tree, tuple(parents)


def _original_blob(repository: Path, tree: str, path: str, *, missing_ok: bool = False) -> tuple[str | None, bytes | None]:
    parts = path.split("/")  # All paths are fixed below, never caller-provided.
    for index, part in enumerate(parts):
        raw = _original_object(repository, tree, "tree", MAX_ORIGINAL_PROOF_BYTES)
        entries = {}
        while raw:
            header, separator, tail = raw.partition(b"\0")
            mode, space, name = header.partition(b" ")
            if not separator or not space or len(tail) < 20 or name in entries:
                raise VerificationContractError("SOURCE_TREE_SCHEMA")
            entries[name] = (mode, tail[:20].hex())
            raw = tail[20:]
        mode, sha = entries.get(part.encode("ascii"), (None, None))
        if mode is None and missing_ok:
            return None, None
        if mode != (b"100644" if index == len(parts) - 1 else b"40000"):
            raise VerificationContractError("SOURCE_BLOB_MISSING_OR_UNSAFE")
        tree = sha
    return sha, _original_object(repository, sha, "blob", MAX_ORIGINAL_PROOF_BYTES)


def _original_local(path: Path, limit: int) -> bytes:
    """No symlinks, junctions, traversal, hardlinks, devices or changing reads."""
    if not path.is_absolute() or ".." in path.parts:
        raise VerificationContractError("UNSAFE_LOCAL_SOURCE")
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise VerificationContractError("UNSAFE_LOCAL_SOURCE")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= limit:
        raise VerificationContractError("UNSAFE_OR_OVERSIZED_LOCAL_SOURCE")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        content = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    named_after = path.lstat()
    def identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    # Windows lstat/fstat can expose creation/change time respectively as
    # st_ctime_ns. Compare each clock only through the same API, while binding
    # every snapshot to the same file identity, size and modification time.
    if (len(content) != before.st_size or len(content) > limit
            or not identity(before) == identity(opened) == identity(after) == identity(named_after)
            or before.st_ctime_ns != named_after.st_ctime_ns
            or opened.st_ctime_ns != after.st_ctime_ns):
        raise VerificationContractError("LOCAL_SOURCE_CHANGED")
    return content


def _original_remote(repository: Path) -> str:
    # Same configured-branch upstream semantics as existing transport. No trust
    # root, raw locator, resolver callback or environment is accepted by the API.
    branch = _original_git(repository, "symbolic-ref", "--quiet", "--short", "HEAD").decode("utf-8").strip()
    remote = _original_git(repository, "config", "--get", f"branch.{branch}.remote").decode("utf-8").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", remote) is None:
        raise VerificationContractError("CANONICAL_UPSTREAM_UNAVAILABLE")
    url = _original_git(repository, "remote", "get-url", remote).decode("utf-8").strip()
    if not url or len(url) > 4096 or url.startswith("-") or "\n" in url:
        raise VerificationContractError("CANONICAL_UPSTREAM_UNAVAILABLE")
    return url


def _original_refs(repository: Path, url: str, run_id: str) -> tuple[tuple[str, str], ...]:
    names = tuple("refs/heads/aios/" + kind + "/" + run_id for kind in
                  ("review", "artifacts", "failure", "failure-artifacts", "repair"))
    output = _original_git(repository, "ls-remote", "--refs", url, *names, limit=8192)
    refs = {}
    for line in output.decode("ascii").splitlines():
        sha, tab, ref = line.partition("\t")
        if not tab or ref not in names or ref in refs or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
            raise VerificationContractError("COMPETING_OR_MALFORMED_SOURCE_REFS")
        refs[ref] = sha
    return tuple(sorted(refs.items()))


def _original_dimensions(task: dict, item: dict) -> tuple[OriginalProofDimension, ...]:
    record = item.get("verification", {})
    candidate = record.get("candidate", {})
    complete = candidate.get("complete") is True and candidate.get("unstable") is False
    # Incomplete V2 diagnostics may retain planned bindings after an observer
    # fault. They are not authenticated actual process conditions.
    profile = candidate.get("profile", {}) if complete else {}
    toolchain = candidate.get("toolchain", {}) if complete else candidate.get("observed_toolchain", {})
    toolchain_source = "EVIDENCE.verification.candidate." + ("toolchain" if complete else "observed_toolchain")
    facts = {}
    def observed(name, value, source):
        if value is not None:
            facts[name] = OriginalProofDimension(name, "OBSERVED", _original_json(value), source,
                                                  "RECORDED_SOURCE_CONTENT")
    observed("verification_policy", task.get("verification", {}).get("policy"), "TASK.verification.policy")
    if record:
        observed("execution_profile", profile or None, "EVIDENCE.verification.candidate.profile")
        observed("toolchain", toolchain or None, toolchain_source)
        interpreter = ("python_implementation", "python_version", "python_executable")
        if all(toolchain.get(key) for key in interpreter):
            observed("interpreter", {k: toolchain[k] for k in interpreter}, toolchain_source)
        observed("environment_digest", profile.get("environment_digest"), "EVIDENCE.verification.candidate.profile.environment_digest")
        # The digest records only Runtime's filtered environment, never its
        # absent original content or excluded temp/observer variables.
        observed("configuration", profile.get("repository_tool_blobs"), "EVIDENCE.verification.candidate.profile.repository_tool_blobs")
        observed("observer", profile.get("observer_blob"), "EVIDENCE.verification.candidate.profile.observer_blob")
        topology = ("workers", "distribution", "max_worker_restart", "collect_only")
        if all(key in profile for key in topology):
            observed("worker_topology", {k: profile[k] for k in topology}, "EVIDENCE.verification.candidate.profile")
        execution = candidate.get("execution", {})
        observed("worker_state", execution or None, "EVIDENCE.verification.candidate.execution")
        observed("collection", candidate.get("collection"), "EVIDENCE.verification.candidate.collection")
        observed("phase_outcomes", candidate.get("reports"), "EVIDENCE.verification.candidate.reports")
        if "reports" in candidate:
            observed("population", sorted({r["nodeid"] for r in candidate["reports"]}), "EVIDENCE.verification.candidate.reports.nodeid")
        observed("completeness", candidate.get("complete"), "EVIDENCE.verification.candidate.complete")
        observed("stability", candidate.get("unstable"), "EVIDENCE.verification.candidate.unstable")
        observed("prior_raw_digest", record.get("raw_digest"), "EVIDENCE.verification.raw_digest")
        diagnostics = {k: candidate[k] for k in ("errors", "observed_toolchain", "observation_sources") if k in candidate}
        observed("condition_diagnostics", diagnostics or None, "EVIDENCE.verification.candidate")
    observed("evidence_kind_schema", {"type": item["type"], "shape": "V2" if record else "V1",
                                     "keys": sorted(item)}, "canonical RESULT.evidence")
    return tuple(facts.get(name) or OriginalProofDimension(name, "UNKNOWN", None,
                 "SOURCE_RECORDS", "NOT_OBSERVED") for name in ORIGINAL_PROOF_DIMENSIONS)


def observe_original_evidence(repository: Path, *, expected: OriginalEvidenceRequest) -> OriginalEvidenceObservation:
    """Explicit Runtime-owned read-only L2-01 observation of one original capture.

    No ordinary V1/V2 execution calls this. Objects must already be local; this
    reader never fetches, verifies a target, writes state, or issues authority.
    Invalid/missing/conflicting source material yields NOT_ESTABLISHED/BLOCK.
    """
    try:
        if type(expected) is not OriginalEvidenceRequest:
            raise VerificationContractError("EXACT_TYPED_SOURCE_REQUIRED")
        expected.__post_init__()
        repository = Path(repository).absolute()
        # Do not resolve away a repository symlink supplied as a locator.
        if repository.resolve(strict=True) != repository:
            raise VerificationContractError("UNSAFE_REPOSITORY")
        url = _original_remote(repository)
        refs = _original_refs(repository, url, expected.run_id)
        wanted = tuple(sorted((("refs/heads/aios/review/" + expected.run_id, expected.candidate_sha),
                               ("refs/heads/aios/artifacts/" + expected.run_id, expected.artifacts_sha))))
        if refs != wanted:
            raise VerificationContractError("SOURCE_REFS_MISSING_STALE_OR_COMPETING")
        tree, _ = _original_commit(repository, expected.candidate_sha)
        if tree != expected.tree_sha:
            raise VerificationContractError("SOURCE_TREE_MISMATCH")
        artifacts_tree, artifact_parents = _original_commit(repository, expected.artifacts_sha)
        if artifact_parents:
            raise VerificationContractError("UNSUPPORTED_ARTIFACT_LINEAGE")
        task_blob, task_bytes = _original_blob(repository, tree, f".ai/tasks/{expected.task_id}.yaml")
        _, run_bytes = _original_blob(repository, artifacts_tree, ".ai/transport/run.json")
        _, result_bytes = _original_blob(repository, artifacts_tree, ".ai/transport/result.json")
        from .task import _TaskLoader, parse_task
        task = yaml.load(task_bytes.decode("utf-8"), Loader=_TaskLoader)
        if (type(task) is not dict or task.get("task_id") != expected.task_id
                or type(task.get("revision")) is not int or task["revision"] != expected.task_revision):
            raise VerificationContractError("SOURCE_TASK_MISMATCH")
        # Bound YAML nesting/types as well as bytes; no parser defaults become facts.
        _original_json(task)
        parse_task(task_bytes.decode("utf-8"))
        git_dir = Path(_original_git(repository, "rev-parse", "--absolute-git-dir").decode("utf-8").strip())
        state = git_dir / "aios"
        run_path = state / "runs" / (expected.run_id + ".json")
        result_path = state / "results" / (expected.run_id + ".json")
        failure_path = state / "failures" / (expected.run_id + ".json")
        if os.path.lexists(failure_path):
            raise VerificationContractError("COMPETING_LOCAL_FAILURE")
        local_run = _original_local(run_path, MAX_ORIGINAL_PROOF_BYTES)
        local_result = _original_local(result_path, MAX_ORIGINAL_PROOF_BYTES)
        run, result = _original_decode(local_run), _original_decode(local_result)
        canonical_run, canonical_result = _original_decode(run_bytes), _original_decode(result_bytes)
        from .review_transport import public_run, public_result
        if (canonical_run not in (run, _original_decode(public_run(local_run)))
                or canonical_result not in (result, _original_decode(public_result(local_result)))):
            raise VerificationContractError("LOCAL_CANONICAL_ARTIFACT_CONFLICT")
        from .review_transport import _bind_performance_terminal_identity
        bound = _bind_performance_terminal_identity(run_bytes, result_bytes,
            ref="refs/heads/aios/artifacts/" + expected.run_id, run_id=expected.run_id,
            task_id=expected.task_id, terminal_kind="RESULT")
        if bound[0] != expected.task_revision or bound[3] != expected.candidate_sha:
            raise VerificationContractError("SOURCE_CANONICAL_TERMINAL_MISMATCH")
        if (set(result) != {"result", "evidence"}
                or type(run.get("task", {}).get("revision")) is not int
                or run.get("run_id") != expected.run_id or run.get("task") != {"id": expected.task_id, "revision": expected.task_revision}
                or run.get("head_sha") not in (None, expected.candidate_sha)
                or result.get("result", {}).get("head_sha") != expected.candidate_sha):
            raise VerificationContractError("SOURCE_RUN_CANDIDATE_MISMATCH")
        base_sha = run["base_sha"]
        if type(base_sha) is not str or re.fullmatch(r"[0-9a-f]{40}", base_sha) is None:
            raise VerificationContractError("SOURCE_BASE_MISMATCH")
        # Walk raw immutable parent headers, bounded independently of local
        # grafts/shallow/replace metadata, to establish the admitted base.
        pending, seen = [expected.candidate_sha], set()
        while pending and base_sha not in seen:
            sha = pending.pop()
            if sha in seen:
                continue
            if len(seen) >= 4096:
                raise VerificationContractError("SOURCE_LINEAGE_BOUND")
            seen.add(sha)
            _, parents = _original_commit(repository, sha)
            pending.extend(parents)
        if base_sha not in seen:
            raise VerificationContractError("SOURCE_BASE_NOT_IN_LINEAGE")
        base_tree, _ = _original_commit(repository, base_sha)
        base_task_blob, _ = _original_blob(repository, base_tree, f".ai/tasks/{expected.task_id}.yaml")
        if base_task_blob != task_blob:
            raise VerificationContractError("SOURCE_TASK_CHANGED_AFTER_ADMISSION")
        population = result.get("evidence")
        if type(population) is not list or not 0 < len(population) <= 256:
            raise VerificationContractError("SOURCE_EVIDENCE_POPULATION")
        ids = [i["evidence_id"] for i in population]
        if len(ids) != len(set(ids)):
            raise VerificationContractError("COMPETING_EVIDENCE_IDENTITY")
        matches = [i for i in population if i["evidence_id"] == expected.evidence_id]
        if len(matches) != 1:
            raise VerificationContractError("SOURCE_EVIDENCE_UNKNOWN")
        item = matches[0]
        keys = {"evidence_id", "run_id", "subject_sha", "type", "source", "result", "raw"}
        if (set(item) not in (keys, keys | {"verification"}) or set(item["source"]) != {"command"}
                or set(item["result"]) != {"exit_code", "summary"} or set(item["raw"]) != {"path"}):
            raise VerificationContractError("SOURCE_EVIDENCE_SCHEMA")
        from .artifacts import validate_evidence
        validate_evidence(item)  # Preserve existing V1/V2 interpretation.
        if (item["run_id"] != expected.run_id or item["subject_sha"] != expected.subject_sha
                or item["type"] != "VERIFICATION" or item["source"]["command"] != expected.command
                or type(item["result"]["exit_code"]) is not int or item["result"]["exit_code"] != expected.exit_code):
            raise VerificationContractError("SOURCE_EVIDENCE_BINDING_MISMATCH")
        record = item.get("verification")
        directory = state / "verification" / expected.run_id
        v2 = task.get("verification", {}).get("policy") == MINIMUM_SUFFICIENT_V2
        raw_path = directory / ("candidate" if v2 else "") / (expected.evidence_id + ".raw")
        # Exact existing layout only. Public redacted paths are never opened.
        if item["raw"]["path"] != str(raw_path):
            raise VerificationContractError("SUBSTITUTED_OR_UNAVAILABLE_RAW_PATH")
        raw = _original_local(raw_path, MAX_ORIGINAL_RAW_BYTES)
        raw_digest = hashlib.sha256(raw).hexdigest()
        if not raw.startswith(b"STDOUT\n") or b"\nSTDERR\n" not in raw:
            raise VerificationContractError("SOURCE_RAW_FORMAT")
        outcome = "FAIL" if expected.exit_code == 1 else "ERROR" if expected.exit_code != 0 else "PASS"
        if record is not None:
            if (record.get("disposition") != "EXECUTED" or "reuse" in record or "early_probe" in record
                    or record.get("evidence_id") != expected.evidence_id or record.get("run_id") != expected.run_id
                    or record.get("raw_path") != str(raw_path) or record.get("raw_digest") != raw_digest
                    or record["binding"]["tree_sha"] != tree or record["binding"]["base_sha"] != base_sha
                    or record["binding"]["subject_sha"] != expected.candidate_sha
                    or task.get("verification", {}).get("policy") != MINIMUM_SUFFICIENT_V2):
                raise VerificationContractError("SOURCE_V2_OR_RAW_DIGEST_CONFLICT")
            candidate = record["candidate"]
            profile = candidate.get("profile", {})
            if candidate.get("complete") is True and candidate.get("unstable") is False:
                # Authenticate the recorded Git-based configuration/observer
                # identities against the original tree, not matching labels.
                tools = {"pyproject.toml", "src/aios_renew/verification_contract.py", "tests/conftest.py",
                         "src/aios_renew/parallel_verification.py", "tests/git_fixture_support.py",
                         "scripts/aios_parallel_full_suite.py", "src/aios_renew/verification_profile.py"}
                recorded_tools = profile.get("repository_tool_blobs", {})
                if type(recorded_tools) is not dict or set(recorded_tools) - tools:
                    raise VerificationContractError("SOURCE_CONFIGURATION_SCHEMA")
                for path, blob_sha in recorded_tools.items():
                    actual, _ = _original_blob(repository, tree, path, missing_ok=True)
                    if actual != blob_sha:
                        raise VerificationContractError("SOURCE_CONFIGURATION_BLOB_CONFLICT")
                for key, path in (("observer_blob", "tests/bp_v4_probe_plugin.py"),
                                  ("selected_policy_blob", ".ai/verification-profiles.yaml")):
                    if profile.get(key) is not None:
                        actual, _ = _original_blob(repository, tree, path, missing_ok=True)
                        if actual != profile[key]:
                            raise VerificationContractError("SOURCE_OBSERVER_POLICY_CONFLICT")
            if candidate.get("complete") is not True or candidate.get("unstable") is not False:
                if expected.exit_code == 0:
                    outcome = "UNKNOWN"
        dimensions = _original_dimensions(task, item)
        digest = lambda content: hashlib.sha256(content).hexdigest()
        provenance = OriginalProofProvenance(1, expected, base_sha, task_blob, digest(task_bytes),
            digest(run_bytes), digest(local_run), digest(result_bytes), digest(local_result),
            digest(_original_json(item).encode("ascii")), raw_digest, len(raw), outcome, refs, dimensions,
            digest(url.encode("utf-8")))
        format_original_proof(provenance)  # Enforce the total projection bound.
        if (_original_remote(repository) != url or _original_refs(repository, url, expected.run_id) != refs
                or os.path.lexists(failure_path) or _original_local(run_path, MAX_ORIGINAL_PROOF_BYTES) != local_run
                or _original_local(result_path, MAX_ORIGINAL_PROOF_BYTES) != local_result
                or _original_local(raw_path, MAX_ORIGINAL_RAW_BYTES) != raw):
            raise VerificationContractError("SOURCE_CHANGED_DURING_OBSERVATION")
        return OriginalEvidenceObservation("AUTHENTICATED", "EXACT_ORIGINAL_SOURCE", provenance)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RuntimeError,
            RecursionError, yaml.YAMLError) as exc:
        reason = str(exc) if isinstance(exc, VerificationContractError) else "SOURCE_UNAVAILABLE_OR_MALFORMED"
        return OriginalEvidenceObservation("NOT_ESTABLISHED", reason[:256], None)


# Explicit L2-02 reader. These helpers are not used by verification execution,
# admission, selection, review, publication or any continuation path.
class _ApplicabilityReader:
    """Already-local authenticated objects only, with aggregate read bounds."""

    def __init__(self, repository: Path):
        self.repository = repository
        self.objects = {}
        self.trees = {}
        self.commits = {}
        self.byte_count = 0

    def object(self, sha: str, kind: str) -> bytes:
        _original_hash(sha, 40)
        key = (sha, kind)
        if key not in self.objects:
            if len(self.objects) >= 8192:
                raise VerificationContractError("APPLICABILITY_OBJECT_COUNT_BOUND")
            remaining = 32 * 1024 * 1024 - self.byte_count
            if remaining <= 0:
                raise VerificationContractError("APPLICABILITY_OBJECT_BYTE_BOUND")
            content = _original_object(self.repository, sha, kind,
                min(remaining, 65536 if kind == "commit" else MAX_ORIGINAL_PROOF_BYTES))
            self.byte_count += len(content)
            if self.byte_count > 32 * 1024 * 1024:
                raise VerificationContractError("APPLICABILITY_OBJECT_BYTE_BOUND")
            self.objects[key] = content
        return self.objects[key]

    def commit(self, sha: str):
        if sha not in self.commits:
            # Same raw-header lineage convention as L2-01, without graph APIs.
            raw = self.object(sha, "commit")
            header, separator, _ = raw.partition(b"\n\n")
            lines = header.split(b"\n")
            if not separator or not lines[0].startswith(b"tree "):
                raise VerificationContractError("APPLICABILITY_COMMIT_SCHEMA")
            tree, parents, index = lines[0][5:].decode("ascii"), [], 1
            while index < len(lines) and lines[index].startswith(b"parent "):
                parents.append(lines[index][7:].decode("ascii"))
                index += 1
            if len(parents) > 16 or any(line.startswith((b"tree ", b"parent ")) for line in lines[index:]):
                raise VerificationContractError("APPLICABILITY_COMMIT_SCHEMA")
            for value in (tree, *parents):
                _original_hash(value, 40)
            self.commits[sha] = tree, tuple(parents)
        return self.commits[sha]

    def anchors(self, head: str, wanted: set[str]) -> dict:
        pending, found = [head], {}
        while pending and not wanted <= found.keys():
            sha = pending.pop()
            if sha in found:
                continue
            if len(found) >= 4096:
                raise VerificationContractError("APPLICABILITY_LINEAGE_BOUND")
            tree, parents = self.commit(sha)
            found[sha] = tree
            pending.extend(reversed(parents))
        if not wanted <= found.keys():
            raise VerificationContractError("APPLICABILITY_LINEAGE_NOT_ESTABLISHED")
        return found

    def tree(self, sha: str) -> dict:
        if sha not in self.trees:
            raw, entries = self.object(sha, "tree"), {}
            while raw:
                header, separator, tail = raw.partition(b"\0")
                mode, space, encoded = header.partition(b" ")
                if not separator or not space or len(tail) < 20:
                    raise VerificationContractError("APPLICABILITY_TREE_SCHEMA")
                name = encoded.decode("utf-8", errors="strict")
                if (not name or name in entries or "/" in name or name in {".", ".."}
                        or mode not in {b"40000", b"100644", b"100755", b"120000", b"160000"}):
                    raise VerificationContractError("APPLICABILITY_TREE_SCHEMA")
                entries[name] = (mode, tail[:20].hex())
                raw = tail[20:]
            self.trees[sha] = entries
        return self.trees[sha]

    def path(self, tree: str, path: str, *, missing_ok=False):
        parts = _app_path(path).split("/")
        for index, part in enumerate(parts):
            entry = self.tree(tree).get(part)
            if entry is None and missing_ok:
                return None
            if entry is None:
                raise VerificationContractError("APPLICABILITY_BLOB_UNAVAILABLE")
            mode, sha = entry
            if (index < len(parts) - 1 and mode != b"40000"
                    or index == len(parts) - 1 and mode not in {b"100644", b"100755"}):
                raise VerificationContractError("APPLICABILITY_UNSAFE_BLOB")
            tree = sha
        return sha

    def blob(self, tree: str, path: str) -> bytes:
        return self.object(self.path(tree, path), "blob")


def _app_plain(value):
    # Internal dataclass tuples are detached to JSON arrays before the shared
    # strict encoder enforces types, depth, nodes and total bytes.
    if type(value) in (list, tuple):
        return [_app_plain(item) for item in value]
    if type(value) is dict:
        return {key: _app_plain(item) for key, item in value.items()}
    return value


def _app_digest(value: object) -> str:
    return hashlib.sha256(_original_json(_app_plain(value)).encode("ascii")).hexdigest()


def _app_proof_digest(value: OriginalProofProvenance) -> str:
    return hashlib.sha256(format_original_proof(value)).hexdigest()


def _app_refs(repository: Path, url: str, patterns: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    from fnmatch import fnmatchcase
    output = _original_git(repository, "ls-remote", "--refs", url, *patterns, limit=32768)
    refs = {}
    for line in output.decode("ascii").splitlines():
        sha, tab, ref = line.partition("\t")
        _original_hash(sha, 40)
        if not tab or ref in refs or not any(fnmatchcase(ref, pattern) for pattern in patterns):
            raise VerificationContractError("APPLICABILITY_REF_CONFLICT")
        refs[ref] = sha
    return tuple(sorted(refs.items()))


def _app_transport(repository: Path) -> tuple[str, str, str]:
    branch = _original_git(repository, "symbolic-ref", "--quiet", "--short", "HEAD").decode("utf-8").strip()
    remote = _original_git(repository, "config", "--get", f"branch.{branch}.remote").decode("utf-8").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", remote) is None:
        raise VerificationContractError("APPLICABILITY_UPSTREAM_UNAVAILABLE")
    raw = _original_git(repository, "config", "--get", f"remote.{remote}.url").decode("utf-8").strip()
    if raw != _original_remote(repository):
        raise VerificationContractError("APPLICABILITY_UPSTREAM_REWRITE")
    return branch, remote, raw


def _app_target_refs(repository: Path, url: str, run_id: str):
    prefix = "refs/heads/aios/"
    patterns = (*(prefix + kind + "/" + run_id for kind in (
        "review", "artifacts", "failure", "failure-artifacts", "repair", "publication-recovery")),
        prefix + "repair-supersession/" + run_id + "/*", prefix + "remediation/" + run_id + "-*")
    return _app_refs(repository, url, patterns)


def _app_target(repository: Path, expected, reader: _ApplicabilityReader):
    from .publication import _run_from_data
    from .task import parse_task
    from .review_transport import _l1_yaml_text, public_run

    if _original_git(repository, "rev-parse", "--verify", "HEAD").decode("ascii").strip() != expected.candidate_sha:
        raise VerificationContractError("TARGET_IS_NOT_CURRENT_COMMITTED_HEAD")
    tree, _ = reader.commit(expected.candidate_sha)
    if tree != expected.tree_sha:
        raise VerificationContractError("TARGET_TREE_MISMATCH")
    lineage = reader.anchors(expected.candidate_sha, {expected.base_sha})
    path = f".ai/tasks/{expected.task_id}.yaml"
    task_blob = reader.path(tree, path)
    if reader.path(lineage[expected.base_sha], path) != task_blob:
        raise VerificationContractError("TARGET_TASK_CHANGED_AFTER_ADMISSION")
    task_bytes = reader.object(task_blob, "blob")
    task = parse_task(_l1_yaml_text(task_bytes))
    if (task.task_id, task.revision) != (expected.task_id, expected.task_revision):
        raise VerificationContractError("TARGET_TASK_MISMATCH")
    git_dir = Path(_original_git(repository, "rev-parse", "--absolute-git-dir").decode("utf-8").strip())
    run_path = git_dir / "aios/runs" / (expected.run_id + ".json")
    raw = _original_local(run_path, MAX_ORIGINAL_PROOF_BYTES)
    data = _original_decode(raw)
    if type(data) is not dict or set(data) - {"run_id", "task", "executor", "base_sha", "workspace", "head_sha", "status", "return_affinity"}:
        raise VerificationContractError("TARGET_ADMITTED_RUN_SCHEMA")
    run = _run_from_data(data, "L2-02 target RUN")
    if (hashlib.sha256(raw).hexdigest() != expected.run_sha256
            or run.run_id != expected.run_id or run.task.id != expected.task_id
            or run.task.revision != expected.task_revision or type(data["task"]["revision"]) is not int
            or run.base_sha != expected.base_sha or run.head_sha not in (None, expected.candidate_sha)
            or run.status != "ACTIVE" or run.return_affinity != task.return_affinity
            or Path(run.workspace).absolute() != repository):
        raise VerificationContractError("TARGET_ADMITTED_RUN_MISMATCH")
    url = _original_remote(repository)
    refs = _app_target_refs(repository, url, expected.run_id)
    wanted = tuple(sorted((("refs/heads/aios/review/" + expected.run_id, expected.candidate_sha),
                           ("refs/heads/aios/artifacts/" + expected.run_id, expected.artifacts_sha))))
    if refs != wanted:
        raise VerificationContractError("TARGET_CANONICAL_REFS_MISSING_STALE_OR_COMPETING")
    artifact_tree, parents = reader.commit(expected.artifacts_sha)
    canonical_run = reader.blob(artifact_tree, ".ai/transport/run.json")
    if parents or _original_decode(canonical_run) not in (data, _original_decode(public_run(raw))):
        raise VerificationContractError("TARGET_CANONICAL_ADMISSION_CONFLICT")
    failure_path = git_dir / "aios/failures" / (expected.run_id + ".json")
    if os.path.lexists(failure_path):
        raise VerificationContractError("TARGET_COMPETING_LOCAL_FAILURE")
    conditions = None
    if expected.condition_evidence is not None:
        observation = observe_original_evidence(repository, expected=expected.condition_evidence)
        if observation.status != "AUTHENTICATED" or observation.provenance is None:
            raise VerificationContractError("TARGET_CONDITION_SOURCE_NOT_ESTABLISHED")
        conditions = observation.provenance
    provenance = ApplicabilityTargetProvenance(expected, task_blob, hashlib.sha256(task_bytes).hexdigest(),
        hashlib.sha256(canonical_run).hexdigest(),
        _app_digest([[sha, *reader.commit(sha)] for sha in sorted(lineage)]), conditions)
    return provenance, task, (run_path, raw, failure_path, refs)


def _app_selection_record(reader: _ApplicabilityReader, sha: str):
    """Read the unchanged L1 carrier using local objects, never its fetch path."""
    from .review_transport import L1_SELECTION_PATH, _l1_selection_request
    tree, parents = reader.commit(sha)
    if len(parents) != 1:
        raise VerificationContractError("MAPPING_SELECTION_PARENT")
    raw = reader.blob(tree, L1_SELECTION_PATH)
    data = _app_exact(_original_decode(raw), ("format", "version", "request", "provenance", "policy"))
    if (len(raw) > 131072 or data["format"] != "AIOS_L1_HUMAN_REVIEW_SELECTION"
            or type(data["version"]) is not int or data["version"] != 1
            or raw != json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            or parents != (data["request"]["expected_state"]["expected_main_sha"],)):
        raise VerificationContractError("MAPPING_SELECTION_RECORD")
    request = _l1_selection_request(data["request"])
    blob = reader.path(tree, L1_SELECTION_PATH)

    def only_added(old_tree, new_tree, parts):
        old = dict(reader.tree(old_tree)) if old_tree else {}
        new = dict(reader.tree(new_tree))
        before, after = old.pop(parts[0], None), new.pop(parts[0], None)
        if old != new or after is None:
            raise VerificationContractError("MAPPING_SELECTION_UNRELATED_CHANGE")
        if len(parts) == 1:
            if before is not None or after != (b"100644", blob):
                raise VerificationContractError("MAPPING_SELECTION_NOT_IMMUTABLE_ADDITION")
        else:
            if after[0] != b"40000" or before and before[0] != b"40000":
                raise VerificationContractError("MAPPING_SELECTION_UNSAFE_TREE")
            only_added(before[1] if before else None, after[1], parts[1:])
    only_added(reader.commit(parents[0])[0], tree, L1_SELECTION_PATH.split("/"))
    return data, request


def _app_selection_authentication(reader: _ApplicabilityReader, record: dict, main: str, url: str):
    from .github_issue_ingress import _decode_policy, _authenticate_selection_issue
    from .review_transport import _l1_selection_request
    old = record["request"]["expected_state"]["expected_main_sha"]
    path = ".ai/brain-ingress-carriers.yaml"
    old_tree, _ = reader.commit(old)
    main_tree, _ = reader.commit(main)
    blob = reader.path(old_tree, path)
    raw = reader.object(blob, "blob")
    if (len(raw) > 65536 or reader.path(main_tree, path) != blob
            or record["policy"] != dict(main_sha=old, blob_sha=blob, sha256=hashlib.sha256(raw).hexdigest())
            or record["request"]["expected_state"]["expected_remote_url"] != url):
        raise VerificationContractError("MAPPING_SELECTION_POLICY_OR_TRANSPORT_CHANGED")
    policy = _decode_policy(raw)
    actual, body = _authenticate_selection_issue(policy, record["provenance"].get("issue_number"))
    if (actual != record["provenance"]
            or _l1_selection_request(_original_decode(body)) != record["request"]):
        raise VerificationContractError("MAPPING_SELECTION_NOT_INDEPENDENTLY_AUTHENTICATED")
    return f"github-issue:{actual['repository']}#{actual['issue_number']}@{actual['actor']}"


def _app_reviewed_population(repository: Path, request,
                             target: ApplicabilityTargetProvenance, task,
                             reader: _ApplicabilityReader, url: str):
    """No selector supplied by the caller can replace the existing L1 selection.

    L1's structural, scope, RESULT and REVIEW validators remain the owners of
    their contracts. Its fetching readers cannot be called by this read-only
    seam, so exact local objects are read through the bounded L2 object reader.
    """
    from dataclasses import asdict
    from .review_transport import _l1_selection_ref, _l1_request_scope
    from .proof_coverage_contract import (
        validate_proof_contract, proof_mapping_digest, validate_semantic_proof_declaration,
        validate_mapping_review_scope, TaskBinding,
    )
    from .review_transport import _l1_yaml_text
    from .task import parse_task
    from .publication import _run_from_data
    from .artifacts import validate_evidence, validate_result, validate_result_package
    from .review import parse_review, validate_review

    selected_ref = _l1_selection_ref(request.target.task_id, request.target.candidate_sha)
    initial_patterns = ("refs/heads/main", selected_ref, selected_ref + "/*")
    initial = dict(_app_refs(repository, url, initial_patterns))
    if set(initial) != {"refs/heads/main", selected_ref} or initial["refs/heads/main"] != request.expected_main_sha:
        raise VerificationContractError("EXACT_CURRENT_MAPPING_SELECTION_UNAVAILABLE")
    selection_sha = initial[selected_ref]
    record, selected = _app_selection_record(reader, selection_sha)
    human = _app_selection_authentication(reader, record, request.expected_main_sha, url)
    scope = _l1_request_scope(selected)
    payload, main = selected["payload"], request.expected_main_sha
    if (scope.target_task.task_id != request.target.task_id or scope.candidate_sha != request.target.candidate_sha
            or scope.target_task.revision != request.target.task_revision
            or asdict(scope.contract_pin) != _app_fields(request.contract)
            or asdict(scope.mapping_pin) != _app_fields(request.mapping)):
        raise VerificationContractError("UNREVIEWED_OR_REPLAYED_TARGET_MAPPING")
    run_pin = payload["run"]
    run_id, reviewed = run_pin["id"], run_pin["candidate_sha"]
    prefix = "refs/heads/aios/"
    patterns = (*initial_patterns, *(prefix + kind + "/" + run_id for kind in (
        "review", "artifacts", "review-decision", "failure", "failure-artifacts", "repair", "publication-recovery")),
        prefix + "repair-supersession/" + run_id + "/*", prefix + "remediation/" + run_id + "-*")
    refs = dict(_app_refs(repository, url, patterns))
    expected_names = {"refs/heads/main", selected_ref, *(prefix + kind + "/" + run_id for kind in ("review", "artifacts", "review-decision"))}
    if (set(refs) != expected_names or any(refs.get(k) != v for k, v in initial.items())
            or refs[prefix + "review/" + run_id] != reviewed):
        raise VerificationContractError("MAPPING_REVIEW_PUBLICATION_REFS_CONFLICT")
    artifacts, decision = refs[prefix + "artifacts/" + run_id], refs[prefix + "review-decision/" + run_id]
    locators = {**payload["sources"], "review_task": payload["review_task_source"]}
    trees = reader.anchors(main, {main, request.target.base_sha, selected["expected_state"]["expected_main_sha"], reviewed,
        *(locator["source_sha"] for locator in locators.values())})
    reviewed_trees = reader.anchors(reviewed, {run_pin["base_sha"], *(locator["source_sha"] for locator in locators.values())})
    contents, sources = {}, {}
    for role, locator in locators.items():
        if (trees[locator["source_sha"]] != locator["tree_sha"]
                or any(reader.path(tree, locator["path"]) != locator["blob_sha"]
                       for tree in (locator["tree_sha"], trees[main], trees[reviewed]))):
            raise VerificationContractError("SEMANTIC_SOURCE_STALE_OR_SUBSTITUTED")
        contents[role] = reader.object(locator["blob_sha"], "blob")
        sources[role] = {**locator, "sha256": hashlib.sha256(contents[role]).hexdigest()}
    if (locators["task"]["path"] != f".ai/tasks/{task.task_id}.yaml"
            or locators["task"]["blob_sha"] != target.task_blob_sha
            or locators["review_task"]["path"] != f".ai/tasks/{scope.review_task.task_id}.yaml"):
        raise VerificationContractError("SEMANTIC_TASK_SOURCE_SUBSTITUTION")
    actual_task = dict(task_id=task.task_id, revision=task.revision, envelope_digest=target.task_sha256,
                       acceptance_ids=sorted(ac.id for ac in task.acceptance))
    contract_data, mapping_data = _original_decode(contents["contract"]), _original_decode(contents["mapping"])
    contract = validate_proof_contract(contract_data, expected_task=actual_task)
    contract_pin = dict(id=contract.id, revision=contract.revision, digest=contract.digest)
    mapping_pin = dict(id=mapping_data.get("id"), revision=mapping_data.get("revision"), digest=proof_mapping_digest(mapping_data))
    if contract_pin != _app_fields(request.contract) or mapping_pin != _app_fields(request.mapping):
        raise VerificationContractError("SEMANTIC_PIN_MISMATCH")
    pins = {role: {key: locators[role][key] for key in ("source_sha", "blob_sha")} for role in ("task", "contract", "mapping")}
    intake = validate_semantic_proof_declaration(dict(schema="task-semantic-proof-intake-v1", task=actual_task,
        candidate_sha=request.target.candidate_sha, sources=pins, contract=contract_data, mapping=mapping_data),
        expected_task=actual_task, expected_candidate_sha=request.target.candidate_sha, expected_sources=pins,
        expected_contract=contract_pin, expected_mapping=mapping_pin)
    review_task = parse_task(_l1_yaml_text(contents["review_task"]))
    review_pin = TaskBinding(review_task.task_id, review_task.revision, sources["review_task"]["sha256"],
                            tuple(sorted(ac.id for ac in review_task.acceptance)))
    scope = validate_mapping_review_scope(scope, intake=intake, expected_review_task=review_pin)
    review_locator = locators["review_task"]
    base_tree = reviewed_trees[run_pin["base_sha"]]
    if reader.path(base_tree, review_locator["path"]) != review_locator["blob_sha"]:
        raise VerificationContractError("MAPPING_REVIEW_TASK_CHANGED_AFTER_ADMISSION")
    semantic_paths, changed_paths = set(), set()
    for role in ("contract", "mapping"):
        locator = locators[role]
        path = locator["path"]
        semantic_paths.add(path)
        changed = reader.path(base_tree, path, missing_ok=True) != locator["blob_sha"]
        if path not in (*review_task.scope.inspect, *review_task.scope.modify) or changed and path not in review_task.scope.modify:
            raise VerificationContractError("SEMANTIC_SOURCE_OUTSIDE_REVIEW_SCOPE")
        if changed:
            changed_paths.add(path)
        if role == "mapping" and (not changed or locator["source_sha"] != reviewed or locator["tree_sha"] != trees[reviewed]):
            raise VerificationContractError("MAPPING_HAS_NO_AUTHORIZED_REVIEWED_DELTA")
    artifact_tree, artifact_parents = reader.commit(artifacts)
    decision_tree, decision_parents = reader.commit(decision)
    def subtree(tree, parts):
        for part in parts:
            mode, tree = reader.tree(tree).get(part, (None, None))
            if mode != b"40000":
                raise VerificationContractError("MAPPING_REVIEW_DIRECTORY_UNAVAILABLE")
        return reader.tree(tree)
    artifact_names = set(subtree(artifact_tree, (".ai", "transport")))
    reviews = subtree(decision_tree, (".ai", "reviews"))
    if (artifact_parents or decision_parents != (reviewed,) or len(reviews) != 1
            or not {"run.json", "result.json"} <= artifact_names
            or not artifact_names <= {"run.json", "result.json", "observation.json", "execution-profile.json"}):
        raise VerificationContractError("MAPPING_REVIEW_LINEAGE_SCHEMA")
    review_name = next(iter(reviews))
    if re.fullmatch(r"REVIEW-[A-Za-z0-9_-]{1,121}\.yaml", review_name) is None:
        raise VerificationContractError("MAPPING_REVIEW_IDENTITY")
    for locator in locators.values():
        if reader.path(decision_tree, locator["path"]) != locator["blob_sha"]:
            raise VerificationContractError("MAPPING_REVIEW_DECISION_SOURCE_CHANGED")
    run_bytes, result_bytes = (reader.blob(artifact_tree, ".ai/transport/" + name) for name in ("run.json", "result.json"))
    run_data = _original_decode(run_bytes)
    if (type(run_data) is not dict or set(run_data) - {"run_id", "task", "executor", "base_sha", "workspace", "head_sha", "status", "return_affinity"}
            or run_data.get("task") != dict(id=review_task.task_id, revision=review_task.revision)
            or type(run_data["task"]["revision"]) is not int
            or hashlib.sha256(run_bytes).hexdigest() != run_pin["sha256"]):
        raise VerificationContractError("MAPPING_REVIEW_RUN_MISMATCH")
    run = _run_from_data(run_data, "L2-02 existing mapping review RUN")
    if (run.run_id != run_id or run.status != "ACTIVE" or run.base_sha != run_pin["base_sha"]
            or run.head_sha not in (None, reviewed) or run.return_affinity != review_task.return_affinity):
        raise VerificationContractError("MAPPING_REVIEW_RUN_BINDING")
    package = _app_exact(_original_decode(result_bytes), ("result", "evidence"))
    if type(package["evidence"]) is not list or len(package["evidence"]) > 256:
        raise VerificationContractError("MAPPING_REVIEW_EVIDENCE_POPULATION")
    result = validate_result(package["result"])
    evidence = tuple(validate_evidence(item) for item in package["evidence"])
    validate_result_package(task=review_task, run=run, result=result, evidence=evidence)
    if (result.head_sha != reviewed or result.unresolved
            or {ac for claim in result.claims for ac in claim.satisfies} != set(review_pin.acceptance_ids)
            or semantic_paths.intersection(result.changed_files) != changed_paths):
        raise VerificationContractError("MAPPING_REVIEW_RESULT_INCOMPLETE")
    review_bytes = reader.blob(decision_tree, ".ai/reviews/" + review_name)
    text = _l1_yaml_text(review_bytes)
    from .task import _TaskLoader
    review_data = yaml.load(text, Loader=_TaskLoader)
    if type(review_data) is not dict or set(review_data) - {"review_id", "reviewed_sha", "mode", "verdict", "acceptance", "findings", "prior_finding_id"}:
        raise VerificationContractError("MAPPING_REVIEW_SCHEMA")
    review = parse_review(text)
    validate_review(task=review_task, result=result, review=review)
    if (review.review_id != review_name[:-5] or review.reviewed_sha != reviewed or review.verdict != "PASS"
            or review.mode != "PRIMARY" or review.prior_finding_id is not None or review.findings
            or set(review.acceptance) != set(review_pin.acceptance_ids)
            or any(v != "PASS" for v in review.acceptance.values())):
        raise VerificationContractError("INDEPENDENT_MAPPING_REVIEW_PASS_ABSENT")
    # Shared authentication only: no source proof is necessary to authenticate
    # the complete selected population. L2 retains its exact per-source checks.
    binding = dict(upstream_sha256=hashlib.sha256(url.encode("utf-8")).hexdigest(), human_event_identity=human,
        review_id=review.review_id, review_run_id=run_id, run_sha256=run_pin["sha256"],
        result_sha256=hashlib.sha256(result_bytes).hexdigest(), review_sha256=hashlib.sha256(review_bytes).hexdigest(),
        scope_sha256=_app_digest(asdict(scope)), sources=sources)
    return intake.coverage, scope, binding, dict(main=main, selection_sha=selection_sha,
        reviewed=reviewed, artifacts=artifacts, decision=decision, trees=trees,
        decision_tree=decision_tree, base_tree=base_tree, review_task=review_task, result=result), (
        patterns, tuple(sorted(refs.items())), record)


def _app_reviewed_authority(repository: Path, request: ProofApplicabilityRequest,
                            target: ApplicabilityTargetProvenance, task, source: OriginalProofProvenance,
                            reader: _ApplicabilityReader, url: str):
    from dataclasses import asdict
    coverage, scope, binding, context, currentness = _app_reviewed_population(
        repository, request, target, task, reader, url)
    contract = coverage.contract
    main, selection_sha, reviewed, artifacts, decision, trees, decision_tree, base_tree, review_task, result = (
        context[key] for key in ("main", "selection_sha", "reviewed", "artifacts", "decision", "trees",
                                 "decision_tree", "base_tree", "review_task", "result"))
    obligation = next((o for o in contract.obligations if o.id == request.obligation_id), None)
    if (obligation is None or obligation.revision != request.obligation_revision or obligation.digest != request.obligation_digest
            or obligation.acceptance_ids != request.acceptance_ids or obligation.kind != "candidate"):
        raise VerificationContractError("EXACT_REVIEWED_CANDIDATE_OBLIGATION_UNAVAILABLE")
    # One supported provider: an exact content-addressed reviewed condition
    # document. Its hash is a locator; the independent review supplies authority.
    match = re.fullmatch(r"l2c:([0-9a-f]{64})", obligation.conditions.test_ref)
    if match is None:
        raise VerificationContractError("UNSUPPORTED_REVIEWED_CONDITION_PROVIDER")
    manifest_path = "docs/aios-proof-contracts/l2-conditions/" + match[1] + ".json"
    manifest_blob = reader.path(trees[reviewed], manifest_path)
    manifest_bytes = reader.object(manifest_blob, "blob")
    if (hashlib.sha256(manifest_bytes).hexdigest() != match[1]
            or manifest_path not in review_task.scope.modify or manifest_path not in result.changed_files
            or reader.path(base_tree, manifest_path, missing_ok=True) == manifest_blob
            or any(reader.path(tree, manifest_path) != manifest_blob for tree in (trees[main], decision_tree))):
        raise VerificationContractError("CONDITION_DOCUMENT_NOT_IN_EXACT_REVIEWED_DELTA")
    manifest = _app_exact(_original_decode(manifest_bytes), ("format", "version", "source_proof_sha256", "target",
        "obligation_id", "obligation_revision", "acceptance_ids", "claim", "conditions", "review_acceptance_id", "dimensions"))
    conditions = asdict(obligation.conditions)
    conditions.pop("test_ref")  # Breaks the content-address cycle, not a condition omission.
    correspondence = next((ac for ac in scope.acceptance if obligation.id in ac.obligation_ids), None)
    if (manifest["format"] != APPLICABILITY_CONDITIONS_FORMAT or type(manifest["version"]) is not int or manifest["version"] != 1
            or manifest_bytes != _original_json(manifest).encode("ascii")
            or manifest["source_proof_sha256"] != _app_proof_digest(source)
            or manifest["target"] != applicability_target_data(request.target)
            or manifest["obligation_id"] != obligation.id or type(manifest["obligation_revision"]) is not int
            or manifest["obligation_revision"] != obligation.revision
            or manifest["acceptance_ids"] != list(obligation.acceptance_ids)
            or manifest["claim"] != asdict(obligation.claim) or _original_json(manifest["conditions"]) != _original_json(_app_plain(conditions))
            or correspondence is None or manifest["review_acceptance_id"] != correspondence.review_acceptance_id):
        raise VerificationContractError("CONDITION_DOCUMENT_SOURCE_TARGET_OR_SEMANTICS_MISMATCH")
    binding = {**binding, "manifest_path": manifest_path,
               "review_acceptance_id": correspondence.review_acceptance_id}
    authority = ApplicabilityAuthority(main, selection_sha, reviewed, artifacts, decision,
        manifest_blob, match[1], _original_json(binding))
    return authority, manifest, currentness


def _app_unknown(name: str, reason: str, authority_sha256=None, source=None, target=None):
    absent = ApplicabilityCondition("UNKNOWN", "UNAVAILABLE", None, None)
    return ApplicabilityDimension(name, "UNKNOWN", source or absent, target or absent, reason, authority_sha256)


def _app_judgments(source: OriginalProofProvenance, target: ApplicabilityTargetProvenance,
                   authority: ApplicabilityAuthority, manifest: dict, reader: _ApplicabilityReader):
    items = manifest["dimensions"]
    if type(items) is not list or len(items) > len(APPLICABILITY_DIMENSIONS):
        raise VerificationContractError("REVIEWED_CONDITION_POPULATION")
    entries, indices = {}, []
    for item in items:
        _app_exact(item, ("name", "provider", "reason", "paths"))
        name = item["name"]
        if type(name) is not str or name not in APPLICABILITY_DIMENSIONS or name in entries:
            raise VerificationContractError("REVIEWED_CONDITION_DIMENSION")
        _original_text(item["reason"], 1800)
        if type(item["paths"]) is not list or len(item["paths"]) > MAX_APPLICABILITY_PATHS:
            raise VerificationContractError("REVIEWED_CONDITION_PATH_BOUND")
        for path in item["paths"]:
            _app_path(path)
        if item["paths"] != sorted(set(item["paths"])):
            raise VerificationContractError("REVIEWED_CONDITION_PATH_ORDER")
        if item["provider"] not in {"GIT_FOOTPRINT", "L2_ORIGINAL", "INAPPLICABLE", "UNAVAILABLE"}:
            raise VerificationContractError("UNSUPPORTED_REVIEWED_CONDITION_PROVIDER")
        if item["provider"] == "GIT_FOOTPRINT":
            if name not in APPLICABILITY_GIT_DIMENSIONS or not item["paths"]:
                raise VerificationContractError("UNSUPPORTED_GIT_CONDITION_DIMENSION")
        elif item["paths"]:
            raise VerificationContractError("UNSUPPORTED_CONDITION_PATHS")
        entries[name] = item
        indices.append(APPLICABILITY_DIMENSIONS.index(name))
    if indices != sorted(indices):
        raise VerificationContractError("REVIEWED_CONDITION_ORDER")
    auth_digest = _app_digest(_app_fields(authority))
    source_dims = {d.name: d for d in source.dimensions}
    target_dims = {} if target.conditions is None else {d.name: d for d in target.conditions.dimensions}

    def original(proof, facts, name):
        dimension = facts.get(name)
        if proof is None or dimension is None or dimension.status != "OBSERVED":
            return ApplicabilityCondition("UNKNOWN", "UNAVAILABLE", None, None)
        return ApplicabilityCondition("OBSERVED", "L2_ORIGINAL", _app_digest(dict(
            proof_sha256=_app_proof_digest(proof), dimension=name, source=dimension.source,
            value=_original_decode(dimension.value_json.encode("ascii")))), dimension.value_json)

    def footprint(proof, tree, paths):
        values = []
        try:
            for path in paths:
                blob = reader.path(tree, path, missing_ok=True)
                content = None if blob is None else reader.object(blob, "blob")
                cursor, mode = tree, None
                if blob is not None:
                    for part in path.split("/"):
                        mode, cursor = reader.tree(cursor)[part]
                values.append(dict(path=path, mode=None if mode is None else mode.decode("ascii"),
                    blob_sha=blob, sha256=None if content is None else hashlib.sha256(content).hexdigest()))
        except (OSError, ValueError, RuntimeError, UnicodeError):
            return ApplicabilityCondition("UNKNOWN", "UNAVAILABLE", None, None)
        encoded = _original_json(values)
        return ApplicabilityCondition("OBSERVED", "GIT_FOOTPRINT",
            _app_digest(dict(provenance=proof, tree_sha=tree, footprint=values)), encoded)

    result = []
    for name in APPLICABILITY_DIMENSIONS:
        item = entries.get(name)
        if item is None or item["provider"] == "UNAVAILABLE":
            result.append(_app_unknown(name, "DIMENSION_NOT_ESTABLISHED", auth_digest))
            continue
        if item["provider"] == "INAPPLICABLE":
            identity = _app_digest(dict(authority=auth_digest, dimension=name, declaration=item))
            fact = ApplicabilityCondition("INAPPLICABLE", "REVIEWED_SEMANTICS", identity, None)
            result.append(ApplicabilityDimension(name, "INAPPLICABLE", fact, fact, item["reason"], auth_digest))
            continue
        if item["provider"] == "L2_ORIGINAL":
            left, right = original(source, source_dims, name), original(target.conditions, target_dims, name)
        else:
            left = footprint(_app_proof_digest(source), source.identity.tree_sha, item["paths"])
            right = footprint(_app_digest(applicability_target_data(target.identity)), target.identity.tree_sha, item["paths"])
            # A reviewed path list cannot conjure an unobserved source dependency.
            if left.status == "OBSERVED" and any(value["blob_sha"] is None for value in _original_decode(left.value_json.encode("ascii"))):
                left = ApplicabilityCondition("UNKNOWN", "UNAVAILABLE", None, None)
        if left.status != "OBSERVED" or right.status != "OBSERVED":
            result.append(_app_unknown(name, "SOURCE_OR_TARGET_CONDITION_NOT_OBSERVED", auth_digest, left, right))
        else:
            same = left.value_json == right.value_json
            result.append(ApplicabilityDimension(name, "EQUIVALENT" if same else "CHANGED", left, right,
                ("AUTHENTICATED_EQUIVALENT: " if same else "PROVED_RELEVANT_DIVERGENCE: ") + item["reason"], auth_digest))
    # Equality alone is insufficient when actual conditions contradict the
    # exact reviewed requirement. This is a consistency guard, not selection
    # or an adequacy judgment inferred from semantic labels.
    declared = manifest["conditions"]
    checks = {d.name: d for d in result}
    def equivalent_value(name):
        item = checks[name]
        return _original_decode(item.source.value_json.encode("ascii")) if item.status == "EQUIVALENT" else None
    population = equivalent_value("population")
    topology = equivalent_value("worker_topology")
    schema = equivalent_value("evidence_kind_schema")
    if (population is not None and len(population) != declared["population_size"]
            or topology is not None and (topology["collect_only"] is not False
                or ("parallel" if topology["workers"] else "serial") != declared["worker_mode"]
                or (topology["workers"] or 1) != declared["workers"])
            or schema is not None and (schema["type"] != declared["evidence_kind"] or schema["shape"] != declared["evidence_schema"])):
        raise VerificationContractError("OBSERVED_CONDITIONS_CONTRADICT_REVIEWED_REQUIREMENTS")
    return tuple(result)


def observe_proof_applicability(repository: Path, *, expected: ProofApplicabilityRequest) -> ProofApplicabilityWitness:
    """Bounded Runtime read of one source -> exact current target, never reuse.

    Only typed locators are accepted. No parsed proof, raw path, provider,
    condition values, reviewed flag, resolver, runner or trust root is an input.
    Missing objects/authority/conditions block; nothing is fetched or executed.
    """
    if type(expected) is not ProofApplicabilityRequest:
        raise VerificationContractError("EXACT_TYPED_APPLICABILITY_REQUEST_REQUIRED")
    expected.__post_init__()
    source, target, authority, source_snapshot = None, None, None, None
    dimensions = tuple(_app_unknown(name, "IDENTITIES_NOT_ESTABLISHED") for name in APPLICABILITY_DIMENSIONS)
    try:
        repository = Path(repository).absolute()
        if repository.resolve(strict=True) != repository:
            raise VerificationContractError("UNSAFE_APPLICABILITY_REPOSITORY")
        transport = _app_transport(repository)
        url = transport[2]
        original = observe_original_evidence(repository, expected=expected.source)
        if original.status != "AUTHENTICATED" or original.provenance is None:
            raise VerificationContractError("ORIGINAL_SOURCE_NOT_ESTABLISHED:" + original.reason)
        source = original.provenance
        source_snapshot = applicability_source_snapshot(source)
        reader = _ApplicabilityReader(repository)
        target, task, local = _app_target(repository, expected.target, reader)
        authority, manifest, currentness = _app_reviewed_authority(repository, expected, target, task, source, reader, url)
        dimensions = _app_judgments(source, target, authority, manifest, reader)
        # Reauthenticate every mutable root after computing judgments. Object
        # hashes and memoized immutable bytes alone never establish currentness.
        patterns, refs, record = currentness
        _app_selection_authentication(reader, record, expected.expected_main_sha, url)
        if observe_original_evidence(repository, expected=expected.source).provenance != source:
            raise VerificationContractError("ORIGINAL_SOURCE_CHANGED_DURING_APPLICABILITY")
        if target.conditions is not None and observe_original_evidence(
                repository, expected=expected.target.condition_evidence).provenance != target.conditions:
            raise VerificationContractError("TARGET_CONDITIONS_CHANGED_DURING_APPLICABILITY")
        if (_app_transport(repository) != transport or _app_refs(repository, url, patterns) != refs
                or _original_local(local[0], MAX_ORIGINAL_PROOF_BYTES) != local[1] or os.path.lexists(local[2])
                or _app_target_refs(repository, url, expected.target.run_id) != local[3]
                or _original_git(repository, "rev-parse", "--verify", "HEAD").decode("ascii").strip() != expected.target.candidate_sha):
            raise VerificationContractError("SOURCE_TARGET_OR_REVIEW_CURRENTNESS_CHANGED")
        witness = ProofApplicabilityWitness(1, expected, "AUTHENTICATED", "EXACT_CURRENT_SOURCE_TARGET_AND_REVIEWED_CONDITIONS",
            source, target, authority, dimensions, source_snapshot)
        format_applicability(witness)
        return witness
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RuntimeError,
            RecursionError, yaml.YAMLError) as exc:
        # External errors can contain private URLs, paths or captures. Only
        # our fixed reason codes cross this inspection boundary.
        reason = str(exc) if type(exc) is VerificationContractError else "APPLICABILITY_AUTHORITY_OR_INPUT_UNAVAILABLE"
        reason = reason[:256]
        if all(d.reason == "IDENTITIES_NOT_ESTABLISHED" for d in dimensions):
            identity = None if authority is None else _app_digest(_app_fields(authority))
            dimensions = tuple(_app_unknown(name, reason, identity) for name in APPLICABILITY_DIMENSIONS)
        witness = ProofApplicabilityWitness(1, expected, "NOT_ESTABLISHED", reason, source, target, authority, dimensions, source_snapshot)
        try:
            format_applicability(witness)
        except VerificationContractError:
            witness = ProofApplicabilityWitness(1, expected, "NOT_ESTABLISHED", "APPLICABILITY_PROJECTION_BOUND",
                None, None, None, tuple(_app_unknown(name, "PROJECTION_UNAVAILABLE") for name in APPLICABILITY_DIMENSIONS), source_snapshot)
        return witness


def _reuse_scope(reader: _ApplicabilityReader, base_tree: str, candidate_tree: str, allowed: tuple[str, ...]):
    """Bounded immutable tree comparison; no graph, worktree or diff driver."""
    pending, entries = [(base_tree, candidate_tree, "", 0)], 0
    while pending:
        left, right, prefix, depth = pending.pop()
        if left == right:
            continue
        if depth > 32:
            raise VerificationContractError("REUSE_SCOPE_DEPTH_BOUND")
        before, after = ({} if tree is None else reader.tree(tree) for tree in (left, right))
        for name in sorted(before.keys() | after.keys()):
            entries += 1
            if entries > 8192:
                raise VerificationContractError("REUSE_SCOPE_ENTRY_BOUND")
            old, new = before.get(name), after.get(name)
            if old == new:
                continue
            path = _app_path(prefix + name)
            old_mode, old_sha = old or (None, None)
            new_mode, new_sha = new or (None, None)
            if old_mode == b"40000" or new_mode == b"40000":
                if old_mode not in (None, b"40000") or new_mode not in (None, b"40000"):
                    raise VerificationContractError("REUSE_SCOPE_FILE_DIRECTORY_SUBSTITUTION")
                pending.append((old_sha, new_sha, path + "/", depth + 1))
            elif (old_mode not in (None, b"100644", b"100755")
                    or new_mode not in (None, b"100644", b"100755") or path not in allowed):
                raise VerificationContractError("REUSE_CANDIDATE_OUTSIDE_IMMUTABLE_TASK_SCOPE")


def _reuse_binding(repository: Path, expected: ProofReuseRequest, app: ProofApplicabilityWitness) -> str:
    """Read the exact already-authenticated semantic blobs, never caller proof."""
    from dataclasses import asdict
    from .proof_coverage_contract import validate_proof_mapping
    from .review_transport import _l1_yaml_text
    from .task import parse_task

    if not _reuse_primary_bound(app):
        raise VerificationContractError("REUSE_PRIMARY_TASK_REVISION_BASE_OR_IMMUTABLE_SCOPE_MISMATCH")
    reader = _ApplicabilityReader(repository)
    sources = _original_decode(app.authority.binding_json.encode("ascii"))["sources"]
    contents = {}
    for name in ("task", "contract", "mapping"):
        content = reader.object(sources[name]["blob_sha"], "blob")
        if hashlib.sha256(content).hexdigest() != sources[name]["sha256"]:
            raise VerificationContractError("REUSE_REVIEWED_SEMANTIC_BYTES_CONFLICT")
        contents[name] = content
    task = parse_task(_l1_yaml_text(contents["task"]))
    coverage = validate_proof_mapping(_original_decode(contents["contract"]), _original_decode(contents["mapping"]),
        expected_task=dict(task_id=task.task_id, revision=task.revision, envelope_digest=app.target.task_sha256,
                           acceptance_ids=sorted(ac.id for ac in task.acceptance)),
        expected_mapping=_app_fields(expected.applicability.mapping))
    obligation = next(o for o in coverage.contract.obligations if o.id == expected.applicability.obligation_id)
    entry = next(e for e in coverage.mapping.entries if e.obligation_id == obligation.id)
    proof = next(p for p in coverage.mapping.proofs if p.id == entry.proof_id)
    binding = _original_json(_app_plain(dict(obligation=asdict(obligation), proof=asdict(proof), entry=asdict(entry))))
    _reuse_reviewed(binding, expected)
    base_tree, _ = reader.commit(app.source.base_sha)
    for candidate_tree in (app.source.identity.tree_sha, app.target.identity.tree_sha):
        _reuse_scope(reader, base_tree, candidate_tree, task.scope.modify)
    return binding


def observe_proof_reuse(repository: Path, *, expected: ProofReuseRequest) -> ProofReuseWitness:
    """Explicit L2-03 Runtime read; no execution, discharge or lifecycle writes.

    This API takes locators, never a parsed/cached VALID or eligibility witness.
    Correction authority cannot be inferred from a primary-shaped admission.
    Existing correction readers fetch objects and do not establish the complete
    native positive L2 target binding; do not call them or authorize by analogy.
    """
    if type(expected) is not ProofReuseRequest:
        raise VerificationContractError("EXACT_TYPED_REUSE_REQUEST_REQUIRED")
    expected.__post_init__()
    app, snapshot, binding = None, None, None
    authentication, reason = "NOT_ESTABLISHED", "REUSE_AUTHORITY_NOT_ESTABLISHED"
    try:
        repository = Path(repository).absolute()
        app = observe_proof_applicability(repository, expected=expected.applicability)
        snapshot = app.source_snapshot
        if expected.operation != "PRIMARY":
            raise VerificationContractError(expected.operation + "_NATIVE_CORRECTION_LINEAGE_NOT_ESTABLISHED")
        if app.authentication != "AUTHENTICATED":
            raise VerificationContractError("REUSE_APPLICABILITY_AUTHORITY_NOT_ESTABLISHED")
        if snapshot != expected.original:
            raise VerificationContractError("REUSE_ORIGINAL_PROOF_OR_RAW_PIN_CONFLICT")
        transport = _app_transport(repository)
        source_refs = _app_target_refs(repository, transport[2], expected.applicability.source.run_id)
        if source_refs != app.source.source_refs:
            raise VerificationContractError("REUSE_SOURCE_COMPETING_CORRECTION_OR_RECOVERY_REFS")
        binding = _reuse_binding(repository, expected, app)
        # L2-03 owns this fresh boundary check. Replaying even a previously
        # authentic VALID is insufficient after extra semantic/scope reads.
        refreshed = observe_proof_applicability(repository, expected=expected.applicability)
        if (refreshed != app or _app_transport(repository) != transport
                or _app_target_refs(repository, transport[2], expected.applicability.source.run_id) != source_refs):
            app, snapshot, binding = refreshed, refreshed.source_snapshot, None
            raise VerificationContractError("REUSE_SOURCE_TARGET_OR_REVIEW_CHANGED_AT_BOUNDARY")
        authentication, reason = "AUTHENTICATED", "EXACT_CURRENT_PRIMARY_SOURCE_OBLIGATION_AND_REVIEWED_PROOF"
    except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration, UnicodeError,
            RuntimeError, RecursionError, yaml.YAMLError) as exc:
        reason = str(exc)[:256] if type(exc) is VerificationContractError else "REUSE_AUTHORITY_OR_INPUT_UNAVAILABLE"
    witness = ProofReuseWitness(1, expected, authentication, reason, app, binding, snapshot)
    try:
        format_reuse(witness)
    except VerificationContractError:
        witness = ProofReuseWitness(1, expected, "NOT_ESTABLISHED", "REUSE_PROJECTION_BOUND", None, None, snapshot)
    return witness


def _scheduler_namespace(repository: Path, url: str, expected):
    """Observe the bounded TASK namespace, including successor/correction refs."""
    prefix = "RUN-" + expected.task_id[5:] + "-"
    patterns = tuple("refs/heads/aios/" + kind + "/" + prefix + "*" for kind in (
        "review", "artifacts", "review-decision", "failure", "failure-artifacts", "repair",
        "publication-recovery", "repair-supersession", "remediation"))
    refs = _app_refs(repository, url, patterns)
    number = int(expected.run_id[len(prefix):])
    for ref, _ in refs:
        suffix = ref.split("/", 4)[4]
        match = re.match(re.escape(prefix) + r"(\d{3,})(?:$|[-/])", suffix)
        if match is None:
            raise VerificationContractError("SCHEDULER_UNRECOGNIZED_TASK_RUN_REF")
        observed = prefix + match[1]
        if int(match[1]) > number or int(match[1]) == number and observed != expected.run_id:
            raise VerificationContractError("SCHEDULER_TARGET_SUPERSEDED_OR_RUN_ALIAS")
        if observed == expected.run_id and ref not in {
                "refs/heads/aios/review/" + observed, "refs/heads/aios/artifacts/" + observed}:
            raise VerificationContractError("SCHEDULER_TARGET_TERMINAL_OR_CORRECTION_AUTHORITY")
    return patterns, refs


def _scheduler_read_population(repository: Path, expected):
    """Fresh native admission, PRIMARY and complete selected review authority."""
    from .verification_contract import _scheduler_validate_coverage, _scheduler_coverage

    transport = _app_transport(repository)
    url = transport[2]
    reader = _ApplicabilityReader(repository)
    target, task, local = _app_target(repository, expected.target, reader)
    artifact_tree, _ = reader.commit(expected.target.artifacts_sha)
    node = artifact_tree
    for part in (".ai", "transport"):
        mode, node = reader.tree(node).get(part, (None, None))
        if mode != b"40000":
            raise VerificationContractError("SCHEDULER_PRIMARY_ADMISSION_TRANSPORT_UNAVAILABLE")
    if not set(reader.tree(node)) <= {"run.json", "result.json", "observation.json", "execution-profile.json"}:
        raise VerificationContractError("SCHEDULER_PRIMARY_CANNOT_INHERIT_CORRECTION_ADMISSION")
    namespace = _scheduler_namespace(repository, url, expected.target)
    base_tree, _ = reader.commit(expected.target.base_sha)
    _reuse_scope(reader, base_tree, expected.target.tree_sha, task.scope.modify)
    coverage, scope, binding, context, currentness = _app_reviewed_population(
        repository, expected, target, task, reader, url)
    _scheduler_validate_coverage(_scheduler_coverage(coverage), expected)
    authority = _original_json(dict(main_sha=context["main"], selection_sha=context["selection_sha"],
        review_candidate_sha=context["reviewed"], artifacts_sha=context["artifacts"], decision_sha=context["decision"],
        binding=binding, target=dict(task_blob_sha=target.task_blob_sha, task_sha256=target.task_sha256,
            canonical_run_sha256=target.canonical_run_sha256, lineage_sha256=target.lineage_sha256,
            condition_proof_sha256=None if target.conditions is None else _app_proof_digest(target.conditions))))
    # No cached carrier receipt or immutable object can establish mutable-root
    # currentness. Reread roots after the complete native validators have run.
    patterns, refs, record = currentness
    _app_selection_authentication(reader, record, expected.expected_main_sha, url)
    if target.conditions is not None and observe_original_evidence(
            repository, expected=expected.target.condition_evidence).provenance != target.conditions:
        raise VerificationContractError("SCHEDULER_TARGET_CONDITIONS_MOVED")
    if (_app_transport(repository) != transport or _app_refs(repository, url, patterns) != refs
            or _scheduler_namespace(repository, url, expected.target) != namespace
            or _original_local(local[0], MAX_ORIGINAL_PROOF_BYTES) != local[1] or os.path.lexists(local[2])
            or _app_target_refs(repository, url, expected.target.run_id) != local[3]
            or _original_git(repository, "rev-parse", "--verify", "HEAD").decode("ascii").strip() != expected.subject_sha):
        raise VerificationContractError("SCHEDULER_ADMISSION_REVIEW_OR_TARGET_MOVED")
    return coverage, authority, (transport, namespace, (patterns, refs), local, target.conditions)


def _scheduler_final_roots(repository: Path, expected, roots, witnesses):
    """Recheck all retained mutable roots after the core's last native reads."""
    transport, namespace, reviewed_refs, local, target_conditions = roots
    for locator, witness in zip(expected.sources, witnesses):
        if witness.authentication != "AUTHENTICATED":
            continue
        source = witness.applicability.source
        if (observe_original_evidence(repository, expected=locator.applicability.source).provenance != source
                or _app_target_refs(repository, transport[2], source.identity.run_id) != source.source_refs):
            raise VerificationContractError("SCHEDULER_ORIGINAL_SOURCE_MOVED_AT_FINAL_BOUNDARY")
    if target_conditions is not None and observe_original_evidence(
            repository, expected=expected.target.condition_evidence).provenance != target_conditions:
        raise VerificationContractError("SCHEDULER_TARGET_CONDITIONS_MOVED_AT_FINAL_BOUNDARY")
    if (_app_transport(repository) != transport
            or _app_refs(repository, transport[2], reviewed_refs[0]) != reviewed_refs[1]
            or _scheduler_namespace(repository, transport[2], expected.target) != namespace
            or _original_local(local[0], MAX_ORIGINAL_PROOF_BYTES) != local[1] or os.path.lexists(local[2])
            or _app_target_refs(repository, transport[2], expected.target.run_id) != local[3]
            or _original_git(repository, "rev-parse", "--verify", "HEAD").decode("ascii").strip() != expected.subject_sha):
        raise VerificationContractError("SCHEDULER_AUTHORITY_MOVED_AT_FINAL_BOUNDARY")


def _scheduler_source_assignments(coverage, expected):
    """Only exact complete reviewed entries can receive a source locator."""
    from dataclasses import asdict
    obligations = {o.id: o for o in coverage.contract.obligations}
    entries = {e.obligation_id: e for e in coverage.mapping.entries}
    proofs = {p.id: p for p in coverage.mapping.proofs}
    for source in expected.sources:
        app = source.applicability
        obligation = obligations.get(app.obligation_id)
        if (obligation is None or obligation.kind != "candidate" or not obligation.blocking
                or (obligation.revision, obligation.digest, obligation.acceptance_ids) != (
                    app.obligation_revision, app.obligation_digest, app.acceptance_ids)):
            raise VerificationContractError("SCHEDULER_UNREVIEWED_SOURCE_OBLIGATION")
        proof = proofs[entries[obligation.id].proof_id]
        if (source.proof_id, source.proof_sha256) != (proof.id, _app_digest(asdict(proof))):
            raise VerificationContractError("SCHEDULER_UNREVIEWED_SOURCE_PROOF")


def plan_candidate_proofs(repository: Path, *, expected: ProofSchedulerRequest, _runtime=None) -> ProofSchedulerPlan:
    """Single explicit Runtime-owned L3-01 core. Reads only; never proof authority.

    No plan/witness/provider/caller mapping is an input. A first candidate needs
    the complete native reviewed population, but needs no original evidence.
    Both future consumers receive projections from this very same plan.
    """
    from .verification_contract import (
        ProofSchedulerRequest, ProofSchedulerPlan, SchedulerSourceDecision,
        format_scheduler_request, format_scheduler_plan,
    )
    if type(expected) is not ProofSchedulerRequest:
        raise VerificationContractError("EXACT_TYPED_SCHEDULER_REQUEST_REQUIRED")
    format_scheduler_request(expected)  # Enforce aggregate bounds before I/O.
    coverage, authority, decisions = None, None, ()
    try:
        g3 = expected.version == 2
        if expected.operation != "PRIMARY" and not g3:
            reason = (expected.operation + "_NATIVE_CORRECTION_LINEAGE_NOT_ESTABLISHED"
                      if expected.operation in {"REPAIR", "REMEDIATION"} else "SCHEDULER_UNSUPPORTED_OPERATION")
            raise VerificationContractError(reason)
        repository = Path(repository).absolute()
        if repository.resolve(strict=True) != repository:
            raise VerificationContractError("UNSAFE_SCHEDULER_REPOSITORY")
        def population():
            if not g3:
                return _scheduler_read_population(repository, expected)
            from .runtime import RuntimeCompletion
            if (type(_runtime) is not RuntimeCompletion or _runtime.repo.absolute() != repository
                    or _runtime._ka01_budget is None or not _runtime._ka01_budget.g3):
                raise VerificationContractError("G3_ORIGINAL_LIVE_RUNTIME_REQUIRED")
            _, selected, binding = _runtime._ka01_budget.g3_population(expected)
            return selected, binding, None
        coverage, authority, roots = population()
        _scheduler_source_assignments(coverage, expected)
        witnesses = tuple(observe_proof_reuse(repository, expected=s) for s in expected.sources)
        decisions = tuple(SchedulerSourceDecision(s.applicability.obligation_id, w.outcome, w.reason,
            w.source_snapshot, hashlib.sha256(format_reuse(w)).hexdigest()) for s, w in zip(expected.sources, witnesses))
        # L3 owns its own final reads after source decisions, even though L2
        # already authenticated each one. No externally parsed witness is used.
        current_coverage, current_authority, current_roots = population()
        if (current_coverage, current_authority, current_roots) != (coverage, authority, roots):
            raise VerificationContractError("SCHEDULER_SOURCE_TARGET_OR_REVIEW_CHANGED_AT_BOUNDARY")
        refreshed = tuple(observe_proof_reuse(repository, expected=s) for s in expected.sources)
        if refreshed != witnesses:
            raise VerificationContractError("SCHEDULER_SOURCE_TARGET_OR_REVIEW_CHANGED_AT_BOUNDARY")
        if g3:
            if population() != (coverage, authority, roots):
                raise VerificationContractError("G3_AUTHORITY_MOVED_AT_FINAL_BOUNDARY")
        else:
            _scheduler_final_roots(repository, expected, current_roots, refreshed)
        plan = ProofSchedulerPlan(expected.version, expected, "AUTHENTICATED", "EXACT_CURRENT_PRIMARY_AND_COMPLETE_REVIEWED_POPULATION",
                                  coverage, authority, decisions)
        format_scheduler_plan(plan)
        return plan
    except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration, UnicodeError,
            RuntimeError, RecursionError, yaml.YAMLError) as exc:
        reason = str(exc)[:256] if type(exc) is VerificationContractError else "SCHEDULER_NATIVE_AUTHORITY_OR_INPUT_UNAVAILABLE"
        plan = ProofSchedulerPlan(expected.version, expected, "NOT_ESTABLISHED", reason, coverage, authority, decisions)
        try:
            format_scheduler_plan(plan)
        except (VerificationContractError, ValueError, TypeError):
            plan = ProofSchedulerPlan(expected.version, expected, "NOT_ESTABLISHED", "SCHEDULER_PROJECTION_BOUND", None, None, ())
        return plan

# L3-02: native proof capture/observation helpers, subordinate to RuntimeCompletion.
# No policy registration, baseline derivation, retry or caller-provided evidence.
def _checkpoint_pytest_coverage(command: str):
    """Recognize checkpoint selectors plus one explicit bounded worker suffix.

    Keep the historical V1/V2 coverage grammar unchanged. Only selection is
    normalized here; the original authored argv, effective profile and actual
    native worker population remain independently bound and checked.
    """
    coverage = parse_pytest_coverage(command)
    if coverage is None:
        try:
            tokens = shlex.split(command)
        except ValueError:
            return None
        if len(tokens) < 7:
            return None
        pin, workers, dist, distribution, restart, retries = tokens[-6:]
        if (pin != "-n" or dist != "--dist" or restart != "--max-worker-restart" or retries != "0"
                or re.fullmatch(r"0|[1-9][0-9]{0,2}", workers) is None or int(workers) > 256
                or distribution != ("no" if workers == "0" else "load")):
            return None
        coverage = parse_pytest_coverage(shlex.join(tokens[:-6]))
    if coverage is None or coverage.measurement or coverage.selected_parallel:
        return None
    return coverage


def _checkpoint_reviewed_execution(repository: Path, request, *, expected_plan=None, _runtime=None):
    from .verification_contract import (
        CHECKPOINT_EXECUTION_FORMAT, format_checkpoint_request, format_scheduler_plan,
        scheduler_plan_data,
    )
    format_checkpoint_request(request)
    plan = plan_candidate_proofs(repository, expected=request.scheduler, _runtime=_runtime)
    if plan.authentication != "AUTHENTICATED":
        raise VerificationContractError("CHECKPOINT_" + plan.reason)
    if expected_plan is not None and format_scheduler_plan(plan) != expected_plan:
        raise VerificationContractError("CHECKPOINT_PLAN_OR_NATIVE_ROOTS_MOVED")
    if _original_git(repository, "status", "--porcelain"):
        raise VerificationContractError("CHECKPOINT_REQUIRES_CLEAN_COMMITTED_CANDIDATE")
    reader = _ApplicabilityReader(repository)
    if request.version == 2:
        task = _runtime.task
    else:
        target, task, _ = _app_target(repository, request.scheduler.target, reader)
    if request.scheduler.sources:
        # L2 locators remain inspection facts. They cannot select a fresh
        # execution or impersonate a current-RUN native execution receipt.
        raise VerificationContractError("CHECKPOINT_REQUIRES_REVIEWED_CURRENT_NATIVE_EXECUTION_BINDING")
    authority = _original_decode(plan.authority_json.encode("ascii"))
    reviewed_tree, _ = reader.commit(authority["review_candidate_sha"])
    main_tree, _ = reader.commit(authority["main_sha"])
    decision_tree, _ = reader.commit(authority["decision_sha"])
    artifacts_tree, _ = reader.commit(authority["artifacts_sha"])
    package = _original_decode(reader.blob(artifacts_tree, ".ai/transport/result.json"))
    review_source = authority["binding"]["sources"]["review_task"]
    from .task import parse_task
    review_task = parse_task(reader.object(review_source["blob_sha"], "blob").decode("utf-8"))
    from .publication import _run_from_data
    review_run = _run_from_data(_original_decode(reader.blob(artifacts_tree, ".ai/transport/run.json")), "L3-02 review")
    base_tree, _ = reader.commit(review_run.base_sha)
    commands = (_runtime._ka01_budget.policy.verification_commands if request.version == 2
                else task.verification.required)
    if not commands or len(commands) > 256 or len(set(commands)) != len(commands):
        raise VerificationContractError("CHECKPOINT_EXACT_AUTHORED_COMMAND_POPULATION_REQUIRED")
    manifests, indices = {}, []
    for unit in scheduler_plan_data(plan)["units"]:
        proof = unit["proof"]
        if proof["kind"] != "candidate":
            raise VerificationContractError("BR_1_THROUGH_BR_6_NATIVE_ATTESTATION_NOT_ESTABLISHED")
        match = re.fullmatch(r"l3e:([0-9a-f]{64})", proof["conditions"]["test_ref"])
        if match is None:
            raise VerificationContractError("CHECKPOINT_REVIEWED_EXECUTION_CONDITIONS_NOT_ESTABLISHED")
        path = "docs/aios-proof-contracts/l3-executed/" + match[1] + ".json"
        blob = reader.path(reviewed_tree, path)
        raw = reader.object(blob, "blob")
        if (hashlib.sha256(raw).hexdigest() != match[1] or path not in review_task.scope.modify
                or path not in package["result"]["changed_files"]
                or reader.path(base_tree, path, missing_ok=True) == blob
                or any(reader.path(tree, path) != blob for tree in (main_tree, decision_tree))):
            raise VerificationContractError("CHECKPOINT_CONDITIONS_OUTSIDE_FULL_REVIEWED_DELTA")
        manifest = _app_exact(_original_decode(raw), ("format", "version", "proof_id", "claims", "conditions",
            "command_index", "profile", "toolchain", "collection"))
        conditions = {k: v for k, v in proof["conditions"].items() if k != "test_ref"}
        reviewed_conditions = dict(manifest["conditions"])
        if request.version == 2:
            # Original independently reviewed execution template, applied only
            # to the current scoped candidate by the admitted G3 implementation.
            reviewed_conditions["candidate_sha"] = request.scheduler.subject_sha
        index = manifest["command_index"]
        if (raw != _original_json(manifest).encode("ascii") or manifest["format"] != CHECKPOINT_EXECUTION_FORMAT
                or type(manifest["version"]) is not int or manifest["version"] != 1
                or manifest["proof_id"] != proof["id"] or manifest["claims"] != proof["claims"]
                or reviewed_conditions != conditions or type(index) is not int or not 0 <= index < len(commands)
                or conditions["evidence_kind"] != "VERIFICATION" or conditions["evidence_schema"] != "V2"):
            raise VerificationContractError("CHECKPOINT_REVIEWED_FULL_PROOF_BINDING_CONFLICT")
        coverage = _checkpoint_pytest_coverage(commands[index])
        if commands[index] == SELECTED_FULL_SUITE_COMMAND or coverage is None or coverage.measurement:
            raise VerificationContractError("CHECKPOINT_NATIVE_COMPLETE_OBSERVER_REQUIRED")
        nodes = validate_exact_collection(manifest["collection"])
        profile, toolchain = _v2_profile(repository, request.scheduler.subject_sha, commands[index], os.environ), _v2_toolchain()
        if (manifest["profile"] != profile or manifest["toolchain"] != toolchain
                or profile["collect_only"] is not False or len(nodes) != conditions["population_size"]
                or not nodes or conditions["worker_mode"] != ("parallel" if profile["workers"] else "serial")
                or conditions["workers"] != (profile["workers"] or 1)):
            raise VerificationContractError("CHECKPOINT_NATIVE_CONDITIONS_STALE_OR_UNKNOWN")
        manifests[proof["id"]] = (unit, manifest, match[1])
        indices.append(index)
    if sorted(indices) != list(range(len(commands))):
        raise VerificationContractError("CHECKPOINT_DISTINCT_FULL_PROOF_AND_COMMAND_COVERAGE_REQUIRED")
    return plan, task, manifests


def _execute_checkpoint_verification(repository: Path, *, request, task, manifests,
                                     raw_directory: Path, start_order: int, runner, timeout_seconds: float,
                                     capture_bytes_remaining: int, commands=None, modification_scope=None):
    """The existing verifier executes only the exact already-authored commands.

    This is observation instrumentation, not V2 derivation: no early probes,
    base work, fallback, semantic selection or second equivalent execution.
    Only RuntimeCompletion calls this after its durable effect reservation.
    """
    env = dict(os.environ)
    evidence = []
    commands = task.verification.required if commands is None else commands
    modification_scope = task.scope.modify if modification_scope is None else modification_scope
    with materialize_verification_subject(repository, run_id=request.scheduler.target.run_id,
            subject_sha=request.scheduler.subject_sha) as subject:
        for index, command in enumerate(commands):
            _, manifest, _ = next(v for v in manifests.values() if v[1]["command_index"] == index)
            profile, toolchain = manifest["profile"], manifest["toolchain"]
            order = start_order + index + 1
            output = raw_directory / f"observation-{order:03d}.json"
            command_env = dict(env)
            command_env.update(PYTHONDONTWRITEBYTECODE="1", AIOS_BP_V4_PLUGIN_OUTPUT=str(output),
                AIOS_VERIFY_SUBJECT_SHA=request.scheduler.subject_sha,
                AIOS_V2_PROFILE=json.dumps({k: profile[k] for k in (
                    "profile", "workers", "distribution", "max_worker_restart", "collect_only")}))
            command_env["PYTHONPATH"] = os.pathsep.join((str(subject / "src"), str(subject / "tests"), env.get("PYTHONPATH", "")))
            if command != SELECTED_FULL_SUITE_COMMAND:
                coverage = _checkpoint_pytest_coverage(command)
                if coverage is None or coverage.measurement:
                    raise VerificationContractError("CHECKPOINT_NATIVE_COMPLETE_OBSERVER_REQUIRED")
                command_env.pop("PYTEST_ADDOPTS", None)
                command_env["PYTEST_PLUGINS"] = ",".join(filter(None, (env.get("PYTEST_PLUGINS", ""), "bp_v4_probe_plugin")))
                command_env["AIOS_V2_EXACT_COLLECTION"] = "1"
            started = time.monotonic()
            def bounded_runner(argv, **kwargs):
                return runner(argv, timeout=max(0.001, timeout_seconds), **kwargs)
            item = execute_verification((command,), run_id=request.scheduler.target.run_id,
                subject_sha=request.scheduler.subject_sha, repository=subject, raw_directory=raw_directory,
                runner=bounded_runner, environment=command_env, stop_on_failure=False, start_order=order)[0]
            timeout_seconds -= time.monotonic() - started
            if timeout_seconds <= 0:
                raise RuntimeVerificationError("checkpoint time accounting exhausted", evidence=(*evidence, item))
            binding = dict(subject_sha=item.subject_sha, base_sha=request.scheduler.target.base_sha,
                tree_sha=request.scheduler.target.tree_sha, command=command, profile=profile, toolchain=toolchain,
                envelope_digest=verification_digest(dict(authored=list(commands), operation=request.scheduler.operation,
                                                        modification_scope=sorted(modification_scope))),
                changed_files_digest=verification_digest(sorted(filter(None, _git(repository, "diff", "--name-only",
                    "--no-renames", "-z", request.scheduler.target.base_sha, item.subject_sha).split("\0")))),
                failure_set_digest=verification_digest([]))
            observation = _v2_observation(item, output, binding=binding, selected=command == SELECTED_FULL_SUITE_COMMAND)
            raw = _original_local(Path(item.raw_path), MAX_ORIGINAL_RAW_BYTES)
            record = dict(policy=MINIMUM_SUFFICIENT_V2, disposition="EXECUTED", binding=binding, candidate=observation,
                candidate_digest=verification_digest(observation), evidence_id=item.evidence_id, run_id=item.run_id,
                raw_path=item.raw_path, raw_digest=hashlib.sha256(raw).hexdigest())
            evidence.append(replace(item, verification=record))
            native_size = len(raw) if command == SELECTED_FULL_SUITE_COMMAND else len(_original_local(output, MAX_CANONICAL_BYTES))
            capture_bytes_remaining -= len(raw) + native_size
            if capture_bytes_remaining < 0:
                raise RuntimeVerificationError("checkpoint capture accounting exhausted", evidence=evidence)
        if (_git(subject, "rev-parse", "HEAD") != request.scheduler.subject_sha or _git(subject, "status", "--porcelain")):
            raise RuntimeVerificationError("checkpoint verification changed committed subject", evidence=evidence)
    return tuple(evidence)


def _checkpoint_native_facts(repository: Path, *, request, manifests, evidence, raw_directory: Path, _runtime=None):
    """Reobserve immutable raw and actual full native proof; never accept flags."""
    from .runtime import evidence_data
    from .artifacts import validate_evidence
    by_command = {item.source.command: item for item in evidence}
    if len(by_command) != len(evidence) or len(evidence) != len(manifests):
        raise VerificationContractError("CHECKPOINT_MISSING_OR_DUPLICATE_NATIVE_PROOF")
    facts = []
    for proof_id, (unit, manifest, manifest_digest) in sorted(manifests.items()):
        # The reviewed command index was independently bound against TASK above.
        matches = [item for item in evidence if item.verification is not None
            and item.verification.get("binding", {}).get("profile") == manifest["profile"]
            and item.source.command == _checkpoint_task_command(repository, request, manifest["command_index"], _runtime=_runtime)]
        if len(matches) != 1:
            raise VerificationContractError("CHECKPOINT_REVIEWED_EXECUTION_MISSING")
        item = matches[0]
        validate_evidence(evidence_data(item))
        record = item.verification
        observation = validate_observation(record.get("candidate"))
        expected_path = raw_directory / (item.evidence_id + ".raw")
        raw = _original_local(expected_path, MAX_ORIGINAL_RAW_BYTES)
        digest = hashlib.sha256(raw).hexdigest()
        order = int(item.evidence_id.rsplit("-V", 1)[1])
        output = raw_directory / f"observation-{order:03d}.json"
        native_raw = raw if item.source.command == SELECTED_FULL_SUITE_COMMAND else _original_local(output, MAX_CANONICAL_BYTES)
        if _v2_observation(item, output, binding=record["binding"],
                selected=item.source.command == SELECTED_FULL_SUITE_COMMAND) != observation:
            raise VerificationContractError("CHECKPOINT_ORIGINAL_NATIVE_OBSERVATION_CHANGED")
        if ((item.run_id, item.subject_sha, item.type) != (request.scheduler.target.run_id,
                request.scheduler.subject_sha, "VERIFICATION") or item.raw_path != str(expected_path)
                or not raw.startswith(b"STDOUT\n") or b"\nSTDERR\n" not in raw
                or record.get("disposition") != "EXECUTED" or "reuse" in record or "early_probe" in record
                or (record.get("evidence_id"), record.get("run_id"), record.get("raw_path"), record.get("raw_digest")) != (
                    item.evidence_id, item.run_id, item.raw_path, digest)
                or record["binding"]["base_sha"] != request.scheduler.target.base_sha
                or record["binding"]["tree_sha"] != request.scheduler.target.tree_sha
                or observation["profile"] != manifest["profile"] or observation["toolchain"] != manifest["toolchain"]
                or observation.get("collection") != manifest["collection"] or observation.get("errors") != []):
            raise VerificationContractError("CHECKPOINT_NATIVE_EXECUTION_OR_RAW_CHANGED")
        nodes = validate_exact_collection(manifest["collection"])
        phases = {node: {} for node in nodes}
        for report in observation["reports"]:
            if report["nodeid"] not in phases or report["phase"] not in {"setup", "call", "teardown"}:
                raise VerificationContractError("CHECKPOINT_PHASE_POPULATION_CONFLICT")
            phases[report["nodeid"]][report["phase"]] = report["outcome"]
        if any(set(row) != {"setup", "call", "teardown"} for row in phases.values()):
            raise VerificationContractError("CHECKPOINT_INCOMPLETE_ACTUAL_PHASES")
        all_pass = all(value == "PASS" for row in phases.values() for value in row.values())
        outcome = "FAIL" if item.result.exit_code == 1 else "ERROR" if item.result.exit_code != 0 or not all_pass else "PASS"
        facts.append(dict(proof_id=proof_id, unit_id=unit["unit_id"], evidence_id=item.evidence_id,
            evidence_sha256=hashlib.sha256(_original_json(evidence_data(item)).encode("ascii")).hexdigest(),
            raw_sha256=digest, raw_size=len(raw), manifest_sha256=manifest_digest,
            native_sha256=hashlib.sha256(native_raw).hexdigest(), native_size=len(native_raw),
            complete=True, stable=True, outcome=outcome, satisfactory=outcome == "PASS",
            source_run_id=item.run_id, subject_sha=item.subject_sha))
    return facts


def _checkpoint_task_command(repository, request, index, *, _runtime=None):
    if request.version == 2:
        if _runtime is None or _runtime._ka01_budget is None or not _runtime._ka01_budget.g3:
            raise VerificationContractError("G3_ORIGINAL_RUNTIME_COMMAND_BINDING_REQUIRED")
        return _runtime._ka01_budget.policy.verification_commands[index]
    from .task import parse_task
    _, content = _original_blob(repository, request.scheduler.target.tree_sha, f".ai/tasks/{request.scheduler.target.task_id}.yaml")
    return parse_task(content.decode("utf-8")).verification.required[index]
