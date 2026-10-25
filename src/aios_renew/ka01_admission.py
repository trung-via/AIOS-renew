"""KA-01 v1.3 admission and finite resources, subordinate to RuntimeCompletion.

Prospective version 4 enrolls newly admitted coding operations from canonical
source by default, after their independent original family admission. Version
3 separately consents to one live native multi-turn PRIMARY session.
Version 2 remains bounded one-shot completion; version 1 remains controlled
local preactivation. No interface reconstructs a session or lease from disk.
Human authentication uses the existing ingress policy and Issue authenticator.
Files are negative crash guards, never bearer authority.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from fractions import Fraction
from functools import wraps
import hashlib
import json
import math
import os
from pathlib import Path
from threading import get_ident
from types import MappingProxyType
import time

from .verification_contract import VerificationContractError


IMPLEMENTATION = "KA01-L3-05-v1"
PRODUCTION_IMPLEMENTATION = "KA01-v1.3-bounded-primary-v1"
PRODUCTION_MODE = "PRODUCTION_BOUNDED_PRIMARY"
INTERACTIVE_IMPLEMENTATION = "KA01-v1.3-interactive-primary-v1"
INTERACTIVE_MODE = "PRODUCTION_INTERACTIVE_PRIMARY"
DEFAULT_IMPLEMENTATION = "KA01-v1.3-default-three-flow-v1"
DEFAULT_MODE = "PRODUCTION_DEFAULT_THREE_FLOW"
KERNEL_UPGRADE_IMPLEMENTATION = "KA01-v1.3-original-source-kernel-upgrade-v1"
VERSION = "1.3"
PROFILE = "KA01-v1.3-default-resource-profile"
NORMATIVE_PATH = "docs/AIOS-KERNEL-KA01-PROSPECTIVE-AMENDMENT.md"
HISTORICAL_NORMATIVE_SHA256 = "1381a4ef25a1b6877b8e6326767e8545eff17cdc49dfd6062090e630c8a5e84e"
# Historical requests keep their original normative pin. A fresh explicit
# exception may select current normative bytes without changing its old mode.
PRE_UPGRADE_NORMATIVE_SHA256 = "52b7f7af908315608ab504e46fdcdcc6e671f157aab2d6a51104c7f83902ed27"
DEFAULT_NORMATIVE_SHA256 = "72831e82cd58abb388d35e4efb3f28412e4b293f36604c7f18ed1b1164b9808d"
NORMATIVE_SHA256 = DEFAULT_NORMATIVE_SHA256
IMPLEMENTATION_PATHS = (
    "src/aios_renew/ka01_admission.py", "src/aios_renew/operator.py",
    "src/aios_renew/runtime.py", "src/aios_renew/dispatcher.py",
    "src/aios_renew/executor.py", "src/aios_renew/verification.py",
    "src/aios_renew/verification_contract.py", "src/aios_renew/intra_run_proof_contract.py",
    "src/aios_renew/codex_adapter.py",
)
INTERACTIVE_IMPLEMENTATION_PATHS = (*IMPLEMENTATION_PATHS,
    "src/aios_renew/codex_live_transport.py", "src/aios_renew/run_observation.py")
DEFAULT_IMPLEMENTATION_PATHS = (*INTERACTIVE_IMPLEMENTATION_PATHS,
    "src/aios_renew/antigravity_adapter.py", "src/aios_renew/antigravity_minimax_adapter.py")
# These are the existing observer and its transitive first-party dependencies,
# not a new verification owner. Their original bytes supply upgrade observation.
KERNEL_PROOF_PATHS = (
    "pyproject.toml", "tests/conftest.py", "tests/bp_v4_probe_plugin.py",
    "tests/git_fixture_support.py", "src/aios_renew/verification_contract.py",
    "src/aios_renew/proof_coverage_contract.py", "src/aios_renew/parallel_verification.py",
)
KERNEL_IMPLEMENTATION_PATHS = tuple(dict.fromkeys((*DEFAULT_IMPLEMENTATION_PATHS,
    *(path for path in KERNEL_PROOF_PATHS if path.startswith("src/aios_renew/")))))
OPERATIONS = frozenset({"PRIMARY", "REPAIR", "REMEDIATION"})
DEFAULTS = dict(max_corrections=2, max_checkpoints=3, max_run_wall_minutes=240,
                max_runtime_verification_minutes=180, max_tokens=None)


def _deny(code):
    raise VerificationContractError("KA01_V13_" + code)


def _stop_reason(owner, exc):
    """Retain only bounded first-party STOP codes, never provider diagnostics."""
    import re
    reason = str(exc)
    owner._ka01_stop_reason = reason if re.fullmatch(r"(?:KA01_|CODEX_LIVE_STOP_)[A-Z0-9_]{1,150}", reason) else "KA01_DEFAULT_ORIGINAL_BOUNDARY_STOP"


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _digest(value):
    return hashlib.sha256(value if type(value) is bytes else _bytes(value)).hexdigest()


def _exact(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        _deny("INVALID_FIELDS")
    return value


def _decode(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                _deny("DUPLICATE_FIELD")
            result[key] = value
        return result
    if type(raw) is not bytes or not 0 < len(raw) <= 65536:
        _deny("SOURCE_BOUND")
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: _deny("NONFINITE_VALUE"))
        if _bytes(value) != raw:
            _deny("NONCANONICAL_SOURCE")
        return value
    except (ValueError, TypeError, RecursionError, UnicodeError):
        _deny("INVALID_SOURCE")


def _hash(value):
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        _deny("INVALID_DIGEST")


def _minutes_ns(value):
    if type(value) is int and value > 0:
        return value * 60_000_000_000
    if type(value) is not float or not math.isfinite(value) or value <= 0:
        _deny("INVALID_TIME_LIMIT")
    converted = Fraction(str(value)) * 60_000_000_000
    if converted.denominator != 1:
        _deny("LOSSY_TIME_LIMIT")
    return converted.numerator


def resolve_limits(overrides):
    """Merge already authenticated applicable field sets, never invalid fallback."""
    merged, named = dict(DEFAULTS), set()
    for values in overrides:
        if type(values) is not dict or not values or set(values) - DEFAULTS.keys() or named & values.keys():
            _deny("INVALID_OR_OVERLAPPING_OVERRIDE")
        named.update(values)
        merged.update(values)
    for name, minimum in (("max_corrections", 0), ("max_checkpoints", 1)):
        if type(merged[name]) is not int or merged[name] < minimum:
            _deny("INVALID_COUNT_LIMIT")
    wall = _minutes_ns(merged["max_run_wall_minutes"])
    verification = _minutes_ns(merged["max_runtime_verification_minutes"])
    if verification > wall:
        _deny("VERIFICATION_EXCEEDS_WALL")
    cap = merged["max_tokens"]
    if cap is not None and (type(cap) is not int or cap <= 0):
        _deny("INVALID_TOKEN_LIMIT")
    return merged


@dataclass(frozen=True)
class AdmissionRequest:
    version: int
    selection_issue: int
    selection_sha256: str
    consent_issue: int
    consent_sha256: str

    def __post_init__(self):
        if type(self.version) is not int or self.version not in (1, 2, 3):
            _deny("UNSUPPORTED_INTERFACE_VERSION")
        for number in (self.selection_issue, self.consent_issue):
            if type(number) is not int or not 0 < number < 10**12:
                _deny("INVALID_ISSUE")
        for digest in (self.selection_sha256, self.consent_sha256):
            _hash(digest)


@dataclass(frozen=True)
class _DefaultAdmission:
    """Runtime selection after original family admission; no bearer fields."""
    version: int = 4

    def __post_init__(self):
        if type(self.version) is not int or self.version != 4:
            _deny("UNSUPPORTED_INTERFACE_VERSION")


def production_gate(request, *, operation="PRIMARY"):
    """Select only the supported interface; this is never authentication."""
    if request is None:
        return
    # Preserve rejection of old bearer-like flags and historical v1 requests.
    if type(request) is not AdmissionRequest or request.version not in (2, 3):
        _deny("NOT_ACTIVATED")
    request.__post_init__()
    if operation != "PRIMARY":
        _deny("PRODUCTION_OPERATION_UNSUPPORTED")


def _production_source(owner, reader, tree, *, interactive=False, default=False, kernel=False):
    """Bind the running first-party consumers to the exact selected main source."""
    import sys
    from .verification import _original_local, MAX_ORIGINAL_PROOF_BYTES
    blobs = {}
    paths = KERNEL_IMPLEMENTATION_PATHS if kernel else DEFAULT_IMPLEMENTATION_PATHS if default else INTERACTIVE_IMPLEMENTATION_PATHS if interactive else IMPLEMENTATION_PATHS
    for path in paths:
        module = sys.modules.get("aios_renew." + Path(path).stem)
        if module is None or not getattr(module, "__file__", None):
            _deny("RUNNING_IMPLEMENTATION_SOURCE_UNAVAILABLE")
        content = reader.blob(tree, path)
        loaded = _original_local(Path(module.__file__).absolute(), MAX_ORIGINAL_PROOF_BYTES)
        # Ordinary Git checkouts may convert text line endings on Windows.
        if loaded.replace(b"\r\n", b"\n") != content.replace(b"\r\n", b"\n"):
            _deny("RUNNING_IMPLEMENTATION_SOURCE_MISMATCH")
        blobs[path] = _digest(content)
    return blobs


def _policy_data(policy):
    data = asdict(policy)
    if data["lineage_path"] is not None:
        data["lineage_path"] = str(data["lineage_path"])
    return data


def _protected_kernel_scope(scope):
    """A directory grant cannot evade the protected-consumer boundary."""
    return sorted(path for path in {*DEFAULT_IMPLEMENTATION_PATHS, *KERNEL_PROOF_PATHS}
        if any(path == grant or path.startswith(grant.rstrip("/") + "/") for grant in scope))


@dataclass(frozen=True)
class _KernelUpgradeSource:
    """Original process's pre-RUN facts; never decoded from a carrier or file."""
    control: Path
    subject: Path
    content: bytes


