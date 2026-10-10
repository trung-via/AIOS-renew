"""Prospective, subordinate owner observations; never a canonical proof source.

Only the existing owner call sites can capture observations. Public consumers
accept the original owner object, not a root, record, issuer or callback. Private
call-site seals are an operational boundary, not a Python adversary sandbox.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import weakref
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path


SCHEMA = "owner-bound-provenance-v1"


class State(str, Enum):
    OBSERVED = "OBSERVED"
    UNKNOWN = "UNKNOWN"
    BLOCK = "BLOCK"


class Gap(str, Enum):
    OWNER_UNAVAILABLE = "OWNER_UNAVAILABLE"
    UNSUPPORTED_OWNER = "UNSUPPORTED_OWNER"
    UNSUPPORTED_PROVENANCE = "UNSUPPORTED_PROVENANCE"
    CAPTURE_UNAVAILABLE = "CAPTURE_UNAVAILABLE"
    OWNER_BINDING_CHANGED = "OWNER_BINDING_CHANGED"
    TASK_BINDING_MISMATCH = "TASK_BINDING_MISMATCH"
    TASK_AUTHORIZATION_UNAVAILABLE = "TASK_AUTHORIZATION_UNAVAILABLE"
    RUN_BINDING_MISMATCH = "RUN_BINDING_MISMATCH"
    PROFILE_UNAVAILABLE = "PROFILE_UNAVAILABLE"
    PROFILE_BINDING_MISMATCH = "PROFILE_BINDING_MISMATCH"
    LEASE_UNAVAILABLE = "LEASE_UNAVAILABLE"
    LEASE_BINDING_MISMATCH = "LEASE_BINDING_MISMATCH"
    CONTINUATION_UNAVAILABLE = "CONTINUATION_UNAVAILABLE"
    MAIN_UNAVAILABLE = "MAIN_UNAVAILABLE"
    MAIN_CHANGED = "MAIN_CHANGED"
    DETACHED_SUBJECT = "DETACHED_SUBJECT"
    REVIEW_BINDING_MISMATCH = "REVIEW_BINDING_MISMATCH"
    REPLAY_BINDING_UNAVAILABLE = "REPLAY_BINDING_UNAVAILABLE"
    REVIEW_UNAVAILABLE = "REVIEW_UNAVAILABLE"
    CARRIER_ORIGIN_UNAVAILABLE = "CARRIER_ORIGIN_UNAVAILABLE"
    INDEPENDENT_ISSUER_UNAVAILABLE = "INDEPENDENT_ISSUER_UNAVAILABLE"
    INDEPENDENT_CURRENTNESS_UNAVAILABLE = "INDEPENDENT_CURRENTNESS_UNAVAILABLE"
    TERMINAL_SOURCE_UNAVAILABLE = "TERMINAL_SOURCE_UNAVAILABLE"
    RAW_UNAVAILABLE = "RAW_UNAVAILABLE"


@dataclass(frozen=True)
class OwnerFacts:
    schema: str = SCHEMA
    kind: str = "UNAVAILABLE"
    status: State = State.UNKNOWN
    owner_origin: str = "UNKNOWN"
    bindings: tuple[tuple[str, str | int], ...] = ()
    gaps: tuple[Gap, ...] = ()
    # These are intentionally invariant, even for an observed admission.
    source_state: State = State.UNKNOWN
    source_gaps: tuple[Gap, ...] = (
        Gap.INDEPENDENT_ISSUER_UNAVAILABLE, Gap.INDEPENDENT_CURRENTNESS_UNAVAILABLE,
        Gap.TERMINAL_SOURCE_UNAVAILABLE, Gap.RAW_UNAVAILABLE,
    )
    issuer_authenticated: bool = False
    reviewer_authority: bool = False
    raw_state: str = "RAW_UNAVAILABLE"


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_metadata(path: Path) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(1_048_577)
    if len(content) > 1_048_576:
        raise ValueError("owner metadata bound exceeded")
    return content


def _encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _metadata_json(content: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate owner metadata")
            result[key] = value
        return result
    return json.loads(content, object_pairs_hook=unique)


def _sha(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _authorization(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is not None


def _bounded(value: object) -> bool:
    return type(value) is str and 0 < len(value) <= 256 and value.isprintable()


def _facts(kind: str, bindings: dict, gaps: list[Gap], *, block: bool = False) -> OwnerFacts:
    safe = {}
    gaps = list(gaps)
    for key, value in bindings.items():
        if (type(key) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,95}", key)
                and (_bounded(value) or type(value) is int and 0 < value <= 2**63 - 1)):
            safe[key] = value
        else:
            gaps.append(Gap.UNSUPPORTED_PROVENANCE)
            block = True
    return OwnerFacts(
        kind=kind, status=State.BLOCK if block else State.UNKNOWN if gaps else State.OBSERVED,
        owner_origin="OWNER_BOUND", bindings=tuple(sorted(safe.items())),
        gaps=tuple(dict.fromkeys(gaps)),
    )


def _unknown(gap: Gap) -> OwnerFacts:
    return OwnerFacts(gaps=(gap,))


def _owner_frame(module: str, names: tuple[str, ...]):
    """Recognize the actual integrated code, not a matching name or fixture."""
    frame = sys._getframe(2)
    owner_module = sys.modules.get(f"aios_renew.{module}")
    if owner_module is None or frame.f_globals is not vars(owner_module):
        return None
    for name in names:
        function = getattr(owner_module, name, None)
        if function is not None and frame.f_code is getattr(function, "__code__", None):
            return frame
    return None


# The seal is closure-held and absent from the public fact constructor. No
# enrollment, registry, persistence, key minting or selectable issuer exists.
def _channels():
    seal = object()

    @dataclass(frozen=True)
    class Snapshot:
        owner: weakref.ReferenceType
        token: object
        facts: OwnerFacts
        checks: tuple
        objects: tuple = ()

    def retain(owner, facts, checks=(), objects=()):
        previous = getattr(owner, "_owner_provenance_snapshot", None)
        if previous is not None:
            # A repeated capture cannot replace the first identity.
            return
        object.__setattr__(owner, "_owner_provenance_snapshot",
                           Snapshot(weakref.ref(owner), seal, facts, tuple(checks), tuple(objects)))

    def capture_operator(completion):
        frame = _owner_frame("operator", (
            "_run_task_impl", "_run_repair_impl", "_run_remediation_impl",
            "_accept_candidate_impl", "_recover_primary_impl", "recover_publication_source",
        ))
        if frame is None:
            return  # No externally callable capture helper can enroll an owner.
        local = frame.f_locals
        if local.get("runtime_completion", local.get("completion")) is not completion:
            return
        attempt = local.get("attempt")
        if attempt is not None and attempt.completion is not completion:
            return
        stage = Gap.CAPTURE_UNAVAILABLE
        bindings, gaps, checks, objects = {}, [], [], []
        kind = "UNAVAILABLE"
        try:
            from dataclasses import asdict
            from . import operator as op
            from .task import parse_task

            task, run = local["task"], local["run"]
            if completion.task is not task or completion.run is not run:
                retain(completion, _unknown(Gap.OWNER_BINDING_CHANGED))
                return
            name = frame.f_code.co_name
            kind = {"_run_task_impl": "PRIMARY", "_run_repair_impl": "REPAIR",
                    "_run_remediation_impl": "REMEDIATION", "_accept_candidate_impl": "DIRECT_CANDIDATE",
                    "_recover_primary_impl": "PRIMARY_RECOVERY", "recover_publication_source": "PUBLICATION_RECOVERY"}[name]
            admission = local.get("admission", {})
            control = completion.transport_repo
            git = op._git

            def file_pin(path, expected=None):
                content = _read_metadata(Path(path))
                if expected is not None and _metadata_json(content) != expected:
                    raise ValueError("owner metadata mismatch")
                checks.append(("file", Path(path), _digest(content)))
                return content

            bindings.update(task_id=task.task_id, task_revision=task.revision,
                            run_id=run.run_id, base_sha=run.base_sha, executor=run.executor,
                            operation=kind)
            if run.task.id != task.task_id or run.task.revision != task.revision or not _sha(run.base_sha):
                gaps.append(Gap.RUN_BINDING_MISMATCH)
            stage = Gap.RUN_BINDING_MISMATCH
            run_bytes = file_pin(completion.run_path)
            run_data = _metadata_json(run_bytes)
            persisted_run = run_data.get("execution", {}).get("run") if run_data.get("kind") == "REMEDIATION" else run_data
            if persisted_run != asdict(run):
                gaps.append(Gap.RUN_BINDING_MISMATCH)
            bindings["run_sha256"] = _digest(run_bytes)
            task_path = f".ai/tasks/{task.task_id}.yaml"
            # Exact admitted base content; Git is content binding, never origin.
            stage = Gap.TASK_BINDING_MISMATCH
            blob = git(completion.repo, "rev-parse", f"{run.base_sha}:{task_path}")
            if int(git(completion.repo, "cat-file", "-s", blob)) > 1_048_576:
                raise ValueError("TASK metadata bound exceeded")
            task_text = git(completion.repo, "show", f"{run.base_sha}:{task_path}", strip_stdout=False)
            if not _sha(blob) or parse_task(task_text) != task:
                gaps.append(Gap.TASK_BINDING_MISMATCH)
            bindings["task_blob_sha"] = blob
            requested_blob = admission.get("task_blob_sha")
            if requested_blob is not None and requested_blob != blob:
                gaps.append(Gap.TASK_BINDING_MISMATCH)
            commit = admission.get("task_commit_sha")
            if kind == "PRIMARY" and _sha(commit) and requested_blob == blob:
                bindings["task_authoring_commit_sha"] = commit
            else:
                gaps.append(Gap.TASK_AUTHORIZATION_UNAVAILABLE)

            stage = Gap.MAIN_UNAVAILABLE
            main = git(control, "rev-parse", "refs/heads/main")
            if not _sha(main):
                gaps.append(Gap.MAIN_UNAVAILABLE)
            else:
                bindings["main_snapshot_sha"] = main
                checks.append(("ref", control, "refs/heads/main", main))
            admission_main = admission.get("current_head_sha") if kind == "PRIMARY" else None
            if _sha(admission_main) and admission_main == run.base_sha:
                bindings["admission_main_sha"] = admission_main
                bindings["currentness_scope"] = "AT_ADMISSION"
                if local.get("synchronize") is not True:
                    gaps.append(Gap.INDEPENDENT_CURRENTNESS_UNAVAILABLE)
            else:
                gaps.append(Gap.INDEPENDENT_CURRENTNESS_UNAVAILABLE)
            candidate = git(completion.repo, "rev-parse", "HEAD")
            if not _sha(candidate):
                gaps.append(Gap.RUN_BINDING_MISMATCH)
            else:
                bindings["candidate_snapshot_sha"] = candidate
                checks.append(("ref", completion.repo, "HEAD", candidate))
            # Historical correction worktrees are deliberately detached.
            if kind in ("PRIMARY", "DIRECT_CANDIDATE"):
                stage = Gap.DETACHED_SUBJECT
                branch = git(completion.repo, "symbolic-ref", "--quiet", "HEAD")
                if kind == "PRIMARY" and branch != "refs/heads/main":
                    gaps.append(Gap.MAIN_CHANGED)
                checks.append(("branch", completion.repo, branch))

            profile = local.get("execution_profile")
            if profile is None:
                gaps.append(Gap.PROFILE_UNAVAILABLE)
            else:
                stage = Gap.PROFILE_BINDING_MISMATCH
                profile_data = profile.as_dict()
                if profile.run_id != run.run_id or profile.executor != run.executor:
                    gaps.append(Gap.PROFILE_BINDING_MISMATCH)
                profile_dir = completion.state.execution_profiles or (completion.state.root / "execution-profiles")
                file_pin(profile_dir / f"{run.run_id}.json", profile_data)
                bindings["profile_sha256"] = _digest(_encoded(profile_data))
                for field in ("model", "reasoning_effort", "model_source", "effort_source"):
                    if _bounded(profile_data[field]):
                        bindings[field] = profile_data[field]

            if kind == "PRIMARY":
                leases, lease = local.get("leases"), local.get("lease")
                if leases is None or lease is None:
                    gaps.append(Gap.LEASE_UNAVAILABLE)
                else:
                    from .executor import _lease_matches_run
                    if leases.holder(task.task_id) is not lease or not _lease_matches_run(lease, run) or lease.return_affinity != run.return_affinity:
                        gaps.append(Gap.LEASE_BINDING_MISMATCH)
                    else:
                        bindings["guard"] = "PRIMARY_OWNER_LEASE"
                        objects.append(("lease", leases, lease, run))
            elif kind == "REPAIR":
                bindings["guard"] = "REPAIR_ADMISSION"
                for field in ("failed_run_id", "failed_head", "root_base_sha", "result_base_sha", "action"):
                    value = local.get(field)
                    if _bounded(value):
                        bindings[field] = value
                authorization = local.get("repair_authorization_sha")
                if _authorization(authorization):
                    bindings["repair_authorization_sha"] = authorization
                else:
                    gaps.append(Gap.CONTINUATION_UNAVAILABLE)
                stage = Gap.CONTINUATION_UNAVAILABLE
                file_pin(completion.state.repairs / f"{run.run_id}.json")
                bindings["executor_invoked"] = "NO" if local.get("reusable_package") is not None else "YES"
            else:
                bindings["guard"] = kind + "_ADMISSION"
                authorization = local.get("remediation_authorization_sha")
                if _authorization(authorization):
                    bindings["remediation_authorization_sha"] = authorization
                else:
                    gaps.append(Gap.CONTINUATION_UNAVAILABLE)
                if run_data.get("kind") == "REMEDIATION":
                    bindings["continuation_sha256"] = _digest(_encoded({
                        key: run_data[key] for key in ("predecessor", "execution_base") if key in run_data}))
                    for key, value in run_data.get("predecessor", {}).items():
                        if _bounded(value):
                            bindings["predecessor_" + key] = value
            for key in ("dispatch_id", "repair_dispatch_id", "correction_dispatch_id", "source_run_id", "migration_handoff", "successor_transport"):
                value = local.get(key)
                if _bounded(value):
                    bindings[key] = value
            objects.append(("completion", completion.repo, completion.transport_repo,
                            task, run, completion.run_path, completion.state))
            blockers = {Gap.RUN_BINDING_MISMATCH, Gap.TASK_BINDING_MISMATCH,
                        Gap.PROFILE_BINDING_MISMATCH, Gap.LEASE_BINDING_MISMATCH,
                        Gap.DETACHED_SUBJECT, Gap.MAIN_CHANGED}
            retain(completion, _facts(kind, bindings, gaps, block=bool(blockers.intersection(gaps))), checks, objects)
        except Exception:
            # Metadata must never replace a canonical exception or success.
            retain(completion, _facts(kind, bindings, gaps + [stage], block=stage not in (
                Gap.CAPTURE_UNAVAILABLE, Gap.MAIN_UNAVAILABLE, Gap.CONTINUATION_UNAVAILABLE)))

    def capture_review(result):
        frame = _owner_frame("authoring_ingress", ("_execute_submit_review",))
        if frame is None:
            return
        local = frame.f_locals
        if local.get("ingress_result") is not result:
            return
        try:
            from . import authoring_ingress as ingress
            envelope, review, repo = local["envelope"], local["review"], local["repo"]
            run_id, candidate = local["run_id"], local["candidate_sha"]
            decision = result.canonical_sha
            if result.canonical_destination != local["decision_ref"] or not _sha(decision):
                retain(result, _unknown(Gap.REVIEW_BINDING_MISMATCH))
                return
            path = f".ai/reviews/{review.review_id}.yaml"
            expected = ingress._payload_to_str(envelope.payload).encode("utf-8")
            if len(expected) > 1_048_576:
                retain(result, _facts("SUBMIT_REVIEW", {}, [Gap.UNSUPPORTED_PROVENANCE], block=True))
                return
            raw = ingress._read_commit_blob(repo, decision, path)
            if raw != expected or candidate != review.reviewed_sha:
                retain(result, _facts("SUBMIT_REVIEW", {}, [Gap.REVIEW_BINDING_MISMATCH], block=True))
                return
            bindings = dict(run_id=run_id, candidate_sha=candidate, decision_sha=decision,
                            review_id=review.review_id, recorded_verdict=review.verdict,
                            review_sha256=_digest(raw), review_size=len(raw))
            gaps, checks = [Gap.CARRIER_ORIGIN_UNAVAILABLE], []
            # Replay's existing fast path does not re-admit RUN/artifact content.
            if "run" in local and "artifacts_sha" in local:
                run, task, artifact = local["run"], local["task"], local["artifacts_sha"]
                bindings.update(task_id=run.task.id, task_revision=run.task.revision, artifacts_sha=artifact)
                if task.task_id != run.task.id or task.revision != run.task.revision:
                    gaps.append(Gap.TASK_BINDING_MISMATCH)
                code, blob, _ = ingress._git(repo, "rev-parse", f"{candidate}:.ai/tasks/{task.task_id}.yaml")
                if code == 0 and _sha(blob.strip()):
                    bindings["task_blob_sha"] = blob.strip()
                else:
                    gaps.append(Gap.TASK_BINDING_MISMATCH)
                checks.append(("ref", repo, local["artifacts_ref"], artifact))
            else:
                gaps.append(Gap.REPLAY_BINDING_UNAVAILABLE)
            # Local ref checks are only applicable to local ingress. Remote
            # currentness needs the existing protected owner, never a new query.
            if local["remote"] is None:
                checks.extend((("ref", repo, local["candidate_ref"], candidate),
                               ("ref", repo, local["decision_ref"], decision)))
            else:
                checks = []
                gaps.append(Gap.INDEPENDENT_CURRENTNESS_UNAVAILABLE)
            retain(result, _facts("SUBMIT_REVIEW", bindings, gaps,
                                 block=Gap.TASK_BINDING_MISMATCH in gaps), checks,
                   (("ingress", result.as_dict()),))
        except Exception:
            retain(result, _unknown(Gap.CAPTURE_UNAVAILABLE))

    def capture_carrier(result):
        frame = _owner_frame("github_issue_ingress", ("deliver_event",))
        if frame is None or frame.f_locals.get("result") is not result:
            return
        snapshot = getattr(result, "_owner_provenance_snapshot", None)
        if type(snapshot) is not Snapshot or snapshot.token is not seal or snapshot.owner() is not result:
            return
        issue, policy = frame.f_locals["issue"], frame.f_locals["policy"]
        # Policy admission is observed; an arbitrary file/environment/CLI is
        # never authenticated as GitHub. No existing review-origin credential
        # is available here (AUTHOR_TASK origin proof is operation-specific).
        bindings = dict(snapshot.facts.bindings)
        bindings.update(carrier_policy_state="OWNER_OBSERVED", carrier_origin="UNKNOWN",
                        carrier_repository=issue.repository, carrier_actor_recorded=issue.actor,
                        carrier_issue_number=issue.number, carrier_body_sha256=_digest(issue.body_bytes),
                        carrier_policy_sha256=_digest(_encoded({
                            "repository": policy.repository, "authorized_actors": policy.authorized_actors,
                            "title_marker": policy.title_marker, "max_body_bytes": policy.max_body_bytes})))
        facts = _facts(snapshot.facts.kind, bindings, list(snapshot.facts.gaps),
                       block=snapshot.facts.status == State.BLOCK)
        object.__setattr__(result, "_owner_provenance_snapshot", replace(snapshot, facts=facts))

    def read(owner: object) -> OwnerFacts:
        """Read the original live owner only; no roots, records or callbacks."""
        from .runtime import RuntimeCompletion
        from .authoring_ingress import IngressResult
        if type(owner) not in (RuntimeCompletion, IngressResult):
            return _unknown(Gap.UNSUPPORTED_OWNER)
        snapshot = getattr(owner, "_owner_provenance_snapshot", None)
        if type(snapshot) is not Snapshot or snapshot.token is not seal or snapshot.owner() is not owner:
            return _unknown(Gap.OWNER_UNAVAILABLE)
        try:
            from .operator import _git
            for check in snapshot.checks:
                if check[0] == "file":
                    if _digest(_read_metadata(check[1])) != check[2]:
                        return replace(snapshot.facts, status=State.BLOCK, gaps=(Gap.OWNER_BINDING_CHANGED,))
                elif check[0] == "ref":
                    if _git(check[1], "rev-parse", check[2]) != check[3]:
                        gap = Gap.MAIN_CHANGED if check[2] == "refs/heads/main" else Gap.OWNER_BINDING_CHANGED
                        return replace(snapshot.facts, status=State.BLOCK, gaps=(gap,))
                elif check[0] == "branch":
                    try:
                        branch = _git(check[1], "symbolic-ref", "--quiet", "HEAD")
                    except Exception:
                        branch = None
                    if branch != check[2]:
                        gap = Gap.DETACHED_SUBJECT if branch is None else Gap.OWNER_BINDING_CHANGED
                        return replace(snapshot.facts, status=State.BLOCK, gaps=(gap,))
            for item in snapshot.objects:
                if item[0] == "lease":
                    _, leases, lease, run = item
                    from .executor import _lease_matches_run
                    if leases.holder(run.task.id) is not lease or not _lease_matches_run(lease, run):
                        return replace(snapshot.facts, status=State.BLOCK, gaps=(Gap.LEASE_BINDING_MISMATCH,))
                elif item[0] == "completion":
                    _, repo, transport, task, run, run_path, state = item
                    if (owner.repo != repo or owner.transport_repo != transport or owner.task is not task
                            or owner.run is not run or owner.run_path != run_path or owner.state is not state):
                        return replace(snapshot.facts, status=State.BLOCK, gaps=(Gap.OWNER_BINDING_CHANGED,))
                elif item[0] == "ingress" and owner.as_dict() != item[1]:
                    return replace(snapshot.facts, status=State.BLOCK, gaps=(Gap.REVIEW_BINDING_MISMATCH,))
            return snapshot.facts
        except Exception:
            return replace(snapshot.facts, status=State.BLOCK, gaps=(Gap.OWNER_BINDING_CHANGED,))

    return capture_operator, capture_review, capture_carrier, read


_observe_operator, _observe_review, _observe_carrier, read_owner_provenance = _channels()
del _channels


def join_owner_provenance(completion: object, review_ingress: object | None = None) -> OwnerFacts:
    """Read-only exact join; never issue a source or a Reviewer verdict."""
    from .runtime import RuntimeCompletion
    from .authoring_ingress import IngressResult
    if type(completion) is not RuntimeCompletion:
        return _unknown(Gap.UNSUPPORTED_OWNER)
    execution = read_owner_provenance(completion)
    if execution.owner_origin != "OWNER_BOUND" or execution.status == State.BLOCK:
        return execution
    residual = list(execution.gaps) + [Gap.INDEPENDENT_ISSUER_UNAVAILABLE,
        Gap.INDEPENDENT_CURRENTNESS_UNAVAILABLE, Gap.TERMINAL_SOURCE_UNAVAILABLE, Gap.RAW_UNAVAILABLE]
    if review_ingress is None:
        return replace(execution, status=State.UNKNOWN, gaps=tuple(dict.fromkeys(residual + [Gap.REVIEW_UNAVAILABLE])))
    if type(review_ingress) is not IngressResult:
        return replace(execution, status=State.BLOCK, gaps=(Gap.REVIEW_BINDING_MISMATCH,))
    decision = read_owner_provenance(review_ingress)
    if decision.owner_origin != "OWNER_BOUND" or decision.status == State.BLOCK:
        return replace(execution, status=State.BLOCK, gaps=(Gap.REVIEW_BINDING_MISMATCH,))
    left, right = dict(execution.bindings), dict(decision.bindings)
    keys = ("run_id", "task_id", "task_revision", "task_blob_sha")
    if any(key not in right for key in keys):
        return replace(execution, status=State.UNKNOWN, gaps=(Gap.REPLAY_BINDING_UNAVAILABLE,))
    if any(left.get(key) != right[key] for key in keys) or left.get("candidate_snapshot_sha") != right.get("candidate_sha"):
        return replace(execution, status=State.BLOCK, gaps=(Gap.REVIEW_BINDING_MISMATCH,))
    bindings = tuple(sorted({**left, **{"review_" + key: value for key, value in right.items()}}.items()))
    return replace(execution, status=State.UNKNOWN, bindings=bindings,
                   gaps=tuple(dict.fromkeys(residual + list(decision.gaps))))


def join_authenticated_provenance(completion: object, review_ingress: object, *, raw_paths=None):
    """Separate prospective service receipts from TASK-335's legacy observations."""
    from .runtime import RuntimeCompletion
    from .authoring_ingress import IngressResult
    from .runtime_provenance_issuer import SourceFacts, correlate_sources
    if type(completion) is not RuntimeCompletion or type(review_ingress) is not IngressResult:
        return SourceFacts(status="BLOCK", gaps=("UNSUPPORTED_SOURCE_OWNER",))
    return correlate_sources(completion, review_ingress, raw_paths=raw_paths)
