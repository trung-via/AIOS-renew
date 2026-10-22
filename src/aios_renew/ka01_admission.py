"""KA-01 v1.3 preactivation admission, subordinate to RuntimeCompletion.

No production activation, issuer, dispatch, proof runner or lease reconstruction
lives here. Human authentication uses the existing ingress policy and original
Issue authenticator. Files are negative crash guards, never bearer authority.
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
G3_IMPLEMENTATION = "KA01-L3-06-G3-v1"
VERSION = "1.3"
PROFILE = "KA01-v1.3-default-resource-profile"
NORMATIVE_PATH = "docs/AIOS-KERNEL-KA01-PROSPECTIVE-AMENDMENT.md"
NORMATIVE_SHA256 = "474be079dd641690dbd44652efc54f6d80cc02557c6c8767d7ae0acb6841d280"
OPERATIONS = frozenset({"PRIMARY", "REPAIR", "REMEDIATION"})
DEFAULTS = dict(max_corrections=2, max_checkpoints=3, max_run_wall_minutes=240,
                max_runtime_verification_minutes=180, max_tokens=None)


def _deny(code):
    raise VerificationContractError("KA01_V13_" + code)


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
        if type(self.version) is not int or self.version != 1:
            _deny("UNSUPPORTED_INTERFACE_VERSION")
        for number in (self.selection_issue, self.consent_issue):
            if type(number) is not int or not 0 < number < 10**12:
                _deny("INVALID_ISSUE")
        for digest in (self.selection_sha256, self.consent_sha256):
            _hash(digest)


@dataclass(frozen=True)
class G3SemanticSources:
    """Original prospective sources, separate from the engineering TASK host.

    The host remains an ordinarily admitted TASK/RUN. No semantic document is
    converted into a legacy TASK or registered as a command policy.
    """

    main_sha: str
    candidate_sha: str
    task: object
    contract: object
    mapping: object

    def __post_init__(self):
        from .review_transport import GitSemanticSource, _l1_locator, _l1_sha
        for sha in (self.main_sha, self.candidate_sha):
            _l1_sha(sha)
        for value in (self.task, self.contract, self.mapping):
            if type(value) is not GitSemanticSource:
                _deny("EXACT_G3_SOURCE_LOCATOR_REQUIRED")
            _l1_locator(value)


@dataclass(frozen=True)
class G3NativeSource:
    """Pinned executable peer supporting the bounded, duplex G3 protocol."""

    path: str
    sha256: str
    interpreter_sha256: str

    def __post_init__(self):
        from .verification_contract import _app_path
        _app_path(self.path)
        for digest in (self.sha256, self.interpreter_sha256):
            _hash(digest)


@dataclass(frozen=True)
class G3AdmissionRequest:
    version: int
    selection_issue: int
    selection_sha256: str
    consent_issue: int
    consent_sha256: str
    semantic: G3SemanticSources
    native: G3NativeSource

    def __post_init__(self):
        AdmissionRequest(self.version, self.selection_issue, self.selection_sha256,
                         self.consent_issue, self.consent_sha256)
        if type(self.semantic) is not G3SemanticSources or type(self.native) is not G3NativeSource:
            _deny("EXACT_G3_LOCATORS_REQUIRED")
        self.semantic.__post_init__()
        self.native.__post_init__()


def _g3_semantic(owner, request):
    """Reopen sources and independent original Human/mapping review authority."""
    from .review_transport import read_semantic_task_readiness
    from .verification import _ApplicabilityReader, _original_remote, _original_decode
    from .proof_coverage_contract import validate_proof_contract, proof_mapping_digest

    pins = request.semantic
    reader = _ApplicabilityReader(owner.repo)
    content = {name: reader.object(getattr(pins, name).blob_sha, "blob")
               for name in ("task", "contract", "mapping")}
    from .task import parse_semantic_task
    semantic = parse_semantic_task(content["task"])
    # The independently admitted engineering host supplies execution authority;
    # every WHAT/WHY/scope/acceptance field must match the prospective source.
    for field in ("task_id", "revision", "goal", "problem", "assumptions", "scope",
                  "non_goals", "constraints", "acceptance"):
        left, right = getattr(semantic, field), getattr(owner.task, field)
        if field == "acceptance":
            left, right = sorted(left, key=lambda a: a.id), sorted(right, key=lambda a: a.id)
        if left != right:
            _deny("SEMANTIC_TASK_HOST_CONFLICT")
    task_pin = dict(task_id=semantic.task_id, revision=semantic.revision,
                    envelope_digest=_digest(content["task"]),
                    acceptance_ids=[a.id for a in semantic.acceptance])
    contract, mapping = _original_decode(content["contract"]), _original_decode(content["mapping"])
    parsed = validate_proof_contract(contract, expected_task=task_pin)
    declaration = dict(schema="task-semantic-proof-intake-v1", task=task_pin,
        candidate_sha=pins.candidate_sha,
        sources={name: dict(source_sha=getattr(pins, name).source_sha,
                            blob_sha=getattr(pins, name).blob_sha) for name in content},
        contract=contract, mapping=mapping)
    readiness = read_semantic_task_readiness(owner.repo, _bytes(declaration),
        expected_remote_url=_original_remote(owner.repo), expected_main_sha=pins.main_sha,
        expected_candidate_sha=pins.candidate_sha,
        sources={name: getattr(pins, name) for name in content})
    authority = readiness.authority
    if (readiness.task != semantic or authority.lineage is None or authority.human_selection is None
            or authority.reason != "EXACT_MAPPING_REVIEW_LINEAGE"):
        _deny("INDEPENDENT_FULL_SEMANTIC_REVIEW_REQUIRED")
    return readiness, dict(contract_id=parsed.id, contract_revision=parsed.revision,
        contract_sha256=parsed.digest, mapping_sha256=proof_mapping_digest(mapping),
        source_sha256=_digest(asdict(readiness)))


def _g3_native_source(owner, source):
    import sys
    from .verification import _original_git, _original_local
    from .verification_contract import _app_path
    _app_path(source.path)
    if source.path in owner.task.scope.modify:
        _deny("NATIVE_SOURCE_CANNOT_BE_CORRECTION_SCOPE")
    path = owner.repo / source.path
    if (path.absolute().resolve(strict=True) != path.absolute()
            or _digest(_original_local(path.absolute(), 262144)) != source.sha256
            or _digest(_original_git(owner.repo, "show", owner.run.base_sha + ":" + source.path)) != source.sha256
            or _digest(_original_git(owner.repo, "show", "HEAD:" + source.path)) != source.sha256
            or _digest(_original_local(Path(sys.executable).resolve(), 64 * 1024 * 1024)) != source.interpreter_sha256):
        _deny("ORIGINAL_NATIVE_SOURCE_CHANGED")


def _g3_run_admission(owner, profile=None):
    from .verification import (_ApplicabilityReader, _original_remote, _app_target_refs,
                               _original_local, _original_decode, _scheduler_namespace)
    from .review_transport import public_run
    from .verification_contract import ApplicabilityTargetRequest
    reader, run = _ApplicabilityReader(owner.repo), owner.run
    raw = _original_local(owner.run_path.absolute(), 2 * 1024 * 1024)
    refs = dict(_app_target_refs(owner.repo, _original_remote(owner.repo), run.run_id))
    review_ref, artifacts_ref = ("refs/heads/aios/" + kind + "/" + run.run_id for kind in ("review", "artifacts"))
    if set(refs) != {review_ref, artifacts_ref}:
        _deny("ORIGINAL_G3_RUN_TRANSPORT_REQUIRED")
    tree, parents = reader.commit(refs[artifacts_ref])
    transport = tree
    for part in (".ai", "transport"):
        mode, transport = reader.tree(transport).get(part, (None, None))
        if mode != b"40000":
            _deny("G3_ORIGINAL_ADMISSION_TRANSPORT_SCHEMA")
    names = set(reader.tree(transport))
    if "run.json" not in names or names - {"run.json", "execution-profile.json"}:
        _deny("G3_TERMINAL_OR_CORRECTION_CANNOT_IMPERSONATE_ADMISSION")
    canonical = reader.blob(tree, ".ai/transport/run.json")
    if parents or _original_decode(canonical) not in (_original_decode(raw), _original_decode(public_run(raw))):
        _deny("G3_CURRENT_RUN_ARTIFACT_CONFLICT")
    profile_sha256 = None
    if "execution-profile.json" in names:
        profile_raw = reader.blob(tree, ".ai/transport/execution-profile.json")
        if profile is not None and _bytes(_original_decode(profile_raw)) != _bytes(profile.as_dict()):
            _deny("G3_ORIGINAL_TRANSPORT_PROFILE_CONFLICT")
        profile_sha256 = _digest(profile_raw)
    reader.anchors(refs[review_ref], {run.base_sha})
    target = ApplicabilityTargetRequest(run.task.id, run.task.revision, run.run_id, run.base_sha,
        refs[review_ref], reader.commit(refs[review_ref])[0], refs[artifacts_ref], _digest(raw))
    namespace = _scheduler_namespace(owner.repo, _original_remote(owner.repo), target)
    return dict(refs=refs, namespace=namespace, canonical_run_sha256=_digest(canonical),
                artifacts_sha=refs[artifacts_ref], admitted_head=refs[review_ref], execution_profile_sha256=profile_sha256)


def production_gate(request):
    """No G3/G6 activation reader exists in this prospective implementation."""
    if request is not None:
        _deny("NOT_ACTIVATED")


def _authority(owner, request, operation, profile, policy, *, native_work=False):
    from .github_issue_ingress import _decode_policy, _authenticate_selection_issue, _github_selection_json
    from .verification import _ApplicabilityReader, _original_remote, _original_git, _original_local, _app_refs
    from .operator import _ka01_operation_source
    from .execution_profile import ResolvedExecutionProfile
    from .runtime import CompletionPolicy

    if type(operation) is not str or operation not in OPERATIONS or type(policy) is not CompletionPolicy or policy.kind != operation:
        _deny("OPERATION_NOT_ADMITTED")
    if type(profile) is not ResolvedExecutionProfile:
        _deny("EXACT_SELECTED_PROFILE_REQUIRED")
    profile.__post_init__()
    if (profile.run_id, profile.executor) != (owner.run.run_id, owner.run.executor):
        _deny("PROFILE_RUN_MISMATCH")
    url = _original_remote(owner.repo)
    # This nondefault interface accepts a real controlled local bare remote.
    # It cannot turn an HTTP/SSH production source into a simulated live permit.
    remote = Path(url)
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
        if os.path.lexists(packed):
            prefix = (selected_ref + "/").encode("ascii")
            for line in _original_local(packed, 32768).splitlines():
                if not line.startswith((b"#", b"^")) and line.partition(b" ")[2].startswith(prefix):
                    _deny("EXACT_UNAMBIGUOUS_FIRST_PARTY_SELECTION_REQUIRED")
        if set(refs) != {"refs/heads/main", selected_ref}:
            _deny("EXACT_UNAMBIGUOUS_FIRST_PARTY_SELECTION_REQUIRED")
        return refs

    refs = selected_refs()
    selection_tree, parents = reader.commit(refs[selected_ref])
    if parents or reader.blob(selection_tree, ".ai/transport/ka01-v13.json") != _bytes(asdict(request)):
        _deny("ORIGINAL_FIRST_PARTY_SELECTION_MISMATCH")
    main = refs["refs/heads/main"]
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

    g3 = type(request) is G3AdmissionRequest
    implementation = G3_IMPLEMENTATION if g3 else IMPLEMENTATION
    selection, selected_source = human(request.selection_issue, request.selection_sha256)
    fields = ("format", "version", "implementation", "ka01_version", "profile", "policy",
              "task", "operations", "normative", "overrides", "remote_url")
    _exact(selection, (*fields, "g3") if g3 else fields)
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
    if normative["path"] != NORMATIVE_PATH or normative["sha256"] != NORMATIVE_SHA256:
        _deny("EXACT_PUBLISHED_NORM_REQUIRED")
    source_tree = reader.anchors(main, {normative["source_sha"]})[normative["source_sha"]]
    norm = reader.blob(source_tree, NORMATIVE_PATH)
    if _digest(norm) != NORMATIVE_SHA256 or reader.blob(tree, NORMATIVE_PATH) != norm:
        _deny("NORMATIVE_SOURCE_CHANGED")
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
    source = _ka01_operation_source(owner, operation, policy, native_work=native_work)
    if g3:
        if (profile.model_source != "EXPLICIT" or profile.effort_source != "EXPLICIT"
                or profile.executor != "codex" or profile.model != "offline-native/" + request.native.sha256
                or profile.reasoning_effort != "none"
                or _bytes(selection["g3"]) != _bytes(dict(semantic=asdict(request.semantic), native=asdict(request.native)))):
            _deny("EXPLICIT_G3_SOURCE_AND_PROFILE_SELECTION_REQUIRED")
        readiness, semantic_facts = _g3_semantic(owner, request)
        _g3_native_source(owner, request.native)
        source = {**source, "g3": dict(semantic=semantic_facts, admission=_g3_run_admission(owner, profile),
                                      native=asdict(request.native))}
    consent, consent_source = human(request.consent_issue, request.consent_sha256)
    expected = dict(format="AIOS_KA01_V13_RUN_CONSENT", version=1,
        mode="CONTROLLED_PREACTIVATION", implementation=implementation, ka01_version=VERSION,
        selection_sha256=request.selection_sha256, task=task, run_id=owner.run.run_id,
        admitted_run_sha256=source["admitted_run_sha256"], operation=operation,
        executor=owner.run.executor, execution_profile_sha256=_digest(profile.as_dict()),
        operation_source_sha256=_digest(source), effective_limits_sha256=_digest(limits))
    if _bytes(consent) != _bytes(expected):
        _deny("EXACT_FRESH_RUN_CONSENT_REQUIRED")
    if selected_refs() != refs:
        _deny("SOURCE_MOVED_DURING_ADMISSION")
    return dict(implementation=implementation, ka01_version=VERSION, profile=PROFILE,
        policy=selection["policy"], normative=normative, ingress_policy_sha256=_digest(ingress_bytes),
        selection=selected_source, overrides=override_sources, consent=consent_source,
        original_selector=dict(ref=selected_ref, sha=refs[selected_ref]),
        effective_limits=limits, operation=operation, execution_profile=profile.as_dict(), source=source,
        production_activation="NOT_ACTIVATED", continuation="NOT_AUTHORIZED")


class _AdmissionBudget:
    """One original Runtime's private capability; no disk-to-live constructor."""

    def __init__(self, owner, request, operation, profile, policy, leases, lease):
        from .executor import _lease_matches_run
        from .run import RunLease, RunLeaseRegistry
        from .run_observation import RunObservationTracker
        from .runtime import _checkpoint_write_once, _checkpoint_make_directory
        if type(request) not in (AdmissionRequest, G3AdmissionRequest):
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
        self.g3 = type(request) is G3AdmissionRequest
        self.profile, self.policy, self.leases, self.lease = profile, policy, leases, lease
        self.thread, self.boundary, self.frame = get_ident(), None, None
        self.native_process = None
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
            accounted_until_ns=self.accounted_until_ns, pending=self.pending, effects=self.effects)

    def stop(self):
        self.stopped = True
        self.owner._local_feedback_stopped = True

    def _clock(self):
        now, real = time.monotonic_ns(), time.time_ns()
        if (type(now) is not int or type(real) is not int or now < self.last_ns
                or abs((real - self.started_real_ns) - (now - self.started_ns)) > 5_000_000_000):
            _deny("CLOCK_CONTINUITY_UNKNOWN")
        self.last_ns = now
        if now - self.started_ns >= self.wall_limit_ns:
            _deny("RUN_WALL_EXHAUSTED")
        return now

    def guard(self, *, live=True, native_work=False):
        from .runtime import _checkpoint_safe_directory
        from .verification import _original_local, MAX_ORIGINAL_PROOF_BYTES
        # Duplex waits also perform Runtime source authentication. Charge that
        # known work even outside a checkpoint method, without charging native
        # code/idle time as verification or double charging an active effect.
        auth_started = time.monotonic_ns() if self.g3 and self.work_started is None else None
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
            if native_work and not self.g3:
                _deny("G3_NATIVE_WORK_NOT_SELECTED")
            if _bytes(_authority(self.owner, self.request, self.operation, self.profile, self.policy,
                                  native_work=native_work)) != self.authority:
                _deny("ORIGINAL_AUTHORITY_WITHDRAWN_OR_CHANGED")
            if live:
                frame, boundary = self.frame, self.boundary
                if (frame is None or boundary._live is not frame or not frame.executing or frame.stopped
                        or frame.thread != get_ident() or frame.owner is not self.owner
                        or frame.lease is not self.lease or frame.profile != self.profile
                        or boundary._execution_profile != self.profile
                        or getattr(frame.adapter, "execution_profile", None) != frame.profile
                        or getattr(frame.adapter, "confirm_local_feedback", None) != frame.callback):
                    _deny("ORIGINAL_INVOCATION_LOST")
                if self.g3 and (self.native_process is None or self.native_process.poll() is not None
                        or frame.adapter._g3_process is not self.native_process):
                    _deny("ORIGINAL_NATIVE_PROCESS_LOST")
            self._clock()
        except BaseException:
            self.stop()
            raise
        finally:
            if auth_started is not None:
                try:
                    now = self._clock()
                    if now < auth_started:
                        _deny("AUTHENTICATION_ACCOUNTING_UNKNOWN")
                    self.verification_spent_ns += now - auth_started
                    self.accounted_until_ns = now
                    self._facts_seal = _bytes(self._facts())
                    if self.verification_spent_ns >= self.verification_limit_ns:
                        _deny("RUNTIME_VERIFICATION_EXHAUSTED")
                except BaseException:
                    self.stop()
                    raise

    def bind(self, boundary, frame):
        self.guard(live=False)
        if (self.frame is not None or frame.lease is not self.lease or boundary._leases is not self.leases
                or frame.profile != self.profile or boundary._execution_profile != self.profile):
            self.stop()
            _deny("REPLACEMENT_INVOCATION")
        if self.operation != "PRIMARY" and not self.g3:
            self.stop()
            _deny("CORRECTION_NATIVE_CONTINUITY_BLOCKED")
        if self.g3:
            from .codex_adapter import CodexAdapter
            if (type(frame.adapter) is not CodexAdapter or frame.adapter.g3_native_source != self.request.native
                    or frame.operation != self.operation or not callable(frame.callback)):
                self.stop()
                _deny("SUPPORTED_G3_NATIVE_TRANSPORT_REQUIRED")
        self.boundary, self.frame = boundary, frame

    def bind_native(self, process):
        if (not self.g3 or self.native_process is not None or self.frame is None
                or self.frame.adapter._g3_process is not process or process.poll() is not None):
            self.stop()
            _deny("ORIGINAL_NATIVE_PROCESS_REQUIRED")
        self.native_process = process
        self.guard()

    def native_cleanup(self, started):
        """Known cleanup cost is monotonic even on STOP or interrupted effects."""
        try:
            now = self._clock()
            spent = now - started
            if spent < 0:
                _deny("NATIVE_CLEANUP_ACCOUNTING_UNKNOWN")
            self.cleanup_spent_ns += spent
            self.verification_spent_ns += spent
            self.accounted_until_ns = now
            self._facts_seal = _bytes(self._facts())
            if self.verification_spent_ns >= self.verification_limit_ns:
                _deny("RUNTIME_VERIFICATION_EXHAUSTED")
        finally:
            self.stop()

    def g3_population(self, expected=None):
        """Authenticate and project the one original reviewed population.

        Only candidate_sha is relative to the current committed descendant.
        Commands, manifests, claims and every distinguishing condition remain
        exactly as independently reviewed. This is not another scheduler.
        """
        from .verification import (_ApplicabilityReader, _original_git, _reuse_scope,
                                   _original_local, _original_remote)
        from .verification_contract import (ApplicabilityTargetRequest, ApplicabilityDeclarationPin,
            ProofSchedulerRequest, _original_json, _scheduler_coverage)
        from .proof_coverage_contract import validate_proof_mapping, validate_proof_contract, proof_mapping_digest
        if not self.g3:
            _deny("G3_NOT_SELECTED")
        self.guard()
        owner = self.owner
        readiness, semantic = _g3_semantic(owner, self.request)
        original = json.loads(self.authority)["source"]["g3"]
        if semantic != original["semantic"] or _bytes(_g3_run_admission(owner, self.profile)) != _bytes(original["admission"]):
            _deny("ORIGINAL_G3_AUTHORITY_CHANGED")
        reader = _ApplicabilityReader(owner.repo)
        head = _original_git(owner.repo, "rev-parse", "HEAD").decode("ascii").strip()
        tree, _ = reader.commit(head)
        lineage = reader.anchors(head, {owner.run.base_sha, original["admission"]["admitted_head"]})
        scope = self.policy.mutation_scope if self.operation == "REPAIR" else self.policy.result_scope
        mutation_base = self.policy.mutation_base_sha if self.operation == "REPAIR" else self.policy.result_base_sha
        _reuse_scope(reader, reader.commit(mutation_base)[0], tree, tuple(scope))
        _reuse_scope(reader, lineage[owner.run.base_sha], tree, owner.task.scope.modify)
        # A no-change action cannot acquire mutation permission from this seam.
        action = self.policy.mutation_action
        if self.operation != "PRIMARY" and action not in ("CODE_FIX", "CONTINUE_IMPLEMENTATION"):
            _deny("G3_MUTATING_CORRECTION_ACTION_NOT_AUTHORIZED")
        coverage_data = _scheduler_coverage(readiness.authority.source_binding.intake.coverage)
        contract, mapping = coverage_data["contract"], coverage_data["mapping"]
        for value in (*contract["obligations"], *mapping["proofs"]):
            value["conditions"]["candidate_sha"] = head
        parsed = validate_proof_contract(contract, expected_task=contract["task"])
        mapping["contract_digest"] = parsed.digest
        obligations = {o.id: o for o in parsed.obligations}
        for entry in mapping["entries"]:
            entry["obligation_digest"] = obligations[entry["obligation_id"]].digest
        contract_pin = ApplicabilityDeclarationPin(parsed.id, parsed.revision, parsed.digest)
        mapping_pin = ApplicabilityDeclarationPin(mapping["id"], mapping["revision"], proof_mapping_digest(mapping))
        coverage = validate_proof_mapping(contract, mapping, expected_task=contract["task"],
                                          expected_mapping=asdict(mapping_pin))
        target = ApplicabilityTargetRequest(owner.task.task_id, owner.task.revision, owner.run.run_id,
            owner.run.base_sha, head, tree, original["admission"]["artifacts_sha"],
            _digest(_original_local(owner.run_path.absolute(), 2 * 1024 * 1024)))
        request = ProofSchedulerRequest(2, target, self.request.semantic.main_sha, contract_pin, mapping_pin,
            self.operation, head, () if expected is None else expected.sources)
        if expected is not None and request != expected:
            _deny("EXACT_ORIGINAL_G3_POPULATION_REQUEST_REQUIRED")
        authority = readiness.authority
        reviewed = authority.lineage
        sources = {s.role: {**asdict(s.locator), "sha256": s.content_sha256}
                   for s in (*authority.source_binding.sources, reviewed.review_task_source)}
        sources["review_task"] = sources.pop("mapping_review_task")
        pins = self.request.semantic
        binding = dict(upstream_sha256=_digest(_original_remote(owner.repo).encode("utf-8")),
            human_event_identity=authority.human_selection.event_identity, review_id=reviewed.review_id,
            review_run_id=reviewed.run_id, run_sha256=reviewed.run_sha256,
            result_sha256=reviewed.result_sha256, review_sha256=reviewed.review_sha256,
            scope_sha256=_digest(asdict(reviewed.scope)), sources=sources)
        result_authority = dict(main_sha=pins.main_sha, selection_sha=authority.human_selection.selection_sha,
            review_candidate_sha=reviewed.reviewed_sha, artifacts_sha=reviewed.artifacts_sha,
            decision_sha=reviewed.decision_sha, binding=binding,
            target=dict(task_blob_sha=pins.task.blob_sha, task_sha256=sources["task"]["sha256"],
                canonical_run_sha256=original["admission"]["canonical_run_sha256"],
                lineage_sha256=_digest([[sha, *reader.commit(sha)] for sha in sorted(lineage)]),
                condition_proof_sha256=None),
            g3=dict(operation=self.operation, original_source_sha256=_digest(json.loads(self.authority)["source"]),
                    admission_sha256=_digest(self.admission), semantic_sha256=semantic["source_sha256"]))
        self.guard()
        return request, coverage, _original_json(result_authority)

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
        self.guard()
        self.capacity(intent)
        key = _digest(intent)
        replay = key in self.effects
        kind = intent["kind"]
        checkpoints = int(kind == "CHECKPOINT" and not replay)
        corrections = int(kind == "LOCAL_FEEDBACK" and not replay)
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
                self.guard()
            else:
                from .verification import _original_local, MAX_ORIGINAL_PROOF_BYTES
                raw = _original_local(self.owner.state.results / (self.owner.run.run_id + ".json"), MAX_ORIGINAL_PROOF_BYTES)
                if kind != "TERMINAL" or _digest(raw) != self.terminal_digest:
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
        checkpoints = int(kind == "CHECKPOINT" and not replay)
        corrections = int(kind == "LOCAL_FEEDBACK" and not replay)
        if kind == "FACTUAL_FEEDBACK" and not replay and self.corrections_reserved >= min(
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
            budget.guard()
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
        except BaseException:
            budget.stop()
            raise
        finally:
            if entered:
                budget.work_started = None
    return call