def _kernel_upgrade_source(control, subject, *, task, base_sha, operation, scope, commands, executor):
    """Authenticate executability before reserving a protected coding RUN.

    Original family admission is still required by the caller and rechecked by
    _ka01_operation_source. A TASK, flag, hash or transport receipt cannot select
    an old Runtime out of its unconditional guard. The installed original must
    already implement this published source and remain outside the mutation tree.
    """
    import sys
    from .task import parse_task
    from .verification import _ApplicabilityReader, _original_remote, _app_refs, _v2_profile, _v2_toolchain
    if not _protected_kernel_scope(scope):
        return None
    if operation not in OPERATIONS or executor != "codex" or not set(scope).issubset(task.scope.modify):
        _deny("KERNEL_UPGRADE_OPERATION_OR_SCOPE_UNAUTHORIZED")
    control, subject = control.absolute(), subject.absolute()
    try:
        url = _original_remote(control)
        refs = dict(_app_refs(control, url, ("refs/heads/main",)))
        if set(refs) != {"refs/heads/main"} or operation == "PRIMARY" and refs["refs/heads/main"] != base_sha:
            _deny("KERNEL_UPGRADE_ORIGINAL_MAIN_MOVED")
        main = refs["refs/heads/main"]
        reader = _ApplicabilityReader(control)
        tree, _ = reader.commit(main)
        if _digest(reader.blob(tree, NORMATIVE_PATH)) != DEFAULT_NORMATIVE_SHA256:
            _deny("KERNEL_UPGRADE_BOOTSTRAP_REQUIRED")
        # Read the exact original TASK, including for historical corrections.
        subject_reader = _ApplicabilityReader(subject)
        base_tree, _ = subject_reader.commit(base_sha)
        task_raw = subject_reader.blob(base_tree, ".ai/tasks/" + task.task_id + ".yaml")
        if parse_task(task_raw.decode("utf-8")) != task:
            _deny("KERNEL_UPGRADE_ORIGINAL_TASK_MISMATCH")
        for path in KERNEL_IMPLEMENTATION_PATHS:
            module = sys.modules.get("aios_renew." + Path(path).stem)
            if module is None or not getattr(module, "__file__", None):
                _deny("KERNEL_UPGRADE_ORIGINAL_RUNTIME_UNAVAILABLE")
            if Path(module.__file__).resolve().is_relative_to(subject.resolve()):
                _deny("KERNEL_UPGRADE_RUNTIME_SOURCE_IS_MUTABLE")
            spec = getattr(module, "__spec__", None)
            if spec is None or not spec.origin or Path(spec.origin).absolute() != Path(module.__file__).absolute():
                _deny("KERNEL_UPGRADE_RUNNING_MODULE_SUBSTITUTED")
        try:
            implementation = _production_source(None, reader, tree, kernel=True)
        except VerificationContractError:
            _deny("KERNEL_UPGRADE_RUNNING_IMPLEMENTATION_SOURCE_MISMATCH")
        try:
            proof = {path: _digest(reader.blob(tree, path)) for path in KERNEL_PROOF_PATHS}
        except VerificationContractError:
            _deny("KERNEL_UPGRADE_ORIGINAL_OBSERVER_UNAVAILABLE")
        if not commands or len(commands) > 256 or len(set(commands)) != len(commands):
            _deny("EXACT_NATIVE_COMMAND_POPULATION_REQUIRED")
        from .verification import _checkpoint_pytest_coverage
        for command in commands:
            coverage = _checkpoint_pytest_coverage(command)
            if coverage is None or coverage.is_full_suite or coverage.measurement:
                _deny("KERNEL_UPGRADE_COMPLETE_OBSERVER_UNSUPPORTED")
            try:
                profile = _v2_profile(control, main, command, os.environ)
            except (RuntimeError, ValueError, TypeError):
                _deny("KERNEL_UPGRADE_ORIGINAL_OBSERVER_UNAVAILABLE")
            if profile["collect_only"] or not profile["observer_blob"] or not all(profile["repository_tool_blobs"].values()):
                _deny("KERNEL_UPGRADE_ORIGINAL_OBSERVER_UNAVAILABLE")
        try:
            toolchain = _v2_toolchain()
        except RuntimeError:
            _deny("KERNEL_UPGRADE_TOOLCHAIN_UNAVAILABLE")
        if dict(_app_refs(control, url, ("refs/heads/main",))) != refs:
            _deny("KERNEL_UPGRADE_SOURCE_MOVED_DURING_PREFLIGHT")
        return _KernelUpgradeSource(control, subject, _bytes(dict(
            implementation=KERNEL_UPGRADE_IMPLEMENTATION, source_main_sha=main, source_tree_sha=tree,
            remote_url=url, implementation_source=implementation, proof_source=proof,
            task_sha256=_digest(asdict(task)), task_blob_sha256=_digest(task_raw), base_sha=base_sha,
            operation=operation, executor=executor, modification_scope=sorted(scope),
            commands=list(commands), toolchain=toolchain)))
    except VerificationContractError as exc:
        if str(exc).startswith("KA01_V13_KERNEL_UPGRADE_"):
            raise
        _deny("KERNEL_UPGRADE_BOOTSTRAP_SOURCE_UNAVAILABLE")
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        _deny("KERNEL_UPGRADE_BOOTSTRAP_SOURCE_UNAVAILABLE")


def _kernel_upgrade_authority(owner, operation, policy, profile):
    scope = policy.mutation_scope if operation == "REPAIR" else policy.result_scope
    original = owner._kernel_upgrade_source
    if not _protected_kernel_scope(scope):
        if original is not None:
            _deny("KERNEL_UPGRADE_CONFLICTING_ADMISSION")
        return None
    if type(original) is not _KernelUpgradeSource:
        _deny("KERNEL_UPGRADE_BOOTSTRAP_REQUIRED")
    current = _kernel_upgrade_source(owner.transport_repo, owner.repo, task=owner.task,
        base_sha=owner.run.base_sha, operation=operation, scope=scope,
        commands=_default_commands(owner.task, policy), executor=owner.run.executor)
    if original != current:
        _deny("KERNEL_UPGRADE_ORIGINAL_SOURCE_CHANGED")
    facts = json.loads(original.content)
    return dict(facts, run_id=owner.run.run_id, execution_profile_sha256=_digest(profile.as_dict()))


def _default_commands(task, policy):
    from .verification_contract import MINIMUM_SUFFICIENT_V2, parse_pytest_coverage
    commands = policy.verification_commands
    if task.verification.policy == MINIMUM_SUFFICIENT_V2 and policy.remediation_execution is not None:
        commands = tuple(dict.fromkeys((*commands, *(
            c for c in task.verification.required
            if (coverage := parse_pytest_coverage(c)) is not None and coverage.is_full_suite))))
    return commands


def _default_authority(owner, operation, profile, policy):
    """Authenticate the current canonical default, subordinate to admission.

    Only canonical main supplies this policy. Carrier fields, flags, old
    Issues and orphan selectors do not participate in default enrollment.
    Main's publication remains the existing Publisher's responsibility.
    """
    from .execution_profile import ResolvedExecutionProfile, parse_execution_profile
    from .operator import _ka01_operation_source
    from .runtime import CompletionPolicy
    from .verification import _ApplicabilityReader, _original_local, _original_remote, _app_refs

    if operation not in OPERATIONS or type(policy) is not CompletionPolicy or policy.kind != operation:
        _deny("OPERATION_NOT_ADMITTED")
    # This reauthenticates original TASK/failure/Review finding authority. A
    # valid global policy can neither create nor widen any of those grants.
    source = _ka01_operation_source(owner, operation, policy, production=True)
    owner._ka01_default_operation_source = source
    control = owner.transport_repo
    url = _original_remote(control)
    refs = dict(_app_refs(control, url, ("refs/heads/main",)))
    if set(refs) != {"refs/heads/main"}:
        _deny("DEFAULT_CANONICAL_MAIN_REQUIRED")
    main = refs["refs/heads/main"]
    owner._ka01_default_source = main
    if operation == "PRIMARY" and main != owner.run.base_sha:
        _deny("ORIGINAL_MAIN_MOVED")
    reader = _ApplicabilityReader(control)
    tree, _ = reader.commit(main)
    norm = reader.blob(tree, NORMATIVE_PATH)
    if _digest(norm) != DEFAULT_NORMATIVE_SHA256:
        _deny("DEFAULT_GLOBAL_SOURCE_MISSING_OR_CHANGED")
    implementation_source = _production_source(owner, reader, tree, interactive=True, default=True)
    if owner.run.executor != "codex":
        _deny("DEFAULT_NATIVE_CAPABILITY_UNSUPPORTED_" + owner.run.executor.upper().replace("-", "_"))
    if type(profile) is not ResolvedExecutionProfile:
        _deny("EXACT_SELECTED_PROFILE_REQUIRED")
    profile.__post_init__()
    if (profile.run_id, profile.executor) != (owner.run.run_id, owner.run.executor):
        _deny("PROFILE_RUN_MISMATCH")
    profile_raw = _original_local(owner.run_path.parent.parent / "execution-profiles" /
        (owner.run.run_id + ".json"), 65536)
    if parse_execution_profile(profile_raw.decode("utf-8")) != profile:
        _deny("ORIGINAL_EXECUTION_PROFILE_CHANGED")
    if (operation == "REPAIR" and policy.mutation_action not in {"CODE_FIX", "CONTINUE_IMPLEMENTATION"}
            or operation == "REMEDIATION" and policy.mutation_action != "CODE_FIX"
            or operation == "PRIMARY" and not owner.task.scope.modify):
        _deny("DEFAULT_NONCODING_ACTION_UNSUPPORTED")
    if owner.task.verification.policy not in {"minimum-sufficient-v1", "minimum-sufficient-v2"}:
        _deny("DEFAULT_VERIFICATION_POLICY_UNSUPPORTED")
    if dict(_app_refs(control, url, ("refs/heads/main",))) != refs:
        _deny("SOURCE_MOVED_DURING_ADMISSION")
    upgrade = _kernel_upgrade_authority(owner, operation, policy, profile)
    return dict(implementation=DEFAULT_IMPLEMENTATION, ka01_version=VERSION, profile=PROFILE,
        policy=owner.task.verification.policy,
        normative=dict(source_sha=main, path=NORMATIVE_PATH, sha256=DEFAULT_NORMATIVE_SHA256),
        global_policy=dict(mode=DEFAULT_MODE, authority="HUMAN_RATIFIED_CANONICAL_SOURCE",
            task="TASK-425", revision=1, operations=sorted(OPERATIONS), prospective_only=True),
        selection=None, consent=None, original_selector=None, overrides=[], effective_limits=resolve_limits([]),
        operation=operation, execution_profile=profile.as_dict(), source=source,
        implementation_main_sha=main, implementation_source=implementation_source,
        production_activation="DEFAULT_ENROLLED_NATIVE_CAPABILITY_REQUIRED",
        continuation="SAME_ORIGINAL_LIVE_SESSION_ONLY",
        native_feedback_delivery="REQUIRES_ORIGINAL_NATIVE_COMPLETED_TURN",
        **(dict(kernel_upgrade=upgrade) if upgrade is not None else {}))


def _authority(owner, request, operation, profile, policy):
    if type(request) is _DefaultAdmission:
        request.__post_init__()
        return _default_authority(owner, operation, profile, policy)
    from .github_issue_ingress import _decode_policy, _authenticate_selection_issue, _github_selection_json
    from .verification import _ApplicabilityReader, _original_remote, _original_git, _original_local, _app_refs
    from .operator import _ka01_operation_source
    from .execution_profile import ResolvedExecutionProfile, parse_execution_profile
    from .runtime import CompletionPolicy

    if type(operation) is not str or operation not in OPERATIONS or type(policy) is not CompletionPolicy or policy.kind != operation:
        _deny("OPERATION_NOT_ADMITTED")
    if type(profile) is not ResolvedExecutionProfile:
        _deny("EXACT_SELECTED_PROFILE_REQUIRED")
    profile.__post_init__()
    if (profile.run_id, profile.executor) != (owner.run.run_id, owner.run.executor):
        _deny("PROFILE_RUN_MISMATCH")
    production = request.version in (2, 3)
    interactive = request.version == 3
    implementation = INTERACTIVE_IMPLEMENTATION if interactive else PRODUCTION_IMPLEMENTATION if production else IMPLEMENTATION
    if production and (operation != "PRIMARY" or owner.run.executor != "codex"
            or owner.task.verification.policy != "minimum-sufficient-v1"):
        _deny("PRODUCTION_CONFIGURATION_UNSUPPORTED")
    if production:
        profile_raw = _original_local(owner.run_path.parent.parent / "execution-profiles" /
            (owner.run.run_id + ".json"), 65536)
        if parse_execution_profile(profile_raw.decode("utf-8")) != profile:
            _deny("ORIGINAL_EXECUTION_PROFILE_CHANGED")
    url = _original_remote(owner.repo)
    # The historical version-1 interface remains strictly controlled/offline.
    # Version 2 uses the ordinary configured upstream, including HTTPS/SSH.
    remote = Path(url)
    if not production:
        if (not remote.is_absolute() or not remote.is_dir() or remote.resolve() != remote.absolute()
                or not (remote / "HEAD").is_file() or not (remote / "objects").is_dir()):
            _deny("CONTROLLED_OFFLINE_SOURCE_REQUIRED")
        if _original_git(owner.repo, "--git-dir=" + str(remote), "rev-parse", "--is-bare-repository").strip() != b"true":
            _deny("CONTROLLED_OFFLINE_BARE_SOURCE_REQUIRED")
    reader = _ApplicabilityReader(owner.repo)
    selected_ref = "refs/heads/aios/ka01-v13-consent/" + owner.run.run_id
    patterns = ("refs/heads/main", selected_ref, selected_ref + "/*")

    def selected_refs():
        refs = dict(_app_refs(owner.repo, url, patterns))
        # Git can hide packed descendants behind a loose parent ref. This
        # controlled bare source must also reject that inconsistent storage;
        # packed bytes supply only a negative guard, never selection authority.
        packed = remote / "packed-refs"
        if remote.is_absolute() and remote.is_dir() and os.path.lexists(packed):
            prefix = (selected_ref + "/").encode("ascii")
            for line in _original_local(packed, 32768).splitlines():
                if not line.startswith((b"#", b"^")) and line.partition(b" ")[2].startswith(prefix):
                    _deny("EXACT_UNAMBIGUOUS_FIRST_PARTY_SELECTION_REQUIRED")
        if set(refs) != {"refs/heads/main", selected_ref}:
            _deny("EXACT_UNAMBIGUOUS_FIRST_PARTY_SELECTION_REQUIRED")
        return refs

    refs = selected_refs()
    if production:
        # Only the exact freshly observed selector object is fetched, using
        # the existing bounded source transport. No ref is installed or trusted
        # merely because its bytes were delivered.
        from .review_transport import _l1_fetch
        try:
            _original_git(owner.repo, "cat-file", "-e", refs[selected_ref] + "^{commit}")
        except VerificationContractError:
            _l1_fetch(owner.repo, url, refs[selected_ref])
    selection_tree, parents = reader.commit(refs[selected_ref])
    if parents or reader.blob(selection_tree, ".ai/transport/ka01-v13.json") != _bytes(asdict(request)):
        _deny("ORIGINAL_FIRST_PARTY_SELECTION_MISMATCH")
    main = refs["refs/heads/main"]
    if production and main != owner.run.base_sha:
        _deny("ORIGINAL_MAIN_MOVED")
    tree, _ = reader.commit(main)
    ingress_bytes = reader.blob(tree, ".ai/brain-ingress-carriers.yaml")
    ingress = _decode_policy(ingress_bytes)

    def human(number, digest):
        _hash(digest)
        provenance, raw = _authenticate_selection_issue(ingress, number)
        if _digest(raw) != digest:
            _deny("HUMAN_SOURCE_CHANGED_OR_REVOKED")
        current = _github_selection_json(ingress.repository, f"/issues/{number}")
        if (current.get("state") != "open" or type(current.get("body")) is not str
                or current["body"].encode("utf-8") != raw or current.get("id") != provenance["issue_id"]
                or current.get("node_id") != provenance["issue_node_id"]
                or current.get("user", {}).get("login") != provenance["actor"]
                or current.get("user", {}).get("id") != provenance["actor_id"]):
            _deny("HUMAN_SOURCE_WITHDRAWN")
        return _decode(raw), dict(issue=number, sha256=digest, provenance=provenance)

    selection, selected_source = human(request.selection_issue, request.selection_sha256)
    _exact(selection, ("format", "version", "implementation", "ka01_version", "profile", "policy",
                       "task", "operations", "normative", "overrides", "remote_url"))
    task = dict(id=owner.task.task_id, revision=owner.task.revision)
    if (selection["format"] != "AIOS_KA01_V13_SELECTION" or type(selection["version"]) is not int
            or selection["version"] != 1 or selection["implementation"] != implementation
            or selection["ka01_version"] != VERSION or selection["profile"] != PROFILE
            or selection["policy"] not in {"minimum-sufficient-v1", "minimum-sufficient-v2"}
            or owner.task.verification.policy != selection["policy"] or _bytes(selection["task"]) != _bytes(task)
            or selection["remote_url"] != url):
        _deny("EXACT_VERSION_POLICY_TASK_REQUIRED")

    def operations(value):
        if (type(value) is not list or not value or any(type(v) is not str for v in value)
                or len(set(value)) != len(value) or set(value) - OPERATIONS or operation not in value):
            _deny("EXPLICIT_OPERATION_REQUIRED")
    operations(selection["operations"])
    normative = _exact(selection["normative"], ("source_sha", "path", "sha256"))
    if normative["path"] != NORMATIVE_PATH or normative["sha256"] not in {HISTORICAL_NORMATIVE_SHA256, PRE_UPGRADE_NORMATIVE_SHA256, NORMATIVE_SHA256}:
        _deny("EXACT_PUBLISHED_NORM_REQUIRED")
    source_tree = reader.anchors(main, {normative["source_sha"]})[normative["source_sha"]]
    norm = reader.blob(source_tree, NORMATIVE_PATH)
    if _digest(norm) != normative["sha256"] or reader.blob(tree, NORMATIVE_PATH) != norm:
        _deny("NORMATIVE_SOURCE_CHANGED")
    implementation_source = None
    if production:
        if normative["source_sha"] != main:
            _deny("EXACT_CURRENT_ACTIVATION_SOURCE_REQUIRED")
        implementation_source = _production_source(owner, reader, tree, interactive=interactive)
    overrides, override_sources = [], []
    if type(selection["overrides"]) is not list or len(selection["overrides"]) > 5:
        _deny("OVERRIDE_BOUND")
    for pin in selection["overrides"]:
        _exact(pin, ("issue", "sha256"))
        override, provenance = human(pin["issue"], pin["sha256"])
        _exact(override, ("format", "version", "ka01_version", "task", "operations", "values"))
        if (override["format"] != "AIOS_KA01_V13_OVERRIDE" or type(override["version"]) is not int
                or override["version"] != 1 or override["ka01_version"] != VERSION or _bytes(override["task"]) != _bytes(task)):
            _deny("OVERRIDE_TASK_VERSION_MISMATCH")
        operations(override["operations"])
        overrides.append(override["values"])
        override_sources.append(provenance)
    limits = resolve_limits(overrides)
    source = _ka01_operation_source(owner, operation, policy, production=production)
    consent, consent_source = human(request.consent_issue, request.consent_sha256)
    expected = dict(format="AIOS_KA01_V13_RUN_CONSENT", version=1,
        mode=INTERACTIVE_MODE if interactive else PRODUCTION_MODE if production else "CONTROLLED_PREACTIVATION",
        implementation=implementation, ka01_version=VERSION,
        selection_sha256=request.selection_sha256, task=task, run_id=owner.run.run_id,
        admitted_run_sha256=source["admitted_run_sha256"], operation=operation,
        executor=owner.run.executor, execution_profile_sha256=_digest(profile.as_dict()),
        operation_source_sha256=_digest(source), effective_limits_sha256=_digest(limits))
    if _bytes(consent) != _bytes(expected):
        _deny("EXACT_FRESH_RUN_CONSENT_REQUIRED")
    if selected_refs() != refs:
        _deny("SOURCE_MOVED_DURING_ADMISSION")
    authority = dict(implementation=implementation,
        ka01_version=VERSION, profile=PROFILE,
        policy=selection["policy"], normative=normative, ingress_policy_sha256=_digest(ingress_bytes),
        selection=selected_source, overrides=override_sources, consent=consent_source,
        original_selector=dict(ref=selected_ref, sha=refs[selected_ref]),
        effective_limits=limits, operation=operation, execution_profile=profile.as_dict(), source=source,
        production_activation="SUPPORTED_INTERACTIVE_PRIMARY" if interactive else "SUPPORTED_BOUNDED_PRIMARY" if production else "NOT_ACTIVATED",
        continuation="SAME_ORIGINAL_LIVE_SESSION_ONLY" if interactive else "NOT_AUTHORIZED")
    if production:
        authority.update(implementation_main_sha=main, implementation_source=implementation_source,
            native_feedback_delivery="REQUIRES_ORIGINAL_NATIVE_COMPLETED_TURN" if interactive else "UNSUPPORTED_SAME_LIVE_RECIPIENT_CHANNEL_UNAVAILABLE")
    return authority


class _AdmissionBudget:
    """One original Runtime's private capability; no disk-to-live constructor."""

    def __init__(self, owner, request, operation, profile, policy, leases, lease):
        from .executor import _lease_matches_run
        from .run import RunLease, RunLeaseRegistry
        from .run_observation import RunObservationTracker
        from .runtime import _checkpoint_write_once, _checkpoint_make_directory
        if type(request) not in {AdmissionRequest, _DefaultAdmission}:
            _deny("TYPED_REQUEST_REQUIRED")
        request.__post_init__()
        tracker = owner._ka01_original_tracker
        if (type(tracker) is not RunObservationTracker or owner.observation_tracker is not tracker
                or tracker._clock is not time.monotonic or tracker._run != owner.run or not tracker._valid
                or tracker._admitted_at is None or tracker._admitted_at != owner._ka01_admitted_at
                or tracker.operation != operation or tracker._executor_invoked or tracker._verification_elapsed is not None
                or tracker._finalized):
            _deny("ORIGINAL_ACTIVE_RUN_TIMING_NOT_ESTABLISHED")
        self.tracker = tracker
        if (type(leases) is not RunLeaseRegistry or type(lease) is not RunLease
                or leases.holder(owner.task.task_id) is not lease or not _lease_matches_run(lease, owner.run)):
            _deny("ORIGINAL_LIVE_LEASE_REQUIRED")
        self.owner, self.request, self.operation = owner, request, operation
        self.default = type(request) is _DefaultAdmission
        self.production = request.version in (2, 3, 4)
        self.interactive = request.version in (3, 4)
        self.session = self.session_identity = None
        self.native_state, self.native_calls, self.package_digest = "NOT_STARTED", 0, None
        self.native_dispatcher = self.native_adapter = None
        self.profile, self.policy, self.leases, self.lease = profile, policy, leases, lease
        self.thread, self.boundary, self.frame = get_ident(), None, None
        self.started_ns, self.started_real_ns = owner._ka01_started_ns, owner._ka01_started_real_ns
        self.last_ns, self.stopped, self.pending = self.started_ns, False, None
        self.records, self.effects = [], {}
        self.verification_spent_ns = self.verification_reserved_ns = self.verification_settled_ns = 0
        self.cleanup_spent_ns = 0
        self.accounted_until_ns = self.started_ns
        self.checkpoints_reserved = self.checkpoints_spent = 0
        self.corrections_reserved = self.corrections_spent = 0
        self.work_started = None
        self.outer_method = None
        self.terminal_digest = None
        self.authority = _bytes(_authority(owner, request, operation, profile, policy))
        self.limits = MappingProxyType(json.loads(self.authority)["effective_limits"])
        # No existing first-party native transport authenticates the complete
        # applicable cumulative token population. A finite cap therefore STOPs;
        # a null cap requires no token input or invented KA-01 token ceiling.
        if self.limits["max_tokens"] is not None:
            _deny("AUTHENTICATED_CUMULATIVE_TOKENS_NOT_ESTABLISHED")
        self.wall_limit_ns = _minutes_ns(self.limits["max_run_wall_minutes"])
        self.verification_limit_ns = _minutes_ns(self.limits["max_runtime_verification_minutes"])
        directory = owner._checkpoint_directory()
        self.directory = directory.parent / "ka01-v13"
        self.admission = _bytes(dict(format="AIOS_KA01_V13_ADMISSION", version=1,
            authority=json.loads(self.authority), started_monotonic_ns=self.started_ns,
            started_wall_ns=self.started_real_ns, lease_identity=_digest(os.urandom(32))))
        with leases.held(lease):
            if os.path.lexists(self.directory):
                _deny("ORIGINAL_ADMISSION_ALREADY_EXISTS")
            _checkpoint_make_directory(self.directory)
            _checkpoint_write_once(self.directory / "admission.json", self.admission)
        owner._ka01_budget = self
        self._facts_seal = _bytes(self._facts())
        self.guard(live=False)

    def _facts(self):
        return dict(checkpoints_reserved=self.checkpoints_reserved, checkpoints_spent=self.checkpoints_spent,
            corrections_reserved=self.corrections_reserved, corrections_spent=self.corrections_spent,
            verification_reserved_ns=self.verification_reserved_ns, verification_spent_ns=self.verification_spent_ns,
            verification_settled_ns=self.verification_settled_ns, cleanup_spent_ns=self.cleanup_spent_ns,
            accounted_until_ns=self.accounted_until_ns, pending=self.pending, effects=self.effects,
            production=self.production, interactive=self.interactive, default=self.default,
            session_identity=self.session_identity, native_state=self.native_state, native_calls=self.native_calls,
            package_digest=self.package_digest)

    def stop(self):
        self.stopped = True
        self.owner._local_feedback_stopped = True
        if self.interactive and self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass  # Preserve the original STOP/terminal cause.

    def _clock(self):
        now, real = time.monotonic_ns(), time.time_ns()
        if (type(now) is not int or type(real) is not int or now < self.last_ns
                or abs((real - self.started_real_ns) - (now - self.started_ns)) > 5_000_000_000):
            _deny("CLOCK_CONTINUITY_UNKNOWN")
        self.last_ns = now
        if now - self.started_ns >= self.wall_limit_ns:
            _deny("RUN_WALL_EXHAUSTED")
        return now

    def guard(self, *, live=True):
        from .runtime import _checkpoint_safe_directory
        from .verification import _original_local, MAX_ORIGINAL_PROOF_BYTES
        try:
            if (self.stopped or self.owner._local_feedback_stopped
                    or self.thread != get_ident() or self.owner._ka01_budget is not self):
                _deny("STOP_LATCHED_OR_REPLACEMENT_OWNER")
            if self.leases.holder(self.owner.task.task_id) is not self.lease:
                _deny("ORIGINAL_LEASE_LOST")
            if (self.owner.observation_tracker is not self.tracker or self.tracker._clock is not time.monotonic
                    or self.tracker._admitted_at != self.owner._ka01_admitted_at
                    or self.tracker._run != self.owner.run or not self.tracker._valid or self.tracker._finalized):
                _deny("ORIGINAL_RUN_TIMING_CHANGED")
            if _bytes(self._facts()) != self._facts_seal or dict(self.limits) != json.loads(self.authority)["effective_limits"]:
                _deny("ORIGINAL_RESOURCE_FACTS_CHANGED")
            self._clock()
            if self.verification_spent_ns >= self.verification_limit_ns:
                _deny("RUNTIME_VERIFICATION_EXHAUSTED")
            if self.pending is not None and self.work_started is None:
                _deny("INTERRUPTED_ACCOUNTING_UNKNOWN")
            _checkpoint_safe_directory(self.directory)
            if len(self.records) > 256:
                _deny("ACCOUNTING_EVENT_BOUND")
            expected_names = {"admission.json", *(f"e{i:03d}.json" for i in range(1, len(self.records) + 1))}
            names = set()
            with os.scandir(self.directory) as entries:
                for index, entry in enumerate(entries):
                    if index >= 257 or entry.name not in expected_names:
                        _deny("ACCOUNTING_HISTORY_UNKNOWN")
                    names.add(entry.name)
            if names != expected_names:
                _deny("ACCOUNTING_HISTORY_UNKNOWN")
            if _original_local(self.directory / "admission.json", MAX_ORIGINAL_PROOF_BYTES) != self.admission:
                _deny("ORIGINAL_ADMISSION_CHANGED")
            for index, raw in enumerate(self.records, 1):
                if _original_local(self.directory / f"e{index:03d}.json", MAX_ORIGINAL_PROOF_BYTES) != raw:
                    _deny("ORIGINAL_ACCOUNTING_CHANGED")
            if _bytes(_authority(self.owner, self.request, self.operation, self.profile, self.policy)) != self.authority:
                _deny("ORIGINAL_AUTHORITY_WITHDRAWN_OR_CHANGED")
            if live and self.production:
                dispatcher, adapter = self.native_dispatcher, self.native_adapter
                if (self.native_state != "EXECUTING" or dispatcher is None or not dispatcher._invoked
                        or dispatcher._prospective_runtime is not self.owner
                        or dispatcher._execution_profile != self.profile or dispatcher._operation != self.operation
                        or dispatcher._selected_executor != self.owner.run.executor
                        or getattr(adapter, "execution_profile", None) != self.profile
                        or getattr(adapter, "executor", None) != self.owner.run.executor):
                    _deny("ORIGINAL_INVOCATION_LOST")
            elif live:
                frame, boundary = self.frame, self.boundary
                if (frame is None or boundary._live is not frame or not frame.executing or frame.stopped
                        or frame.thread != get_ident() or frame.owner is not self.owner
                        or frame.lease is not self.lease or frame.profile != self.profile
                        or boundary._execution_profile != self.profile
                        or getattr(frame.adapter, "execution_profile", None) != frame.profile
                        or getattr(frame.adapter, "confirm_local_feedback", None) != frame.callback):
                    _deny("ORIGINAL_INVOCATION_LOST")
            if self.interactive and self.session_identity is not None:
                if (self.session is None or self.native_adapter._live_transport is not self.session
                        or self.native_adapter.execution_profile != self.profile
                        or self.native_dispatcher._execution_profile != self.profile
                        or self.native_dispatcher._prospective_runtime is not self.owner):
                    _deny("ORIGINAL_NATIVE_SESSION_LOST")
                self.session.assert_live(self.session_identity)
                self.session.assert_idle()
            self._clock()
        except BaseException as exc:
            if self.default:
                _stop_reason(self.owner, exc)
            self.stop()
            raise

    @contextmanager
    def production_invocation(self, dispatcher, adapter):
        """One ordinary native call; no local callback is a delivery receipt."""
        from .runtime import _checkpoint_mutation
        self.guard(live=False)
        if not self.production or self.native_state != "NOT_STARTED":
            self.stop()
            _deny("NATIVE_INVOCATION_ALREADY_RESERVED")
        self.native_dispatcher, self.native_adapter = dispatcher, adapter
        try:
            with _checkpoint_mutation(self.owner._checkpoint_directory(), dict(kind="KA01_NATIVE_RESERVATION"),
                    retain_ambiguity=False):
                self.native_state = "EXECUTING"
                self._append(dict(state="NATIVE_RESERVED", execution_profile_sha256=_digest(self.profile.as_dict())))
                self._facts_seal = _bytes(self._facts())
            self.guard()
            yield
            self.guard()
            if self.native_calls != 1 or self.package_digest is None:
                _deny("ORIGINAL_NATIVE_RESULT_REQUIRED")
            with _checkpoint_mutation(self.owner._checkpoint_directory(), dict(kind="KA01_NATIVE_RETURN"),
                    retain_ambiguity=False):
                self.native_state = "RETURNED"
                self.accounted_until_ns = self._clock()
                self._append(dict(state="NATIVE_RETURNED", package_sha256=self.package_digest,
                    wall_elapsed_ns=self.last_ns - self.started_ns,
                    feedback_delivery=("SOURCE_CORRELATED_CANDIDATE_RECEIVED" if self.corrections_spent else "NOT_REQUESTED")
                        if self.interactive else "UNSUPPORTED_SAME_LIVE_RECIPIENT_CHANNEL_UNAVAILABLE"))
                self._facts_seal = _bytes(self._facts())
        except BaseException as exc:
            if self.default:
                _stop_reason(self.owner, exc)
            self.stop()
            raise

    def native_runner(self, runner):
        """Clamp the existing provider watchdog to remaining RUN wall time."""
        @wraps(runner)
        def invoke(command, **kwargs):
            try:
                self.guard()
                if self.native_calls:
                    _deny("NATIVE_INVOCATION_ALREADY_RESERVED")
                self.native_calls = 1  # Sticky before the only original process.
                self._facts_seal = _bytes(self._facts())
                remaining = min(self.wall_limit_ns - (self._clock() - self.started_ns), 10**300) / 1_000_000_000
                current = kwargs.get("timeout")
                kwargs["timeout"] = remaining if current is None else min(current, remaining)
                result = runner(command, **kwargs)
                self.guard()
                return result
            except BaseException:
                self.stop()
                raise
        return invoke

    def poll_native(self):
        """Wall/lease/process checks while the Executor may have a dirty tree.

        Full source and Human reauthentication occurs before/after each turn
        and each protected Runtime effect, never against half-written work.
        """
        try:
            if (self.stopped or self.owner._local_feedback_stopped or self.thread != get_ident()
                    or self.owner._ka01_budget is not self
                    or self.leases.holder(self.owner.task.task_id) is not self.lease
                    or self.native_state != "EXECUTING"
                    or getattr(self.native_adapter, "execution_profile", None) != self.profile):
                _deny("ORIGINAL_SESSION_AUTHORITY_LOST")
            if self.default and (self.native_dispatcher is None
                    or self.native_dispatcher._prospective_runtime is not self.owner
                    or self.native_dispatcher._operation != self.operation
                    or self.native_dispatcher._selected_executor != self.owner.run.executor
                    or self.native_dispatcher._execution_profile != self.profile
                    or getattr(self.native_adapter, "_interactive_runtime", None) is not self.owner):
                _deny("ORIGINAL_SESSION_OPERATION_OR_EXECUTOR_LOST")
            self._clock()
            if self.session is not None:
                self.session.assert_live(self.session_identity)
        except BaseException:
            self.stop()
            raise

    def open_native_session(self, factory):
        """Reserve before spawn; no exception permits replacement or replay."""
        from .runtime import _checkpoint_mutation
        self.guard()
        if not self.interactive or self.native_calls or self.session is not None:
            self.stop()
            _deny("NATIVE_INVOCATION_ALREADY_RESERVED")
        try:
            with _checkpoint_mutation(self.owner._checkpoint_directory(), dict(kind="NATIVE_PROCESS")):
                self.native_calls = 1
                self._append(dict(state="NATIVE_PROCESS_RESERVED"))
                self._facts_seal = _bytes(self._facts())
                self.session = factory()
                self.native_adapter._live_transport = self.session
                self.session_identity = self.session.identity()
                self._append(dict(state="NATIVE_SESSION_BOUND", session=self.session_identity))
                self._facts_seal = _bytes(self._facts())
            self.guard()
            return self.session
        except BaseException:
            self.stop()
            if self.session is not None:
                self.session.close()
            raise

    def returned_package(self, package):
        from .runtime import result_package_data
        self.guard()
        if self.package_digest is not None:
            self.stop()
            _deny("NATIVE_RESULT_ALREADY_RETURNED")
        self.package_digest = _digest(result_package_data(package))
        self._facts_seal = _bytes(self._facts())

    def observation(self):
        """Bounded original-caller facts, without review/publication inference."""
        if not self.production:
            _deny("PRODUCTION_OBSERVATION_UNAVAILABLE")
        authority = json.loads(self.authority)
        limits = dict(self.limits)
        return dict(implementation=authority["implementation"], ka01_version=VERSION,
            status=("COMPLETED_" + self.operation if self.default else "COMPLETED_SUPPORTED_PRIMARY")
                if self.terminal_digest is not None and self.pending is None
                else "STOPPED" if self.stopped else "ADMITTED_" + self.operation if self.default else "ADMITTED_SUPPORTED_PRIMARY",
            configuration=DEFAULT_MODE if self.default else INTERACTIVE_MODE if self.interactive else PRODUCTION_MODE,
            operation=self.operation, run_id=self.owner.run.run_id,
            source_main_sha=authority.get("implementation_main_sha"),
            **(dict(kernel_upgrade=dict(implementation=KERNEL_UPGRADE_IMPLEMENTATION,
                source_main_sha=authority["kernel_upgrade"]["source_main_sha"],
                original_source_sha256=_digest(self.owner._kernel_upgrade_source.content)))
                if "kernel_upgrade" in authority else {}),
            selection_sha256=getattr(self.request, "selection_sha256", None),
            consent_sha256=getattr(self.request, "consent_sha256", None),
            **(dict(global_policy_sha256=_digest(authority["global_policy"]),
                operation_source_sha256=_digest(authority["source"]),
                stop_reason=getattr(self.owner, "_ka01_stop_reason", None),
                checkpoint_digests=[r.digest for r in self.owner._interactive_checkpoints]) if self.default else {}),
            execution_profile_sha256=_digest(self.profile.as_dict()),
            effective_limits=limits if len(_bytes(limits)) <= 4096 else None,
            effective_limits_sha256=_digest(limits), effective_limits_source="ORIGINAL_RUNTIME_ADMISSION",
            native_invocations=self.native_calls,
            checkpoints_spent=self.checkpoints_spent, corrections_spent=self.corrections_spent,
            verification_spent_ns=self.verification_spent_ns,
            native_feedback_delivery=("SOURCE_CORRELATED_CANDIDATE_RECEIVED" if self.corrections_spent
                else "PREPARED_CONSUMPTION_NOT_ESTABLISHED" if self.owner._interactive_feedback
                else "NOT_REQUESTED" if self.session_identity is not None else "BLOCKER_NATIVE_SESSION_UNAVAILABLE")
                if self.interactive else "UNSUPPORTED_SAME_LIVE_RECIPIENT_CHANNEL_UNAVAILABLE",
            continuation="SAME_ORIGINAL_LIVE_SESSION_ONLY" if self.interactive else "NOT_AUTHORIZED",
            **(dict(native_session=self.session_identity,
                    turns=list(getattr(self.session, "turn_observations", ())),
                    checkpoints_reserved=self.checkpoints_reserved, corrections_reserved=self.corrections_reserved,
                    verification_reserved_ns=self.verification_reserved_ns,
                    verification_settled_ns=self.verification_settled_ns,
                    unresolved_reservation=self.pending,
                    unavailable_evidence=["AUTHENTICATED_CUMULATIVE_TOKENS", "CROSS_CANDIDATE_APPLICABILITY"],
                    final_status="RESULT" if self.terminal_digest is not None else "STOP" if self.stopped else "ACTIVE")
               if self.interactive else {}), review="NOT_OBSERVED", publication="NOT_OBSERVED")

    def bind(self, boundary, frame):
        self.guard(live=False)
        if (self.frame is not None or frame.lease is not self.lease or boundary._leases is not self.leases
                or frame.profile != self.profile or boundary._execution_profile != self.profile):
            self.stop()
            _deny("REPLACEMENT_INVOCATION")
        if self.operation != "PRIMARY":
            self.stop()
            _deny("CORRECTION_NATIVE_CONTINUITY_BLOCKED")
        self.boundary, self.frame = boundary, frame

    def _append(self, data):
        from .runtime import _checkpoint_write_once, _checkpoint_fence
        from .verification import _original_local, MAX_ORIGINAL_PROOF_BYTES
        if len(self.records) >= 256:
            _deny("ACCOUNTING_EVENT_BOUND")
        # All records share the original Runtime mutation fence, never a lock
        # or reducer whose possession independently grants an effect.
        fence = _checkpoint_fence(self.owner._checkpoint_directory())
        _original_local(fence / "intent.json", MAX_ORIGINAL_PROOF_BYTES)
        raw = _bytes(dict(version=1, index=len(self.records) + 1, **data))
        _checkpoint_write_once(self.directory / f"e{len(self.records) + 1:03d}.json", raw)
        self.records.append(raw)

    def remaining_ns(self):
        now = self._clock()
        elapsed = 0 if self.work_started is None else now - self.work_started
        remaining = min(self.wall_limit_ns - (now - self.started_ns),
                        self.verification_limit_ns - self.verification_spent_ns - elapsed)
        if remaining <= 0:
            self.stop()
            _deny("TIME_CAPACITY_EXHAUSTED")
        return remaining

    def remaining_seconds(self):
        # Finite very large Human integer overrides stay exact in admission
        # and accounting. Bound only the float conversion used by subprocess;
        # the existing stricter native timeout is still applied by the caller.
        return min(self.remaining_ns(), 10**300) / 1_000_000_000

    def charge_cleanup(self):
        """Include settlement/fsync/fence cleanup through the public return.

        These original in-process facts are carried into the next protected
        resource record. A crash cannot reconstruct them or restart this RUN.
        No cleanup measurement creates another mutation or releases capacity.
        """
        try:
            now = self._clock()
            elapsed = now - self.accounted_until_ns
            self.cleanup_spent_ns += elapsed
            self.verification_spent_ns += elapsed
            self.accounted_until_ns = now
            self._facts_seal = _bytes(self._facts())
            if self.verification_spent_ns >= self.verification_limit_ns:
                self.stop()
                _deny("RUNTIME_VERIFICATION_EXHAUSTED")
        except BaseException:
            self.stop()
            raise

    @contextmanager
    def effect(self, intent):
        self.guard(live=not self.production)
        self.capacity(intent)
        key = _digest(intent)
        replay = key in self.effects
        kind = intent["kind"]
        checkpoints = int(kind in {"CHECKPOINT", "PRODUCTION_COMPLETION", "NATIVE_CHECKPOINT"} and not replay)
        corrections = int(kind in {"LOCAL_FEEDBACK", "NATIVE_FEEDBACK"} and not replay)
        started = self._clock()
        preparation = 0 if self.work_started is None else started - self.work_started
        if self.verification_spent_ns + preparation >= self.verification_limit_ns:
            _deny("RUNTIME_VERIFICATION_EXHAUSTED")
        self.verification_spent_ns += preparation
        self.accounted_until_ns = started
        self.work_started = started
        reserved = self.remaining_ns()
        self.checkpoints_reserved += checkpoints
        self.corrections_reserved += corrections
        self.verification_reserved_ns += reserved
        self.pending = key
        self._append(dict(state="RESERVED", key=key, kind=kind, replay=replay,
            checkpoints_reserved=self.checkpoints_reserved, corrections_reserved=self.corrections_reserved,
            verification_reserved_ns=self.verification_reserved_ns, interval_reserved_ns=reserved,
            verification_spent_ns=self.verification_spent_ns, preparation_spent_ns=preparation,
            cleanup_spent_ns=self.cleanup_spent_ns,
            wall_elapsed_ns=self.last_ns - self.started_ns))
        self._facts_seal = _bytes(self._facts())
        charged = False
        try:
            yield
            if self.terminal_digest is None:
                self.guard(live=not self.production)
            else:
                from .verification import _original_local, MAX_ORIGINAL_PROOF_BYTES
                raw = _original_local(self.owner.state.results / (self.owner.run.run_id + ".json"), MAX_ORIGINAL_PROOF_BYTES)
                if kind not in {"TERMINAL", "PRODUCTION_COMPLETION", "NATIVE_TERMINAL"} or _digest(raw) != self.terminal_digest:
                    _deny("TERMINAL_SETTLEMENT_CONFLICT")
            spent = self._clock() - started
            if spent > reserved or self.verification_spent_ns + spent >= self.verification_limit_ns:
                _deny("VERIFICATION_RESERVATION_EXHAUSTED")
            self.verification_spent_ns += spent
            self.accounted_until_ns = self.last_ns
            charged = True
            self.verification_settled_ns += reserved
            self.checkpoints_spent += checkpoints
            self.corrections_spent += corrections
            self._append(dict(state="SETTLED", key=key, kind=kind, replay=replay,
                checkpoints_spent=self.checkpoints_spent, corrections_spent=self.corrections_spent,
                verification_spent_ns=self.verification_spent_ns, verification_settled_ns=self.verification_settled_ns,
                cleanup_spent_ns=self.cleanup_spent_ns,
                interval_spent_ns=spent, interval_unused_ns=reserved - spent,
                wall_elapsed_ns=self.last_ns - self.started_ns))
            self.effects[key] = kind
            self.pending = None
            self._facts_seal = _bytes(self._facts())
            if self.terminal_digest is not None:
                self.stop()
        except BaseException:
            self.stop()
            # Preserve proven elapsed cost even if the effect is unresolved.
            # Its reservation stays outstanding; unknown work never becomes a
            # zero-cost release or permission to retry. Failure to append also
            # leaves the original fence and reservation intact.
            try:
                now, real = time.monotonic_ns(), time.time_ns()
                if (type(now) is int and type(real) is int and now >= self.last_ns
                        and abs((real - self.started_real_ns) - (now - self.started_ns)) <= 5_000_000_000):
                    elapsed = now - started
                    if not charged:
                        self.verification_spent_ns += elapsed
                        self.accounted_until_ns = now
                    self._append(dict(state="INTERRUPTED", key=key, kind=kind,
                        verification_spent_ns=self.verification_spent_ns, interval_lower_bound_ns=elapsed,
                        wall_elapsed_ns=now - self.started_ns, reservation="OUTSTANDING_UNKNOWN_EFFECT"))
                    self._facts_seal = _bytes(self._facts())
            except Exception:
                pass
            raise

    def capacity(self, intent):
        """Read-only preflight, repeated inside the original protected effect."""
        if self.pending is not None:
            _deny("DUPLICATE_OR_CONCURRENT_RESERVATION")
        key = _digest(intent)
        replay = key in self.effects
        kind = intent["kind"]
        checkpoints = int(kind in {"CHECKPOINT", "PRODUCTION_COMPLETION", "NATIVE_CHECKPOINT"} and not replay)
        corrections = int(kind in {"LOCAL_FEEDBACK", "NATIVE_FEEDBACK"} and not replay)
        if kind in {"FACTUAL_FEEDBACK", "NATIVE_FACTUAL_FEEDBACK"} and not replay and self.corrections_reserved >= min(
                self.limits["max_corrections"], self.limits["max_checkpoints"] - 1):
            _deny("CORRECTION_CAPACITY_EXHAUSTED")
        if checkpoints and self.checkpoints_reserved >= min(self.limits["max_checkpoints"], self.limits["max_corrections"] + 1):
            _deny("CHECKPOINT_CAPACITY_EXHAUSTED")
        if corrections and self.corrections_reserved >= min(self.limits["max_corrections"], self.limits["max_checkpoints"] - 1):
            _deny("CORRECTION_CAPACITY_EXHAUSTED")
        if checkpoints and self.checkpoints_reserved and self.corrections_spent != self.checkpoints_reserved:
            _deny("PRIOR_FEEDBACK_NOT_CONSUMED")


def guarded(method):
    """Keep unopted native APIs byte-for-byte on their original code path."""
    @wraps(method)
    def call(owner, *args, **kwargs):
        budget = owner._ka01_budget
        if budget is None:
            if owner._ka01_opted:
                _deny("OPTED_RUN_STOP_LATCHED")
            return method(owner, *args, **kwargs)
        entered = False
        try:
            if budget.production and method.__name__ != "complete" and (not budget.interactive
                    or method.__name__ not in {"_seal_interactive_candidate", "_prepare_interactive_feedback", "_accept_interactive_correction"}):
                _deny("NATIVE_FEEDBACK_UNSUPPORTED")
            if budget.work_started is not None:
                if method.__name__ == "terminal_reuse" and budget.outer_method == "complete":
                    return method(owner, *args, **kwargs)
                _deny("BOUNDARY_REENTRY")
            if budget.pending is not None:
                _deny("INTERRUPTED_ACCOUNTING_UNKNOWN")
            budget.work_started = budget._clock()
            entered = True
            budget.outer_method = method.__name__
            budget.remaining_ns()
            budget.guard(live=not budget.production)
            budget.remaining_ns()
            before = len(budget.records)
            value = method(owner, *args, **kwargs)
            if budget.terminal_digest is None and len(budget.records) == before:
                from .runtime import _checkpoint_mutation
                directory = owner._checkpoint_directory()
                with _checkpoint_mutation(directory, dict(version=1, kind="KA01_INSPECTION")):
                    with budget.effect(dict(kind="INSPECTION", boundary=method.__name__)):
                        pass
            budget.charge_cleanup()
            return value
        except BaseException as exc:
            if budget.default:
                _stop_reason(owner, exc)
            budget.stop()
            raise
        finally:
            if entered:
                budget.work_started = None
    return call
