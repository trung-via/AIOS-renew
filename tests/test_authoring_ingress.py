"""Deterministic tests for generic Brain authoring ingress."""

import json
import copy
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import aios_renew.authoring_ingress as authoring_ingress_module
from tests.git_fixture_support import (
    commit_fixture_state,
    materialize_git_baseline,
    read_git_ref,
)

from aios_renew.authoring_ingress import (
    AuthoringIngressError,
    IngressEnvelope,
    IngressResult,
    ingest_carrier,
    parse_envelope,
    read_carrier_input,
    execute_ingress,
)
from aios_renew.publication import publish_review_decision
from aios_renew.review_transport import (
    ReviewTransportError,
    resolve_remote_repair_authorization,
    resolve_remote_task_lifecycle,
    transport_failure,
    transport_post_pass,
)
from aios_renew.unified_state import observe_unified_state


def new_task_envelope(main_sha: str) -> IngressEnvelope:
    return IngressEnvelope(
        "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_TASK", {"task_id": "TASK-105"},
        {"expected_main_sha": main_sha}, V1_TASK_105_SOURCE,
    )


def test_audited_authoring_missing_handoff_is_non_mutating(tmp_path):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    with pytest.raises(AuthoringIngressError, match="requires audited_handoff"):
        execute_ingress(new_task_envelope(main_sha), repo=repo)
    assert git(repo, "rev-parse", "HEAD") == main_sha
    assert git(remote, "rev-parse", "refs/heads/main") == main_sha
    assert not (repo / ".ai/tasks/TASK-105.yaml").exists()


def test_audited_authoring_semantic_formatting_and_read_only_replay(tmp_path):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    envelope = audited_envelope(new_task_envelope(main_sha), repo)
    body = yaml.safe_load(envelope.payload)
    envelope = replace(envelope, payload=json.dumps(dict(reversed(list(body.items()))), indent=3) + "\r\n")
    result = execute_ingress(envelope, repo=repo)
    assert result.status == "CANONICALIZED"
    assert set(git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", main_sha, result.canonical_sha).splitlines()) == {".ai/tasks/TASK-105.yaml"}
    replay = execute_ingress(replace(envelope, audited_handoff=None), repo=repo)
    assert replay.status == "IDEMPOTENT"
    assert git(remote, "rev-parse", "refs/heads/main") == result.canonical_sha
    with pytest.raises(AuthoringIngressError, match="conflicting TASK"):
        execute_ingress(replace(envelope, audited_handoff=None, payload=envelope.payload.replace("Implement generic", "Change generic")), repo=repo)
    revised = replace(envelope, audited_handoff=None,
                      expected_state={"expected_main_sha": result.canonical_sha},
                      payload=V1_TASK_105_R2_SOURCE)
    with pytest.raises(AuthoringIngressError, match="requires audited_handoff"):
        execute_ingress(revised, repo=repo)


@pytest.mark.parametrize("fault", [
    "stage1_only", "packet", "profile", "flow", "order", "missing_lens",
    "no_decision", "substitution", "reconciliation", "payload",
])
def test_audited_authoring_rejects_invalid_provenance_before_mutation(tmp_path, fault):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    envelope = audited_envelope(new_task_envelope(main_sha), repo)
    handoff = copy.deepcopy(envelope.audited_handoff)
    if fault == "stage1_only":
        del handoff["stage2"]
    elif fault == "packet":
        handoff["stage1"]["packet_fingerprint"] = "0" * 64
    elif fault == "profile":
        handoff["stage1"]["audit_profile_ref"]["id"] = "wrong-profile"
    elif fault == "flow":
        handoff["stage2"]["selected_flow"] = "ARCHITECTURE"
    elif fault == "order":
        handoff["stage2"]["construct_audit"].reverse()
    elif fault == "missing_lens":
        handoff["stage2"]["closure"].pop()
    elif fault == "no_decision":
        handoff["stage2"]["closure"][0].update(outcome="BLOCKER", blocker_summary="Material blocker.")
        handoff["stage2"]["outcome"] = "NO_DECISION"
    elif fault in {"substitution", "reconciliation"}:
        handoff["stage2"]["reconciled_candidate"]["goal"] = "A different semantic goal."
        if fault == "substitution":
            # A valid risk/reconciliation still cannot substitute the payload.
            handoff["stage2"]["construct_audit"][0] = {
                "lens": handoff["stage2"]["construct_audit"][0]["lens"], "outcome": "RISK_FOUND",
                "risks": [{"risk_summary": "Goal mismatch.", "counterexample": "Different goal.",
                           "candidate_anchor": "goal", "disposition": "ADDRESSED_BY_RECONCILIATION"}],
            }
    elif fault == "payload":
        envelope = replace(envelope, payload=envelope.payload.replace("Implement generic", "Change generic"))
    with pytest.raises(AuthoringIngressError):
        execute_ingress(replace(envelope, audited_handoff=handoff), repo=repo)
    assert git(remote, "rev-parse", "refs/heads/main") == main_sha
    assert not (repo / ".ai/tasks/TASK-105.yaml").exists()


@pytest.mark.parametrize("input_path", [".ai/roadmap-state.yaml", ".ai/flow-cards.yaml",
                                       ".ai/brain-audit-profiles.yaml", ".ai/brain-return-contracts.yaml",
                                       ".ai/tasks/TASK-OTHER.yaml"])
def test_audited_authoring_rejects_dirty_or_alternate_projection(tmp_path, input_path):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    envelope = audited_envelope(new_task_envelope(main_sha), repo)
    git(repo, "checkout", "-b", "alternate")
    path = repo / input_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((path.read_bytes() if path.exists() else b"") + b"\n# divergent input\n")
    with pytest.raises(AuthoringIngressError, match="divergent"):
        execute_ingress(envelope, repo=repo)
    assert git(remote, "rev-parse", "refs/heads/main") == main_sha


@pytest.mark.parametrize("movement", ["main", "correction", "input_at_publication"])
def test_audited_authoring_rechecks_movement_after_audit_and_at_publication(tmp_path, monkeypatch, movement):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    envelope = audited_envelope(new_task_envelope(main_sha), repo)
    if movement == "input_at_publication":
        original = authoring_ingress_module._commit_tree
        def move_input(*args, **kwargs):
            commit = original(*args, **kwargs)
            path = repo / ".ai/brain-return-contracts.yaml"
            path.write_bytes(path.read_bytes() + b"\n# moved after audit\n")
            return commit
        monkeypatch.setattr(authoring_ingress_module, "_commit_tree", move_input)
    else:
        original = authoring_ingress_module.validate_stage2
        def move_ref(*args, **kwargs):
            audit = original(*args, **kwargs)
            if movement == "main":
                tree = git(repo, "rev-parse", f"{main_sha}^{{tree}}")
                commit = authoring_ingress_module._commit_tree(repo, tree, [main_sha], "movement")
                git(remote, "fetch", "--no-tags", str(repo), commit)
                git(remote, "update-ref", "refs/heads/main", commit)
            else:
                git(remote, "update-ref", "refs/heads/aios/repair/RUN-OTHER", main_sha)
            return audit
        monkeypatch.setattr(authoring_ingress_module, "validate_stage2", move_ref)
    with pytest.raises(AuthoringIngressError, match="freshness"):
        execute_ingress(envelope, repo=repo)
    assert not (repo / ".ai/tasks/TASK-105.yaml").exists()
    if movement != "main":
        assert git(remote, "rev-parse", "refs/heads/main") == main_sha


def test_audited_handoff_has_one_closed_surface_and_excludes_reviewer():
    envelope = {
        "format": "AIOS_INGRESS_ENVELOPE", "version": 1, "operation": "AUTHOR_TASK",
        "identity": {"task_id": "TASK-105"}, "expected_state": {"expected_main_sha": "a" * 40},
        "payload": V1_TASK_105_SOURCE,
        "audited_handoff": {"format": "AIOS_AUDITED_AUTHORING_HANDOFF", "version": 1, "stage1": {}, "stage2": {}},
    }
    for key in ("decision_packet", "canonical_state", "provider", "model", "session"):
        invalid = copy.deepcopy(envelope)
        invalid["audited_handoff"][key] = {}
        with pytest.raises(AuthoringIngressError, match="two-stage"):
            parse_envelope(invalid)
    envelope.update(operation="SUBMIT_REVIEW", identity={"run_id": "RUN-105-001"})
    with pytest.raises(AuthoringIngressError, match="SUBMIT_REVIEW"):
        parse_envelope(envelope)


def test_repair_review_semantics_require_exact_remediation_finding() -> None:
    prior = SimpleNamespace()
    exact = SimpleNamespace(mode="DELTA", prior_finding_id="R-EXACT")
    authoring_ingress_module._validate_repair_review_semantics(
        exact,
        prior_review=prior,
        repaired_finding_id="R-EXACT",
    )

    for invalid in (
        SimpleNamespace(mode="PRIMARY", prior_finding_id=None),
        SimpleNamespace(mode="DELTA", prior_finding_id=None),
        SimpleNamespace(mode="DELTA", prior_finding_id="R-SIBLING"),
    ):
        with pytest.raises(AuthoringIngressError, match="exact repaired DELTA"):
            authoring_ingress_module._validate_repair_review_semantics(
                invalid,
                prior_review=prior,
                repaired_finding_id="R-EXACT",
            )


def git(repo: Path, *args: str, check: bool = True) -> str:
    if check and args == ("rev-parse", "HEAD"):
        return read_git_ref(repo)
    proc = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=check,
    )
    return proc.stdout.strip()


def assert_exact_metadata_delta(
    repo: Path,
    commit_sha: str,
    parent_sha: str,
    metadata_path: str,
    metadata_bytes: bytes,
) -> None:
    assert git(repo, "show", "-s", "--format=%P", commit_sha).split() == [parent_sha]
    assert git(
        repo,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        parent_sha,
        commit_sha,
    ).splitlines() == [metadata_path]
    entry = git(repo, "ls-tree", commit_sha, "--", metadata_path)
    assert entry.startswith("100644 blob ")
    assert entry.endswith(f"\t{metadata_path}")
    blob = subprocess.run(
        ("git", "-C", str(repo), "show", f"{commit_sha}:{metadata_path}"),
        capture_output=True,
        check=True,
    ).stdout
    assert blob == metadata_bytes


TASK_105_SOURCE = """\
task_id: TASK-105
revision: 1
goal: Implement generic ingress capability.
problem: Brain lacks generic transport-neutral authoring ingress.
assumptions:
  - Canonical main is established.
scope:
  inspect: []
  modify: [src/sample.py]
non_goals:
  - Arbitrary mutations.
constraints:
  hard:
    - Bounded mutation authority only.
acceptance:
  - id: AC1
    condition: Ingress envelope is validated.
verification:
  required:
    - git diff --check
"""

TASK_105_R2_SOURCE = """\
task_id: TASK-105
revision: 2
goal: Revise generic ingress capability.
problem: Second revision needed.
assumptions:
  - Canonical main is established.
scope:
  inspect: []
  modify: [src/sample.py]
non_goals:
  - Arbitrary mutations.
constraints:
  hard:
    - Bounded mutation authority only.
acceptance:
  - id: AC1
    condition: Ingress envelope is validated.
verification:
  required:
    - git diff --check
"""

V1_TASK_105_SOURCE = TASK_105_SOURCE.replace(
    "verification:\n", "verification:\n  policy: minimum-sufficient-v1\n"
)
V1_TASK_105_R2_SOURCE = TASK_105_R2_SOURCE.replace(
    "verification:\n", "verification:\n  policy: minimum-sufficient-v1\n"
)


def setup_test_repo(root: Path, *, task_id: str = "TASK-105") -> tuple[Path, Path, str]:
    """Create local repo and bare upstream git repo."""
    return materialize_git_baseline(
        root,
        files={
            "README.md": "initial repo\n",
            ".gitattributes": ".ai/** -text\n",
            ".ai/roadmap-state.yaml": yaml.safe_dump({
                "version": 1, "active_track": "ingress", "active_track_status": "ACTIVE",
                "sequence": [{"id": task_id, "task_id": task_id, "status": "NEXT"}],
            }).encode("utf-8"),
            **{f".ai/{name}": (Path(__file__).resolve().parents[1] / ".ai" / name).read_bytes()
               for name in ("flow-cards.yaml", "brain-audit-profiles.yaml", "brain-return-contracts.yaml")},
        },
        user_name="AIOS Test",
        user_email="test@example.invalid",
        commit_message="initial commit",
    )


def audited_envelope(envelope: IngressEnvelope, repo: Path) -> IngressEnvelope:
    """Fixture Brain supplies BP-4A semantic stages over the real fresh packet."""
    from aios_renew.brain_audit import construct_stage1
    packet, profile, _, _ = authoring_ingress_module._compose_authoring_packet(envelope, repo)
    candidate = yaml.safe_load(envelope.payload) if isinstance(envelope.payload, str) else dict(envelope.payload)
    stage1 = construct_stage1(packet, profile, candidate)
    stage2 = {
        **{key: stage1[key] for key in ("packet_fingerprint", "audit_profile_ref", "selected_flow",
                                       "construct_candidate", "construct_fingerprint")},
        "construct_audit": [{"lens": lens["id"], "outcome": "CLEAR"} for lens in profile["lenses"]],
        "reconciled_candidate": copy.deepcopy(candidate),
        "closure": [{"lens": lens["id"], "outcome": "CLEAR"} for lens in profile["lenses"]],
        "outcome": "CANDIDATE",
    }
    return replace(envelope, audited_handoff={
        "format": "AIOS_AUDITED_AUTHORING_HANDOFF", "version": 1,
        "stage1": stage1, "stage2": stage2,
    })


def execute_audited_ingress(envelope: IngressEnvelope, *, repo: Path) -> IngressResult:
    return execute_ingress(audited_envelope(envelope, repo), repo=repo)


def historical_repair_fixture(envelope: IngressEnvelope, *, repo: Path) -> IngressResult:
    """Seed pre-H1 lineage for historical family/replay tests.

    First prove that this direct prospective authoring is rejected. Existing
    immutable authorizations remain valid fixture inputs; the fixture does not
    mock or bypass the production gate to pretend a new decision was audited.
    """
    with pytest.raises(AuthoringIngressError, match="requires audited_handoff"):
        execute_ingress(replace(envelope, audited_handoff=None), repo=repo)
    payload = dict(envelope.payload)
    run_id = envelope.identity["failed_run_id"]
    remote = authoring_ingress_module._resolve_remote(repo)
    ref = f"refs/heads/aios/repair/{run_id}"
    first = authoring_ingress_module._resolve_ref_sha(repo, ref, remote)
    current = resolve_remote_repair_authorization(repo, run_id) if first else None
    parent = current.commit_sha if current else payload["failed_head_sha"]
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    tree = authoring_ingress_module._tree_with_metadata(
        repo, parent, ".ai/transport/repair.json", body, replace_existing=current is not None)
    if current:
        staged = authoring_ingress_module._commit_tree(repo, tree, [parent], "historical repair fixture stage")
        failure_sha = authoring_ingress_module._resolve_ref_sha(
            repo, f"refs/heads/aios/failure-artifacts/{run_id}", remote)
        metadata = {
            "format": "AIOS_REPAIR_SUPERSESSION", "version": 1, "failed_run_id": run_id,
            "authorization_revision": current.revision + 1, "predecessor_repair_sha": parent,
            "failure_artifacts_sha": failure_sha,
        }
        tree = authoring_ingress_module._tree_with_metadata(
            repo, staged, ".ai/transport/repair-supersession.json",
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8"), replace_existing=True)
        ref = f"refs/heads/aios/repair-supersession/{run_id}/{current.revision + 1}"
    commit = authoring_ingress_module._commit_tree(repo, tree, [parent], "historical pre-H1 repair fixture")
    authoring_ingress_module._publish_ingress_ref(repo, remote, ref, commit, expect_missing=True)
    return IngressResult(operation="AUTHOR_REPAIR", canonical_destination=ref, canonical_sha=commit)


def completion_gate_supersession_fixture(tmp_path):
    """RUN-254-001's r2 blocker shape, with isolated Git identities.

    The r2 candidate is clean/transportable and fails only at COMPLETION_GATE.
    It is transported as a failed candidate, never published on fixture main.
    """
    repo, remote, _ = setup_test_repo(tmp_path, task_id="TASK-254")
    task_source = TASK_105_R2_SOURCE.replace("TASK-105", "TASK-254")
    task_path = repo / ".ai/tasks/TASK-254.yaml"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(task_source, encoding="utf-8")
    sample = repo / "src/sample.py"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text("# base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "canonical r2 TASK fixture")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")
    git(repo, "switch", "--quiet", "-c", "failed-candidate")
    sample.write_text("# completed candidate\n", encoding="utf-8")
    git(repo, "add", "src/sample.py")
    git(repo, "commit", "--quiet", "-m", "r2 bounded candidate fixture")
    head = git(repo, "rev-parse", "HEAD")
    # Transport reads configured upstream authority on the candidate checkout.
    git(repo, "config", "branch.failed-candidate.remote", "origin")
    git(repo, "config", "branch.failed-candidate.merge", "refs/heads/main")
    run = {"run_id": "RUN-254-001", "task": {"id": "TASK-254", "revision": 2},
           "executor": "codex", "base_sha": base, "head_sha": head,
           "workspace": str(repo), "status": "ACTIVE"}
    failure = {"kind": "FAILURE", "run_id": run["run_id"], "task": run["task"],
               "executor": "codex", "base_sha": base, "failed_head_sha": head,
               "phase": "COMPLETION_GATE",
               "error": {"type": "OperatorError", "message": "RESULT has unresolved items",
                         "executor_diagnostics": {"unresolved": [
                             "Superseding REPAIR cannot obtain an audited packet while Unified State projects EXECUTE_REPAIR."]}},
               "candidate": {"transportable": True, "repairable": True, "dirty": False,
                             "descends_from_base": True, "changed_files": ["src/sample.py"],
                             "outside_task_scope": []}}
    state = tmp_path / "failure-material"
    state.mkdir()
    run_path, failure_path = state / "run.json", state / "failure.json"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    transport_failure(repo, run_id=run["run_id"], head_sha=head,
                      run_path=run_path, failure_path=failure_path)
    git(repo, "switch", "--quiet", "main")
    failure_sha = git(remote, "rev-parse", "refs/heads/aios/failure-artifacts/RUN-254-001")
    predecessor = {"repair_id": "REPAIR-254-001", "failed_run_id": run["run_id"],
                   "failed_head_sha": head, "task": run["task"],
                   "action": "CONTINUE_IMPLEMENTATION", "modification_scope": ["src/sample.py"],
                   "instructions": ["Continue implementation."],
                   "constraints": ["Bounded mutation authority only."]}
    initial = IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                              {"failed_run_id": run["run_id"]},
                              {"expected_failed_head_sha": head}, predecessor)
    return repo, remote, base, failure_sha, initial


def test_completion_gate_audited_supersession_preserves_execute_repair_and_r2_history(tmp_path):
    from aios_renew.brain_context import BrainContextError, compose_brain_work_context, resolve_flow
    from aios_renew.brain_sync import observe_brain_sync
    from aios_renew.decision_packet import compile_decision_packet, DecisionPacketError
    from aios_renew import decision_packet

    repo, remote, base, failure_sha, initial = completion_gate_supersession_fixture(tmp_path)
    work = compose_brain_work_context(repo=repo)
    assert resolve_flow(work).selected_flow == "REPAIR_AUTHORING"
    assert resolve_flow(work).selection_basis == "UNIFIED_STATE"
    assert work.current_request is None
    initial_audited = audited_envelope(initial, repo)
    first = execute_ingress(initial_audited, repo=repo)
    assert observe_unified_state("TASK-254", repo=repo).next_action == "EXECUTE_REPAIR"
    assert resolve_flow(compose_brain_work_context(repo=repo)).selected_flow == "NONE"

    prior_snapshot = observe_brain_sync(repo=repo)
    explicit = compose_brain_work_context(prior_snapshot,
                                          {"flow_selector": "REPAIR_AUTHORING",
                                           "human_input": "The implementation is already complete."})
    flow = resolve_flow(explicit)
    assert flow.selection_basis == "EXPLICIT_UNEXECUTED_REPAIR_SUPERSESSION"
    assert flow.authority_owner == "BRAIN"
    assert flow.canonical_next_action == flow.unified_state_next_action == "EXECUTE_REPAIR"
    assert flow.requires_fresh_context_for_continuation
    assert flow.pending_canonical_obligation is None
    material = explicit.repair_supersession_material
    # Reproduce the r2 completion blocker: the ordinary initial-authoring
    # packet cannot reinterpret the now-executable authorization as AUTHOR_REPAIR.
    ordinary_material = {key: material[key] for key in ("kind", "task", "failed_run", "failure")}
    with pytest.raises(DecisionPacketError, match="canonical state is not REPAIR authoring"):
        decision_packet._repair_authoring(ordinary_material, observe_brain_sync(repo=repo).as_dict())
    packet = compile_decision_packet(explicit, flow, material).as_dict()
    assert packet["subject"]["failure_artifacts_sha"] == failure_sha
    assert packet["subject"]["failed_run_id"] == "RUN-254-001"
    assert packet["subject"]["current_repair_authorization_sha"] == first.canonical_sha
    assert packet["prior_semantic_decisions"] == {
        "kind": "REPAIR", "authorization_sha": first.canonical_sha, "authorization": initial.payload}
    assert packet["bounded_observations"]["phase"] == "COMPLETION_GATE"
    assert all(packet[key] is False for key in
               ("run_created", "executor_invoked", "verification_invoked", "state_mutated"))
    altered = copy.deepcopy(material)
    altered["current_authorization"]["authorization"]["instructions"] = ["Caller invented prior strategy"]
    with pytest.raises(DecisionPacketError, match="canonical reconstruction"):
        compile_decision_packet(explicit, flow, altered)

    successor = replace(initial, expected_state={**initial.expected_state,
                        "expected_current_repair_sha": first.canonical_sha,
                        "expected_failure_artifacts_sha": failure_sha},
                        payload={**initial.payload, "action": "FINALIZE_CANDIDATE",
                                 "modification_scope": [], "instructions": ["Finalize existing candidate."]})
    with pytest.raises(AuthoringIngressError, match="requires audited_handoff"):
        execute_ingress(successor, repo=repo)
    with pytest.raises(AuthoringIngressError, match="audited authoring rejected"):
        execute_ingress(replace(successor, audited_handoff=initial_audited.audited_handoff), repo=repo)
    fresh = audited_envelope(successor, repo)
    assert fresh.audited_handoff["stage1"]["packet_fingerprint"] != initial_audited.audited_handoff["stage1"]["packet_fingerprint"]
    with pytest.raises(AuthoringIngressError, match="differs"):
        execute_ingress(replace(fresh, payload={**successor.payload, "instructions": ["Different strategy."]}), repo=repo)
    second = execute_ingress(fresh, repo=repo)
    current = resolve_remote_repair_authorization(repo, "RUN-254-001")
    assert current.commit_sha == second.canonical_sha and current.revision == 2
    assert current.predecessor_sha == first.canonical_sha
    assert current.failure_artifacts_sha == failure_sha
    assert observe_unified_state("TASK-254", repo=repo).next_action == "EXECUTE_REPAIR"
    assert git(remote, "rev-parse", "refs/heads/main") == base
    assert git(remote, "rev-parse", "refs/heads/aios/failure-artifacts/RUN-254-001") == failure_sha
    assert git(remote, "rev-parse", "refs/heads/aios/repair/RUN-254-001") == first.canonical_sha
    assert git(repo, "show", f"{base}:.ai/tasks/TASK-254.yaml") == (repo / ".ai/tasks/TASK-254.yaml").read_text(encoding="utf-8").strip()
    replay = execute_ingress(replace(successor, expected_state={**successor.expected_state,
                             "expected_current_repair_sha": second.canonical_sha}), repo=repo)
    assert replay.replayed and replay.canonical_sha == second.canonical_sha
    changed = replace(fresh, payload={**successor.payload, "instructions": ["Another successor strategy."]})
    with pytest.raises(AuthoringIngressError, match="expected current REPAIR SHA"):
        execute_ingress(changed, repo=repo)
    changed = replace(changed, expected_state={**changed.expected_state,
                      "expected_current_repair_sha": second.canonical_sha})
    with pytest.raises(AuthoringIngressError, match="audited authoring rejected"):
        execute_ingress(changed, repo=repo)
    with pytest.raises(BrainContextError, match="stale"):
        compose_brain_work_context(prior_snapshot, {"flow_selector": "REPAIR_AUTHORING"})
    assert resolve_remote_repair_authorization(repo, "RUN-254-001").commit_sha == second.canonical_sha

    # An admitted local continuation cannot be replaced by an external-only
    # snapshot that still displays EXECUTE_REPAIR.
    saved_snapshot = observe_brain_sync(repo=repo)
    from aios_renew.operator import _runtime_paths_readonly
    paths = _runtime_paths_readonly(repo)
    paths.runs.mkdir(parents=True, exist_ok=True)
    (paths.runs / "RUN-254-002.json").write_text(json.dumps({
        "run_id": "RUN-254-002", "task": {"id": "TASK-254", "revision": 2},
        "executor": "codex", "base_sha": initial.payload["failed_head_sha"],
        "workspace": str(repo), "head_sha": None, "status": "ACTIVE"}), encoding="utf-8")
    with pytest.raises(BrainContextError, match="local Runtime"):
        compose_brain_work_context(saved_snapshot, {"flow_selector": "REPAIR_AUTHORING"})


@pytest.mark.parametrize(
    "payload",
    [b"", b"\xef\xbb\xbffinding_id: F1\r\nraw: \x00\xff\nlast: no newline "],
    ids=["empty", "binary-bom-mixed-newlines"],
)
def test_read_commit_blob_is_binary_exact_in_source_and_fresh_clone(tmp_path, payload):
    repo, remote, parent_sha = setup_test_repo(tmp_path / "source")
    metadata_path = ".ai/remediations/REMEDIATION-RUN-105-001-F1.yaml"
    blob = subprocess.run(
        ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
        input=payload,
        capture_output=True,
        check=True,
    ).stdout.decode("ascii").strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{metadata_path}")
    git(repo, "commit", "--quiet", "-m", "exact metadata blob")
    commit_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    fresh = tmp_path / "fresh"
    subprocess.run(
        ("git", "clone", "--quiet", str(remote), str(fresh)),
        capture_output=True,
        check=True,
    )
    for checkout in (repo, fresh):
        assert authoring_ingress_module._read_commit_blob(
            checkout, commit_sha, metadata_path
        ) == payload
        assert authoring_ingress_module._read_commit_blob(
            checkout, commit_sha, metadata_path + ".missing"
        ) is None
        assert authoring_ingress_module._read_commit_blob(
            checkout, parent_sha, metadata_path
        ) is None
        assert authoring_ingress_module._read_commit_blob(
            checkout, "0" * 40, metadata_path
        ) is None
        assert authoring_ingress_module._read_commit_blob(
            checkout, blob, metadata_path
        ) is None
        assert authoring_ingress_module._read_commit_blob(
            checkout, commit_sha, ".ai/remediations"
        ) is None
        authoring_ingress_module._validate_metadata_commit(
            checkout,
            commit_sha,
            expected_parent_sha=parent_sha,
            metadata_path=metadata_path,
            metadata_bytes=payload,
            operation="AUTHOR_REMEDIATION",
        )
        with pytest.raises(AuthoringIngressError, match="metadata content mismatch"):
            authoring_ingress_module._validate_metadata_commit(
                checkout,
                commit_sha,
                expected_parent_sha=parent_sha,
                metadata_path=metadata_path,
                metadata_bytes=payload + b"\n",
                operation="AUTHOR_REMEDIATION",
            )


def setup_candidate_lineage(
    root: Path,
    *,
    run_id: str = "RUN-105-001",
    task_id: str = "TASK-105",
    task_source: str = TASK_105_SOURCE,
    candidate_file: str = "src/sample.py",
    candidate_content: str = "def sample(): return True\n",
    run_override: dict[str, object] | None = None,
    result_override: dict[str, object] | None = None,
) -> dict[str, object]:
    repo, remote, base_sha = setup_test_repo(root, task_id=task_id)
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / f"{task_id}.yaml").write_bytes(task_source.encode("utf-8"))
    workflow = repo / ".github" / "workflows" / "aios-auto-publish.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("name: AIOS auto publish\n", encoding="utf-8")
    (repo / "README.md").write_text("base content\n", encoding="utf-8")
    task_main_sha = commit_fixture_state(
        repo,
        paths=(".",),
        message=f"add {task_id}",
        user_name="AIOS Test",
        user_email="test@example.invalid",
        remote=remote,
        remote_ref="refs/heads/main",
    )

    sample_path = repo / candidate_file
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    sample_path.write_text(candidate_content, encoding="utf-8")
    candidate_sha = commit_fixture_state(
        repo,
        paths=(candidate_file,),
        message="candidate implementation",
        user_name="AIOS Test",
        user_email="test@example.invalid",
    )

    # Post-pass transport
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    run_path = state / "run.json"
    result_path = state / "result.json"

    run_payload = {
        "run_id": run_id,
        "task": {"id": task_id, "revision": 1},
        "executor": "antigravity",
        "base_sha": task_main_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    result_payload = {
        "result": {
            "head_sha": candidate_sha,
            "claims": [
                {
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": "The candidate is valid.",
                    "evidence": ["E1"],
                }
            ],
            "changed_files": [candidate_file],
            "unresolved": [],
        },
        "evidence": [
            {
                "evidence_id": "E1",
                "run_id": run_id,
                "subject_sha": candidate_sha,
                "type": "verification",
                "source": {"command": "git diff --check"},
                "result": {"exit_code": 0, "summary": "clean"},
                "raw": {"path": ".git/aios/evidence/E1.log"},
            }
        ],
    }
    if run_override:
        run_payload.update(run_override)
    if result_override:
        for k, v in result_override.items():
            if k == "result" and isinstance(v, dict):
                v_copy = dict(v)
                if v_copy.get("head_sha") == "candidate_sha":
                    v_copy["head_sha"] = candidate_sha
                result_payload["result"] = v_copy
            elif k == "evidence" and isinstance(v, list):
                ev_list = []
                for item in v:
                    item_copy = dict(item)
                    if item_copy.get("subject_sha") == "candidate_sha":
                        item_copy["subject_sha"] = candidate_sha
                    ev_list.append(item_copy)
                result_payload["evidence"] = ev_list
            else:
                result_payload[k] = v

    run_path.write_text(json.dumps(run_payload), encoding="utf-8")
    result_path.write_text(json.dumps(result_payload), encoding="utf-8")

    transport_post_pass(
        repo,
        run_id=run_id,
        head_sha=candidate_sha,
        run_path=run_path,
        result_path=result_path,
    )

    return {
        "repo": repo,
        "remote": remote,
        "task_id": task_id,
        "run_id": run_id,
        "main_sha": task_main_sha,
        "candidate_sha": candidate_sha,
    }


# ===========================================================================
# AC1: Envelope validation, reject unknown/prohibited fields, carrier input
# ===========================================================================

def test_envelope_validation_rejection():
    # Invalid format
    with pytest.raises(AuthoringIngressError, match="invalid envelope format"):
        parse_envelope({"format": "INVALID", "version": 1, "operation": "AUTHOR_TASK"})

    # Invalid version
    with pytest.raises(AuthoringIngressError, match="invalid envelope version"):
        parse_envelope({"format": "AIOS_INGRESS_ENVELOPE", "version": 2, "operation": "AUTHOR_TASK"})

    # Unknown operation
    with pytest.raises(AuthoringIngressError, match="invalid envelope operation"):
        parse_envelope({"format": "AIOS_INGRESS_ENVELOPE", "version": 1, "operation": "UNKNOWN_OP"})

    # Prohibited destination field
    with pytest.raises(AuthoringIngressError, match="prohibited field detected: 'destination'"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "destination": ".ai/tasks/TASK-105.yaml",
            "expected_state": {"main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Prohibited command field
    with pytest.raises(AuthoringIngressError, match="prohibited field detected: 'command'"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "command": "git push origin main",
            "expected_state": {"main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Prohibited authority override
    with pytest.raises(AuthoringIngressError, match="prohibited field detected: 'credentials'"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "credentials": "token-123",
            "expected_state": {"main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Prohibited ref
    with pytest.raises(AuthoringIngressError, match="prohibited field detected: 'ref'"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "ref": "refs/heads/main",
            "expected_state": {"main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Unknown top-level field
    with pytest.raises(AuthoringIngressError, match="envelope contains unknown field"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "mystery_param": True,
            "expected_state": {"main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Envelope format alias rejection (AIOS_AUTHORING_INGRESS is rejected)
    with pytest.raises(AuthoringIngressError, match="invalid envelope format"):
        parse_envelope({
            "format": "AIOS_AUTHORING_INGRESS",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "identity": {"task_id": "TASK-105"},
            "expected_state": {"expected_main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Top-level identity alias rejection
    with pytest.raises(AuthoringIngressError, match="identity"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "task_id": "TASK-105",
            "expected_state": {"expected_main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Duplicate identity representation (both top-level and in identity) fails closed
    with pytest.raises(AuthoringIngressError, match="identity"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "identity": {"task_id": "TASK-105"},
            "task_id": "TASK-105",
            "expected_state": {"expected_main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Conflicting identity representation fails closed
    with pytest.raises(AuthoringIngressError, match="identity"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "identity": {"task_id": "TASK-105"},
            "task_id": "TASK-999",
            "expected_state": {"expected_main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Missing identity mapping fails closed
    with pytest.raises(AuthoringIngressError, match="identity is required and must be a mapping"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "expected_state": {"expected_main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    with pytest.raises(AuthoringIngressError, match="identity is required and must be a mapping"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "identity": "TASK-105",
            "expected_state": {"expected_main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })

    # Unexpected keys in identity mapping fail closed
    with pytest.raises(AuthoringIngressError, match="identity contains unexpected key"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "identity": {"task_id": "TASK-105", "run_id": "RUN-105-001"},
            "expected_state": {"expected_main_sha": "a" * 40},
            "payload": TASK_105_SOURCE,
        })


def test_carrier_parsing(tmp_path):
    envelope_data = {
        "format": "AIOS_INGRESS_ENVELOPE",
        "version": 1,
        "operation": "AUTHOR_TASK",
        "identity": {"task_id": "TASK-105"},
        "expected_state": {"expected_main_sha": "a" * 40},
        "payload": TASK_105_SOURCE,
    }
    # File delivery
    file_path = tmp_path / "envelope.json"
    file_path.write_text(json.dumps(envelope_data), encoding="utf-8")
    env = read_carrier_input(str(file_path))
    assert env.operation == "AUTHOR_TASK"
    assert env.identity["task_id"] == "TASK-105"

    # Stdin delivery
    env_stdin = read_carrier_input("-", stdin_bytes=json.dumps(envelope_data).encode("utf-8"))
    assert env_stdin.operation == "AUTHOR_TASK"

    # Missing file fails closed
    with pytest.raises(AuthoringIngressError, match="envelope file not found"):
        read_carrier_input(str(tmp_path / "nonexistent.json"))


# ===========================================================================
# AC2: AUTHOR_TASK deterministic canonicalization, revisions, continuity, CAS
# ===========================================================================

def test_author_task_new_and_revision(tmp_path):
    repo, remote, base_sha = setup_test_repo(tmp_path)

    # 1. Author new TASK-105 revision 1
    envelope = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_TASK",
        identity={"task_id": "TASK-105"},
        expected_state={"expected_main_sha": base_sha},
        payload=V1_TASK_105_SOURCE,
    )
    envelope = audited_envelope(envelope, repo)
    result = execute_ingress(envelope, repo=repo)
    assert result.status == "CANONICALIZED"
    assert result.canonical_destination == ".ai/tasks/TASK-105.yaml"
    assert result.replayed is False
    assert (repo / ".ai" / "tasks" / "TASK-105.yaml").is_file()

    new_main_sha = git(repo, "rev-parse", "refs/heads/main")
    assert new_main_sha == result.canonical_sha
    assert git(remote, "rev-parse", "refs/heads/main") == new_main_sha

    # 2. Idempotent replay of identical TASK revision 1
    replay_result = execute_ingress(envelope, repo=repo)
    assert replay_result.status == "IDEMPOTENT"
    assert replay_result.replayed is True
    assert replay_result.canonical_sha == new_main_sha

    # 3. Conflicting attempt (different payload for same revision 1) fails closed
    conflicting_source = V1_TASK_105_SOURCE.replace("Implement generic ingress capability.", "Different goal.")
    conflicting_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_TASK",
        identity={"task_id": "TASK-105"},
        expected_state={"expected_main_sha": new_main_sha},
        payload=conflicting_source,
    )
    with pytest.raises(AuthoringIngressError, match="conflicting TASK payload"):
        execute_ingress(conflicting_env, repo=repo)

    # 4. Stale CAS attempt fails closed
    stale_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_TASK",
        identity={"task_id": "TASK-105"},
        expected_state={"expected_main_sha": base_sha},  # stale
        payload=V1_TASK_105_R2_SOURCE,
    )
    with pytest.raises(AuthoringIngressError, match="expected main SHA mismatch"):
        execute_ingress(stale_env, repo=repo)

    # 5. Continuity violation: revision 3 when revision 1 is on main fails closed
    r3_source = V1_TASK_105_R2_SOURCE.replace("revision: 2", "revision: 3")
    r3_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_TASK",
        identity={"task_id": "TASK-105"},
        expected_state={"expected_main_sha": new_main_sha},
        payload=r3_source,
    )
    with pytest.raises(AuthoringIngressError, match="revision continuity violation"):
        execute_ingress(r3_env, repo=repo)

    # 6. Valid revision 2 succeeds
    r2_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_TASK",
        identity={"task_id": "TASK-105"},
        expected_state={"expected_main_sha": new_main_sha},
        payload=V1_TASK_105_R2_SOURCE,
    )
    r2_result = execute_ingress(audited_envelope(r2_env, repo), repo=repo)
    assert r2_result.status == "CANONICALIZED"
    assert "revision: 2" in (repo / ".ai" / "tasks" / "TASK-105.yaml").read_text(encoding="utf-8")


def test_author_task_rejects_new_legacy_but_replays_historical_revision(tmp_path):
    repo, _, base_sha = setup_test_repo(tmp_path)
    new_envelope = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_TASK",
        identity={"task_id": "TASK-105"},
        expected_state={"expected_main_sha": base_sha},
        payload=TASK_105_SOURCE,
    )
    with pytest.raises(AuthoringIngressError, match="minimum-sufficient-v1"):
        execute_ingress(new_envelope, repo=repo)

    task_path = repo / ".ai" / "tasks" / "TASK-105.yaml"
    task_path.parent.mkdir(parents=True)
    task_path.write_text(TASK_105_SOURCE, encoding="utf-8")
    git(repo, "add", ".ai/tasks/TASK-105.yaml")
    git(repo, "commit", "--quiet", "-m", "historical task")
    historical_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    replay = execute_ingress(
        IngressEnvelope(
            format="AIOS_INGRESS_ENVELOPE",
            version=1,
            operation="AUTHOR_TASK",
            identity={"task_id": "TASK-105"},
            expected_state={"expected_main_sha": historical_sha},
            payload=TASK_105_SOURCE,
        ),
        repo=repo,
    )
    assert replay.status == "IDEMPOTENT"
    assert replay.canonical_sha == historical_sha


# ===========================================================================
# AC3, AC4, AC5: SUBMIT_REVIEW (PASS and CHANGES_REQUIRED), main separation
# ===========================================================================

def test_submit_review_pass_and_changes_required(tmp_path):
    lineage = setup_candidate_lineage(tmp_path)
    repo = lineage["repo"]
    run_id = lineage["run_id"]
    candidate_sha = lineage["candidate_sha"]
    main_before = git(repo, "rev-parse", "refs/heads/main")

    pass_review = f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
"""
    envelope = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id},
        expected_state={"expected_candidate_sha": candidate_sha},
        payload=pass_review,
    )
    res = execute_ingress(envelope, repo=repo)
    assert res.status == "CANONICALIZED"
    assert res.canonical_destination == f"refs/heads/aios/review-decision/{run_id}"
    assert_exact_metadata_delta(
        repo,
        res.canonical_sha,
        candidate_sha,
        ".ai/reviews/REVIEW-105-001.yaml",
        pass_review.encode("utf-8"),
    )
    assert git(
        repo,
        "rev-parse",
        f"{res.canonical_sha}:.github/workflows/aios-auto-publish.yml",
    ) == git(
        repo,
        "rev-parse",
        f"{candidate_sha}:.github/workflows/aios-auto-publish.yml",
    )

    # AC3: Prove review metadata cannot become implementation-main content
    main_after = git(repo, "rev-parse", "refs/heads/main")
    assert main_after == main_before
    code, _, _ = subprocess.run(
        ("git", "-C", str(repo), "show", "refs/heads/main:.ai/reviews/REVIEW-105-001.yaml"),
        capture_output=True,
    ).returncode, "", ""
    assert code != 0, "review metadata must not exist on implementation main"

    # AC5: Ingress does not publish main, but canonical publication independently succeeds
    pub_report = publish_review_decision(
        repo,
        run_id=run_id,
        decision_sha=res.canonical_sha,
    )
    assert pub_report.outcome == "PUBLISHED"
    # Now main has advanced to reviewed candidate
    assert git(repo, "rev-parse", "origin/main") == candidate_sha
    # And candidate on main STILL does not contain review metadata!
    code, _, _ = subprocess.run(
        ("git", "-C", str(repo), "show", "origin/main:.ai/reviews/REVIEW-105-001.yaml"),
        capture_output=True,
    ).returncode, "", ""
    assert code != 0, "candidate on main must not contain review metadata"


def test_submit_review_accepts_code_fix_zero_delta_failure_continuation(
    tmp_path: Path,
) -> None:
    repo, _, _ = setup_test_repo(tmp_path)
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "TASK-105.yaml").write_text(TASK_105_SOURCE, encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "sample.py").write_text("value = 'base'\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "task candidate base")
    root_base_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    state = tmp_path / "zero-delta-code-fix"
    state.mkdir()
    (repo / "src" / "sample.py").write_text("value = 'failed'\n", encoding="utf-8")
    git(repo, "add", "src/sample.py")
    git(repo, "commit", "--quiet", "-m", "failed implementation")
    failed_code_sha = git(repo, "rev-parse", "HEAD")

    first_run_id = "RUN-105-002"
    first_run = {
        "run_id": first_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "codex",
        "base_sha": root_base_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    first_failure = {
        "kind": "FAILURE",
        "run_id": first_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "codex",
        "base_sha": root_base_sha,
        "failed_head_sha": failed_code_sha,
        "phase": "VERIFICATION",
        "error": {"type": "RuntimeVerificationError", "message": "failed"},
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["src/sample.py"],
            "outside_task_scope": [],
        },
    }
    first_run_path = state / "first-run.json"
    first_failure_path = state / "first-failure.json"
    first_run_path.write_text(json.dumps(first_run), encoding="utf-8")
    first_failure_path.write_text(json.dumps(first_failure), encoding="utf-8")
    transport_failure(
        repo,
        run_id=first_run_id,
        head_sha=failed_code_sha,
        run_path=first_run_path,
        failure_path=first_failure_path,
    )
    code_fix = {
        "repair_id": "REPAIR-105-002",
        "failed_run_id": first_run_id,
        "failed_head_sha": failed_code_sha,
        "task": {"id": "TASK-105", "revision": 1},
        "action": "CODE_FIX",
        "modification_scope": ["src/sample.py"],
        "instructions": ["Correct the failed implementation."],
        "constraints": [],
    }
    code_fix_auth = execute_audited_ingress(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
            {"failed_run_id": first_run_id},
            {"expected_failed_head_sha": failed_code_sha}, code_fix,
        ),
        repo=repo,
    )

    zero_run_id = "RUN-105-003"
    zero_run = {
        "run_id": zero_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "codex",
        "base_sha": failed_code_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    code_fix_lineage = {
        "failed_run_id": first_run_id,
        "root_base_sha": root_base_sha,
        "failed_head_sha": failed_code_sha,
        "failure": first_failure,
        "task": {"task_id": "TASK-105", "revision": 1},
        "repair": code_fix,
        "repair_authorization_sha": code_fix_auth.canonical_sha,
        "run": zero_run,
    }
    zero_failure = {
        "kind": "FAILURE",
        "run_id": zero_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "codex",
        "base_sha": failed_code_sha,
        "failed_head_sha": failed_code_sha,
        "phase": "EXECUTION",
        "error": {"type": "ExecutorUnavailable", "message": "capacity"},
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": [],
            "outside_task_scope": [],
        },
    }
    zero_run_path = state / "zero-run.json"
    zero_failure_path = state / "zero-failure.json"
    code_fix_lineage_path = state / "code-fix-lineage.json"
    zero_run_path.write_text(json.dumps(zero_run), encoding="utf-8")
    zero_failure_path.write_text(json.dumps(zero_failure), encoding="utf-8")
    code_fix_lineage_path.write_text(json.dumps(code_fix_lineage), encoding="utf-8")
    transport_failure(
        repo,
        run_id=zero_run_id,
        head_sha=failed_code_sha,
        run_path=zero_run_path,
        failure_path=zero_failure_path,
        lineage_path=code_fix_lineage_path,
    )
    continuation = {
        "repair_id": "REPAIR-105-003",
        "failed_run_id": zero_run_id,
        "failed_head_sha": failed_code_sha,
        "task": {"id": "TASK-105", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["src/sample.py"],
        "instructions": ["Continue the authorized correction."],
        "constraints": [],
    }
    continuation_auth = execute_audited_ingress(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
            {"failed_run_id": zero_run_id},
            {"expected_failed_head_sha": failed_code_sha}, continuation,
        ),
        repo=repo,
    )

    (repo / "src" / "sample.py").write_text("value = 'repaired'\n", encoding="utf-8")
    git(repo, "add", "src/sample.py")
    git(repo, "commit", "--quiet", "-m", "complete continued implementation")
    candidate_sha = git(repo, "rev-parse", "HEAD")
    final_run_id = "RUN-105-004"
    final_run = {
        "run_id": final_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "codex",
        "base_sha": failed_code_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    continuation_lineage = {
        "failed_run_id": zero_run_id,
        "root_base_sha": root_base_sha,
        "failed_head_sha": failed_code_sha,
        "failure": zero_failure,
        "task": {"task_id": "TASK-105", "revision": 1},
        "repair": continuation,
        "repair_authorization_sha": continuation_auth.canonical_sha,
        "run": final_run,
    }
    final_result = {
        "result": {
            "head_sha": candidate_sha,
            "claims": [{
                "id": "C1", "satisfies": ["AC1"],
                "claim": "The continued implementation is complete.",
                "evidence": ["E1"],
            }],
            "changed_files": ["src/sample.py"],
            "unresolved": [],
        },
        "evidence": [{
            "evidence_id": "E1", "run_id": final_run_id,
            "subject_sha": candidate_sha, "type": "TEST",
            "source": {"command": "git diff --check"},
            "result": {"exit_code": 0, "summary": "clean"},
            "raw": {"path": ".ai/evidence/E1.log"},
        }],
    }
    final_run_path = state / "final-run.json"
    final_result_path = state / "final-result.json"
    continuation_lineage_path = state / "continuation-lineage.json"
    final_run_path.write_text(json.dumps(final_run), encoding="utf-8")
    final_result_path.write_text(json.dumps(final_result), encoding="utf-8")
    continuation_lineage_path.write_text(json.dumps(continuation_lineage), encoding="utf-8")
    transport_post_pass(
        repo, run_id=final_run_id, head_sha=candidate_sha,
        run_path=final_run_path, result_path=final_result_path,
        lineage_path=continuation_lineage_path,
    )

    review = f"""review_id: REVIEW-105-004
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
"""
    result = execute_ingress(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "SUBMIT_REVIEW",
            {"run_id": final_run_id},
            {"expected_candidate_sha": candidate_sha}, review,
        ),
        repo=repo,
    )

    assert result.status == "CANONICALIZED"
    assert result.canonical_destination == f"refs/heads/aios/review-decision/{final_run_id}"
    assert_exact_metadata_delta(
        repo, result.canonical_sha, candidate_sha,
        ".ai/reviews/REVIEW-105-004.yaml", review.encode("utf-8"),
    )


def test_changes_required_review_observable_in_unified_state(tmp_path):
    lineage = setup_candidate_lineage(tmp_path)
    repo = lineage["repo"]
    run_id = lineage["run_id"]
    task_id = lineage["task_id"]
    candidate_sha = lineage["candidate_sha"]

    cr_review = f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance:
  AC1: FAIL
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: src/sample.py
    issue: Missing required feature.
    expected: Implement feature.
"""
    envelope = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id},
        expected_state={"expected_candidate_sha": candidate_sha},
        payload=cr_review,
    )
    res = execute_ingress(envelope, repo=repo)
    assert res.status == "CANONICALIZED"
    assert_exact_metadata_delta(
        repo,
        res.canonical_sha,
        candidate_sha,
        ".ai/reviews/REVIEW-105-001.yaml",
        cr_review.encode("utf-8"),
    )
    assert git(
        repo,
        "rev-parse",
        f"{res.canonical_sha}:.github/workflows/aios-auto-publish.yml",
    ) == git(
        repo,
        "rev-parse",
        f"{candidate_sha}:.github/workflows/aios-auto-publish.yml",
    )

    # AC4: Observable through existing Unified State path without creating RUN or invoking Executor
    runs_dir = repo / ".git" / "aios" / "runs"
    assert not runs_dir.exists() or len(list(runs_dir.glob("*.json"))) == 0

    obs = observe_unified_state(task_id, repo=repo)
    assert obs.lifecycle_state == "CORRECTION"
    assert obs.next_action == "AUTHOR_REMEDIATION"
    assert obs.finding_id == "F1"

    # A same-payload replay cannot certify or replace a malformed destination.
    decision_ref = f"refs/heads/aios/review-decision/{run_id}"
    decision_tree = git(repo, "rev-parse", f"{res.canonical_sha}^{{tree}}")
    malformed_sha = git(
        repo,
        "commit-tree",
        decision_tree,
        "-p",
        lineage["main_sha"],
        "-m",
        "malformed review decision",
    )
    git(repo, "push", "--quiet", "--force", "origin", f"{malformed_sha}:{decision_ref}")
    with pytest.raises(AuthoringIngressError, match="conflicting review decision"):
        execute_ingress(envelope, repo=repo)
    assert git(repo, "ls-remote", "--refs", "origin", decision_ref).split()[0] == malformed_sha
    assert git(
        repo, "show", f"{malformed_sha}:.ai/reviews/REVIEW-105-001.yaml"
    )


def test_submit_review_rejects_run_identity_and_task_revision_mismatches(tmp_path):
    # 1. RUN run_id mismatch
    p1 = tmp_path / "case1"
    lineage1 = setup_candidate_lineage(
        p1,
        run_override={"run_id": "RUN-105-999"},
    )
    repo1 = lineage1["repo"]
    run_id1 = lineage1["run_id"]
    candidate_sha1 = lineage1["candidate_sha"]
    env1 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id1},
        expected_state={"expected_candidate_sha": candidate_sha1},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha1}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="RUN run_id mismatch"):
        execute_ingress(env1, repo=repo1)

    # 2. TASK revision mismatch (RUN references r2, but candidate has r1)
    p2 = tmp_path / "case2"
    lineage2 = setup_candidate_lineage(
        p2,
        run_override={"task": {"id": "TASK-105", "revision": 2}},
    )
    repo2 = lineage2["repo"]
    run_id2 = lineage2["run_id"]
    candidate_sha2 = lineage2["candidate_sha"]
    env2 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id2},
        expected_state={"expected_candidate_sha": candidate_sha2},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha2}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="RUN does not reference the supplied TASK"):
        execute_ingress(env2, repo=repo2)


def test_submit_review_rejects_evidence_and_verification_mismatches(tmp_path):
    # 1. Evidence run_id mismatch
    p1 = tmp_path / "case1"
    lineage1 = setup_candidate_lineage(
        p1,
        result_override={
            "evidence": [
                {
                    "evidence_id": "E1",
                    "run_id": "RUN-OTHER-999",
                    "subject_sha": "candidate_sha",
                    "type": "verification",
                    "source": {"command": "git diff --check"},
                    "result": {"exit_code": 0, "summary": "clean"},
                    "raw": {"path": ".git/aios/evidence/E1.log"},
                }
            ]
        },
    )
    repo1 = lineage1["repo"]
    run_id1 = lineage1["run_id"]
    candidate_sha1 = lineage1["candidate_sha"]
    env1 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id1},
        expected_state={"expected_candidate_sha": candidate_sha1},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha1}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="does not reference RUN"):
        execute_ingress(env1, repo=repo1)

    # 2. Evidence subject_sha mismatch
    p2 = tmp_path / "case2"
    lineage2 = setup_candidate_lineage(
        p2,
        result_override={
            "evidence": [
                {
                    "evidence_id": "E1",
                    "run_id": "RUN-105-001",
                    "subject_sha": "0" * 40,
                    "type": "verification",
                    "source": {"command": "git diff --check"},
                    "result": {"exit_code": 0, "summary": "clean"},
                    "raw": {"path": ".git/aios/evidence/E1.log"},
                }
            ]
        },
    )
    repo2 = lineage2["repo"]
    run_id2 = lineage2["run_id"]
    candidate_sha2 = lineage2["candidate_sha"]
    env2 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id2},
        expected_state={"expected_candidate_sha": candidate_sha2},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha2}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="subject_sha does not match RESULT head_sha"):
        execute_ingress(env2, repo=repo2)

    # 3. Missing required verification evidence
    p3 = tmp_path / "case3"
    lineage3 = setup_candidate_lineage(
        p3,
        result_override={
            "result": {
                "head_sha": "candidate_sha",
                "claims": [
                    {
                        "id": "C1",
                        "satisfies": ["AC1"],
                        "claim": "The candidate is valid.",
                        "evidence": ["E2"],
                    }
                ],
                "changed_files": ["src/sample.py"],
                "unresolved": [],
            },
            "evidence": [
                {
                    "evidence_id": "E2",
                    "run_id": "RUN-105-001",
                    "subject_sha": "candidate_sha",
                    "type": "verification",
                    "source": {"command": "other-command"},
                    "result": {"exit_code": 0, "summary": "clean"},
                    "raw": {"path": ".git/aios/evidence/E2.log"},
                }
            ],
        },
    )
    repo3 = lineage3["repo"]
    run_id3 = lineage3["run_id"]
    candidate_sha3 = lineage3["candidate_sha"]
    env3 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id3},
        expected_state={"expected_candidate_sha": candidate_sha3},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha3}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="missing verification evidence for required command"):
        execute_ingress(env3, repo=repo3)


def test_submit_review_rejects_base_sha_ancestry_and_invalid_run_status(tmp_path):
    # 1. Candidate does not descend from RUN base_sha
    p1 = tmp_path / "case1"
    repo1, remote1, _ = setup_test_repo(p1)
    task_dir1 = repo1 / ".ai" / "tasks"
    task_dir1.mkdir(parents=True, exist_ok=True)
    (task_dir1 / "TASK-105.yaml").write_text(TASK_105_SOURCE, encoding="utf-8")
    git(repo1, "add", ".")
    git(repo1, "commit", "--quiet", "-m", "add TASK-105")
    git(repo1, "push", "--quiet", "origin", "main")

    # Create orphaned commit on unrelated branch to use as RUN base_sha
    git(repo1, "checkout", "--orphan", "unrelated-branch")
    git(repo1, "rm", "-rf", ".")
    (repo1 / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    git(repo1, "add", "unrelated.txt")
    git(repo1, "commit", "--quiet", "-m", "unrelated commit")
    unrelated_base = git(repo1, "rev-parse", "HEAD")
    git(repo1, "checkout", "main")

    sample_path1 = repo1 / "src" / "sample.py"
    sample_path1.parent.mkdir(parents=True, exist_ok=True)
    sample_path1.write_text("def sample(): return True\n", encoding="utf-8")
    git(repo1, "add", "src/sample.py")
    git(repo1, "commit", "--quiet", "-m", "candidate implementation")
    candidate_sha1 = git(repo1, "rev-parse", "HEAD")

    run_id1 = "RUN-105-001"
    state1 = p1 / "state"
    state1.mkdir(parents=True, exist_ok=True)
    run_path1 = state1 / "run.json"
    result_path1 = state1 / "result.json"

    run_payload1 = {
        "run_id": run_id1,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "antigravity",
        "base_sha": unrelated_base,
        "workspace": str(repo1),
        "head_sha": None,
        "status": "ACTIVE",
    }
    result_payload1 = {
        "result": {
            "head_sha": candidate_sha1,
            "claims": [
                {
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": "The candidate is valid.",
                    "evidence": ["E1"],
                }
            ],
            "changed_files": ["src/sample.py"],
            "unresolved": [],
        },
        "evidence": [
            {
                "evidence_id": "E1",
                "run_id": run_id1,
                "subject_sha": candidate_sha1,
                "type": "verification",
                "source": {"command": "git diff --check"},
                "result": {"exit_code": 0, "summary": "clean"},
                "raw": {"path": ".git/aios/evidence/E1.log"},
            }
        ],
    }
    run_path1.write_text(json.dumps(run_payload1), encoding="utf-8")
    result_path1.write_text(json.dumps(result_payload1), encoding="utf-8")
    transport_post_pass(
        repo1,
        run_id=run_id1,
        head_sha=candidate_sha1,
        run_path=run_path1,
        result_path=result_path1,
    )

    env1 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id1},
        expected_state={"expected_candidate_sha": candidate_sha1},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha1}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="does not descend from RUN base_sha"):
        execute_ingress(env1, repo=repo1)

    # 2. RUN status is not ACTIVE
    p2 = tmp_path / "case2"
    lineage2 = setup_candidate_lineage(p2, run_override={"status": "FAILED"})
    repo2 = lineage2["repo"]
    run_id2 = lineage2["run_id"]
    candidate_sha2 = lineage2["candidate_sha"]
    env2 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id2},
        expected_state={"expected_candidate_sha": candidate_sha2},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha2}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="canonical successful RUN status is invalid"):
        execute_ingress(env2, repo=repo2)

    # 3. Invalid executor in RUN
    p3 = tmp_path / "case3"
    lineage3 = setup_candidate_lineage(p3, run_override={"executor": "unsupported_executor"})
    repo3 = lineage3["repo"]
    run_id3 = lineage3["run_id"]
    candidate_sha3 = lineage3["candidate_sha"]
    env3 = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id3},
        expected_state={"expected_candidate_sha": candidate_sha3},
        payload=f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha3}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
""",
    )
    with pytest.raises(AuthoringIngressError, match="invalid canonical RUN"):
        execute_ingress(env3, repo=repo3)


# ===========================================================================
# AC6: AUTHOR_REMEDIATION and AUTHOR_REPAIR
# ===========================================================================

def test_author_remediation_success_and_rejections(tmp_path):
    lineage = setup_candidate_lineage(tmp_path)
    repo = lineage["repo"]
    run_id = lineage["run_id"]
    candidate_sha = lineage["candidate_sha"]

    # First submit CHANGES_REQUIRED review with two findings F1 and F2
    cr_review = f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance:
  AC1: FAIL
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: src/sample.py
    issue: Defect found.
    expected: Fix defect.
  - id: F2
    basis: AC1
    action: CODE_FIX
    location: src/sample.py
    issue: Another defect found.
    expected: Fix another defect.
"""
    review_result = execute_ingress(
        IngressEnvelope(
            format="AIOS_INGRESS_ENVELOPE",
            version=1,
            operation="SUBMIT_REVIEW",
            identity={"run_id": run_id},
            expected_state={"expected_candidate_sha": candidate_sha},
            payload=cr_review,
        ),
        repo=repo,
    )

    remediation_payload = f"""\
finding_id: F1
action: CODE_FIX
reviewed_sha: {candidate_sha}
modification_scope:
  - src/sample.py
affected_verification:
  - git diff --check
constraints:
  hard:
    - Bounded mutation authority only.
"""
    rem_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REMEDIATION",
        identity={"source_run_id": run_id, "finding_id": "F1"},
        expected_state={"expected_reviewed_sha": candidate_sha},
        payload=remediation_payload,
    )
    with pytest.raises(AuthoringIngressError, match="requires audited_handoff"):
        execute_ingress(rem_env, repo=repo)
    rem_env = audited_envelope(rem_env, repo)
    result = execute_ingress(rem_env, repo=repo)
    assert result.status == "CANONICALIZED"
    assert result.canonical_destination == f"refs/heads/aios/remediation/{run_id}-F1"
    assert authoring_ingress_module._read_commit_blob(
        repo,
        result.canonical_sha,
        f".ai/remediations/REMEDIATION-{run_id}-F1.yaml",
    ) == remediation_payload.encode("utf-8")
    assert_exact_metadata_delta(
        repo,
        result.canonical_sha,
        review_result.canonical_sha,
        f".ai/remediations/REMEDIATION-{run_id}-F1.yaml",
        remediation_payload.encode("utf-8"),
    )
    assert git(
        repo,
        "rev-parse",
        f"{result.canonical_sha}:.ai/reviews/REVIEW-105-001.yaml",
    ) == git(
        repo,
        "rev-parse",
        f"{review_result.canonical_sha}:.ai/reviews/REVIEW-105-001.yaml",
    )

    # Idempotent replay
    replay = execute_ingress(rem_env, repo=repo)
    assert replay.status == "IDEMPOTENT"
    assert replay.replayed is True

    # Conflicting replay for F1 fails closed
    conflict_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REMEDIATION",
        identity={"source_run_id": run_id, "finding_id": "F1"},
        expected_state={"expected_reviewed_sha": candidate_sha},
        payload=remediation_payload.replace("Bounded mutation authority only.", "Different constraint."),
    )
    with pytest.raises(AuthoringIngressError, match="conflicting canonical remediation"):
        execute_ingress(conflict_env, repo=repo)

    # Rejection: unknown finding
    bad_finding_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REMEDIATION",
        identity={"source_run_id": run_id, "finding_id": "F999"},
        expected_state={"expected_reviewed_sha": candidate_sha},
        payload=remediation_payload.replace("finding_id: F1", "finding_id: F999"),
    )
    with pytest.raises(AuthoringIngressError, match="not found in source review"):
        execute_ingress(bad_finding_env, repo=repo)

    # Rejection: scope widens task scope (tested on uncanonicalized finding F2)
    wide_scope_payload = f"""\
finding_id: F2
action: CODE_FIX
reviewed_sha: {candidate_sha}
modification_scope:
  - src/sample.py
  - outside.txt
affected_verification:
  - git diff --check
constraints:
  hard:
    - Bounded mutation authority only.
"""
    wide_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REMEDIATION",
        identity={"source_run_id": run_id, "finding_id": "F2"},
        expected_state={"expected_reviewed_sha": candidate_sha},
        payload=wide_scope_payload,
    )
    with pytest.raises(AuthoringIngressError, match="widens TASK.scope.modify"):
        execute_ingress(wide_env, repo=repo)

    # Malformed historical content remains readable, but replay fails without overwrite.
    remediation_ref = f"refs/heads/aios/remediation/{run_id}-F1"
    remediation_tree = git(repo, "rev-parse", f"{result.canonical_sha}^{{tree}}")
    malformed_sha = git(
        repo,
        "commit-tree",
        remediation_tree,
        "-p",
        candidate_sha,
        "-m",
        "malformed remediation",
    )
    git(repo, "push", "--quiet", "--force", "origin", f"{malformed_sha}:{remediation_ref}")
    with pytest.raises(AuthoringIngressError, match="conflicting canonical remediation"):
        execute_ingress(rem_env, repo=repo)
    assert git(repo, "ls-remote", "--refs", "origin", remediation_ref).split()[0] == malformed_sha
    assert git(
        repo,
        "show",
        f"{malformed_sha}:.ai/remediations/REMEDIATION-{run_id}-F1.yaml",
    )


def test_author_remediation_for_v1_task_requires_v1_verification(tmp_path):
    lineage = setup_candidate_lineage(
        tmp_path, task_source=V1_TASK_105_SOURCE
    )
    repo = lineage["repo"]
    run_id = lineage["run_id"]
    candidate_sha = lineage["candidate_sha"]
    review = f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance:
  AC1: FAIL
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: src/sample.py
    issue: Defect found.
    expected: Fix defect.
"""
    execute_ingress(
        IngressEnvelope(
            format="AIOS_INGRESS_ENVELOPE",
            version=1,
            operation="SUBMIT_REVIEW",
            identity={"run_id": run_id},
            expected_state={"expected_candidate_sha": candidate_sha},
            payload=review,
        ),
        repo=repo,
    )

    legacy_payload = f"""\
finding_id: F1
action: CODE_FIX
reviewed_sha: {candidate_sha}
modification_scope: [src/sample.py]
affected_verification: [git diff --check]
"""
    envelope = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REMEDIATION",
        identity={"source_run_id": run_id, "finding_id": "F1"},
        expected_state={"expected_reviewed_sha": candidate_sha},
        payload=legacy_payload,
    )
    with pytest.raises(AuthoringIngressError, match="must use verification.policy"):
        execute_ingress(envelope, repo=repo)

    v1_payload = legacy_payload.replace(
        "affected_verification: [git diff --check]",
        "verification:\n"
        "  policy: minimum-sufficient-v1\n"
        "  affected: [git diff --check]",
    )
    result = execute_audited_ingress(
        IngressEnvelope(
            format=envelope.format,
            version=envelope.version,
            operation=envelope.operation,
            identity=envelope.identity,
            expected_state=envelope.expected_state,
            payload=v1_payload,
        ),
        repo=repo,
    )
    assert result.status == "CANONICALIZED"


def test_author_remediation_rejects_already_resolved_finding(tmp_path):
    lineage = setup_candidate_lineage(tmp_path)
    repo = lineage["repo"]
    run_id = lineage["run_id"]
    candidate_sha = lineage["candidate_sha"]

    # Submit CHANGES_REQUIRED review with finding F1
    cr_review = f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance:
  AC1: FAIL
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: src/sample.py
    issue: Defect found.
    expected: Fix defect.
"""
    execute_ingress(
        IngressEnvelope(
            format="AIOS_INGRESS_ENVELOPE",
            version=1,
            operation="SUBMIT_REVIEW",
            identity={"run_id": run_id},
            expected_state={"expected_candidate_sha": candidate_sha},
            payload=cr_review,
        ),
        repo=repo,
    )

    remediation_payload = f"""\
finding_id: F1
action: CODE_FIX
reviewed_sha: {candidate_sha}
modification_scope:
  - src/sample.py
affected_verification:
  - git diff --check
constraints:
  hard:
    - Bounded mutation authority only.
"""
    rem_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REMEDIATION",
        identity={"source_run_id": run_id, "finding_id": "F1"},
        expected_state={"expected_reviewed_sha": candidate_sha},
        payload=remediation_payload,
    )

    # Advance canonical main past reviewed candidate (simulating publication of later lineage)
    sample_path = repo / "src" / "sample.py"
    sample_path.write_text("def sample(): return 'resolved'\n", encoding="utf-8")
    git(repo, "add", "src/sample.py")
    git(repo, "commit", "--quiet", "-m", "resolve finding on main")
    advanced_main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/heads/main", advanced_main_sha)
    git(repo, "push", "--quiet", "origin", "main")

    # Rejection: finding is no longer outstanding after canonical main advanced past reviewed_sha
    with pytest.raises(
        AuthoringIngressError,
        match="already resolved, superseded, or otherwise no longer outstanding",
    ):
        execute_ingress(rem_env, repo=repo)

    # Verify no remediation ref was created
    rem_ref = f"refs/heads/aios/remediation/{run_id}-F1"
    output = git(repo, "ls-remote", "--refs", "origin", rem_ref)
    assert not output.strip()


def test_author_repair_success_and_rejections(tmp_path):
    repo, remote, base_sha = setup_test_repo(tmp_path)
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "TASK-105.yaml").write_text(TASK_105_SOURCE, encoding="utf-8")
    git(repo, "add", ".ai/tasks/TASK-105.yaml")
    git(repo, "commit", "--quiet", "-m", "canonical task before failure")
    base_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "sample.py").write_text("# broken\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "failed head candidate")
    failed_head_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    failed_run_id = "RUN-105-001"
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)

    run_payload = {
        "run_id": failed_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "antigravity",
        "base_sha": base_sha,
        "workspace": str(repo),
        "head_sha": failed_head_sha,
        "status": "ACTIVE",
    }
    failure_payload = {
        "kind": "FAILURE",
        "run_id": failed_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "antigravity",
        "base_sha": base_sha,
        "failed_head_sha": failed_head_sha,
        "phase": "VERIFICATION",
        "error": {"type": "RuntimeVerificationError", "message": "candidate failed"},
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["src/sample.py"],
            "outside_task_scope": [],
        },
    }

    run_path = state / "run.json"
    failure_path = state / "failure.json"
    run_path.write_text(json.dumps(run_payload), encoding="utf-8")
    failure_path.write_text(json.dumps(failure_payload), encoding="utf-8")

    transport_failure(
        repo,
        run_id=failed_run_id,
        head_sha=failed_head_sha,
        run_path=run_path,
        failure_path=failure_path,
    )

    repair_payload = {
        "repair_id": "REPAIR-105-001",
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head_sha,
        "task": {"id": "TASK-105", "revision": 1},
        "action": "CODE_FIX",
        "modification_scope": ["src/sample.py"],
        "instructions": ["Fix the broken implementation."],
        "constraints": ["Bounded mutation authority only."],
    }
    envelope = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REPAIR",
        identity={"failed_run_id": failed_run_id},
        expected_state={"expected_failed_head_sha": failed_head_sha},
        payload=repair_payload,
    )
    with pytest.raises(AuthoringIngressError, match="requires audited_handoff"):
        execute_ingress(envelope, repo=repo)
    envelope = audited_envelope(envelope, repo)
    result = execute_ingress(envelope, repo=repo)
    assert result.status == "CANONICALIZED"
    assert result.canonical_destination == f"refs/heads/aios/repair/{failed_run_id}"
    assert_exact_metadata_delta(
        repo,
        result.canonical_sha,
        failed_head_sha,
        ".ai/transport/repair.json",
        json.dumps(
            repair_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"),
    )

    # Idempotent replay
    replay = execute_ingress(envelope, repo=repo)
    assert replay.status == "IDEMPOTENT"
    assert replay.replayed is True

    # Exact replay needs no transient material. A changed authorization cannot
    # reuse that path or the previous authoring packet. Current projection
    # authorities do not provide a REPAIR_AUTHORING flow after authorization.
    assert execute_ingress(replace(envelope, audited_handoff=None), repo=repo).replayed
    failure_ref = f"refs/heads/aios/failure-artifacts/{failed_run_id}"
    failure_sha = git(repo, "ls-remote", "--refs", "origin", failure_ref).split()[0]
    changed = replace(envelope, payload={**repair_payload, "instructions": ["Changed strategy."]},
                      expected_state={**envelope.expected_state,
                                      "expected_current_repair_sha": result.canonical_sha,
                                      "expected_failure_artifacts_sha": failure_sha})
    with pytest.raises(AuthoringIngressError, match="selected flow"):
        execute_ingress(changed, repo=repo)
    assert git(repo, "ls-remote", "--refs", "origin", result.canonical_destination).split()[0] == result.canonical_sha

    # Stale expected_failed_head_sha rejection
    stale_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REPAIR",
        identity={"failed_run_id": failed_run_id},
        expected_state={"expected_failed_head_sha": "0" * 40},
        payload=repair_payload,
    )
    with pytest.raises(AuthoringIngressError, match="expected failed head SHA mismatch"):
        execute_ingress(stale_env, repo=repo)

    # A malformed existing repair remains readable and cannot be certified by replay.
    repair_ref = f"refs/heads/aios/repair/{failed_run_id}"
    repair_tree = git(repo, "rev-parse", f"{result.canonical_sha}^{{tree}}")
    malformed_sha = git(
        repo,
        "commit-tree",
        repair_tree,
        "-p",
        base_sha,
        "-m",
        "malformed repair",
    )
    git(repo, "push", "--quiet", "--force", "origin", f"{malformed_sha}:{repair_ref}")
    with pytest.raises(AuthoringIngressError, match="conflicting canonical repair"):
        execute_ingress(envelope, repo=repo)
    assert git(repo, "ls-remote", "--refs", "origin", repair_ref).split()[0] == malformed_sha
    assert git(repo, "show", f"{malformed_sha}:.ai/transport/repair.json")


def test_author_repair_immutable_supersession_resolves_one_current_tip(tmp_path):
    repo, remote, base_sha = setup_test_repo(tmp_path)
    (repo / ".ai" / "tasks").mkdir(parents=True, exist_ok=True)
    (repo / ".ai" / "tasks" / "TASK-105.yaml").write_text(
        TASK_105_SOURCE, encoding="utf-8"
    )
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "sample.py").write_text("# candidate\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "failed candidate")
    failed_head_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    failed_run_id = "RUN-105-004"
    state = tmp_path / "supersession-state"
    state.mkdir()
    run = {
        "run_id": failed_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "codex",
        "base_sha": base_sha,
        "workspace": str(repo),
        "head_sha": failed_head_sha,
        "status": "ACTIVE",
    }
    failure = {
        "kind": "FAILURE",
        "run_id": failed_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "codex",
        "base_sha": base_sha,
        "failed_head_sha": failed_head_sha,
        "phase": "EXECUTION",
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["src/sample.py"],
            "outside_task_scope": [],
        },
    }
    run_path = state / "run.json"
    failure_path = state / "failure.json"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    transport_failure(
        repo,
        run_id=failed_run_id,
        head_sha=failed_head_sha,
        run_path=run_path,
        failure_path=failure_path,
    )
    failure_ref = f"refs/heads/aios/failure-artifacts/{failed_run_id}"
    failure_sha = git(repo, "ls-remote", "--refs", "origin", failure_ref).split()[0]

    predecessor = {
        "repair_id": "REPAIR-105-004",
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head_sha,
        "task": {"id": "TASK-105", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["src/sample.py"],
        "instructions": ["Continue the interrupted implementation."],
        "constraints": ["Bounded mutation authority only."],
    }
    first = historical_repair_fixture(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
            {"failed_run_id": failed_run_id},
            {"expected_failed_head_sha": failed_head_sha},
            predecessor,
        ),
        repo=repo,
    )
    successor = {
        **predecessor,
        "action": "FINALIZE_CANDIDATE",
        "modification_scope": [],
        "instructions": ["Finalize the exact existing candidate without mutation."],
    }
    superseding_envelope = IngressEnvelope(
        "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
        {"failed_run_id": failed_run_id},
        {
            "expected_failed_head_sha": failed_head_sha,
            "expected_current_repair_sha": first.canonical_sha,
            "expected_failure_artifacts_sha": failure_sha,
        },
        successor,
    )
    second = historical_repair_fixture(superseding_envelope, repo=repo)
    current = resolve_remote_repair_authorization(repo, failed_run_id)

    assert second.canonical_destination.endswith(f"/{failed_run_id}/2")
    assert current.commit_sha == second.canonical_sha
    assert current.revision == 2
    assert current.predecessor_sha == first.canonical_sha
    assert json.loads(current.repair) == successor
    lifecycle = resolve_remote_task_lifecycle(
        repo, task_id="TASK-105", task_revision=1
    )
    assert lifecycle.repair_selectors == (
        (failed_run_id, second.canonical_sha, current.repair),
    )
    assert git(
        repo, "ls-remote", "--refs", "origin",
        f"refs/heads/aios/repair/{failed_run_id}",
    ).split()[0] == first.canonical_sha
    replay = execute_ingress(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
            {"failed_run_id": failed_run_id},
            {
                "expected_failed_head_sha": failed_head_sha,
                "expected_current_repair_sha": second.canonical_sha,
                "expected_failure_artifacts_sha": failure_sha,
            },
            successor,
        ),
        repo=repo,
    )
    assert replay.status == "IDEMPOTENT"
    assert replay.canonical_sha == second.canonical_sha

    stale = {**successor, "instructions": ["Different explicit intent."]}
    with pytest.raises(AuthoringIngressError, match="expected current REPAIR SHA"):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                {"failed_run_id": failed_run_id},
                superseding_envelope.expected_state,
                stale,
            ),
            repo=repo,
        )

    continuation = tmp_path / "continuation-author"
    git(tmp_path, "clone", "--quiet", str(remote), str(continuation))
    git(continuation, "switch", "--quiet", "main")
    git(continuation, "config", "user.name", "AIOS Test")
    git(continuation, "config", "user.email", "test@example.invalid")
    (continuation / ".ai" / "transport").mkdir(parents=True, exist_ok=True)
    (continuation / ".ai" / "transport" / "repair.json").write_text(
        json.dumps({"failed_run_id": failed_run_id}), encoding="utf-8"
    )
    git(continuation, "add", ".ai/transport/repair.json")
    git(continuation, "commit", "--quiet", "-m", "admitted continuation")
    git(
        continuation,
        "push",
        "--quiet",
        "origin",
        "HEAD:refs/heads/aios/artifacts/RUN-105-005",
    )
    with pytest.raises(AuthoringIngressError, match="continuation already exists"):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                {"failed_run_id": failed_run_id},
                {
                    "expected_failed_head_sha": failed_head_sha,
                    "expected_current_repair_sha": second.canonical_sha,
                    "expected_failure_artifacts_sha": failure_sha,
                },
                stale,
            ),
            repo=repo,
        )

    git(
        repo,
        "push",
        "--quiet",
        "origin",
        f"{second.canonical_sha}:refs/heads/aios/repair-supersession/{failed_run_id}/4",
    )
    with pytest.raises(ReviewTransportError, match="identity|discontinuous"):
        resolve_remote_repair_authorization(repo, failed_run_id)


# ===========================================================================
# AC7 & AC8: Concurrency, idempotency, destination derivation, authority separation
# ===========================================================================

def test_authority_separation_and_destination_derivation(tmp_path):
    repo, remote, base_sha = setup_test_repo(tmp_path)

    # Brain cannot specify custom destination path
    with pytest.raises(AuthoringIngressError, match="prohibited field detected"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "identity": {"task_id": "TASK-105"},
            "destination_path": "arbitrary/path.yaml",
            "expected_state": {"expected_main_sha": base_sha},
            "payload": TASK_105_SOURCE,
        })

    # Brain cannot inject git command
    with pytest.raises(AuthoringIngressError, match="prohibited field detected"):
        parse_envelope({
            "format": "AIOS_INGRESS_ENVELOPE",
            "version": 1,
            "operation": "AUTHOR_TASK",
            "identity": {"task_id": "TASK-105"},
            "git_command": "git reset --hard",
            "expected_state": {"expected_main_sha": base_sha},
            "payload": TASK_105_SOURCE,
        })


def test_author_repair_multi_generation_supersession_production_topology_ac1_to_ac7(tmp_path):
    repo, remote, base_sha = setup_test_repo(tmp_path)
    (repo / ".ai" / "tasks").mkdir(parents=True, exist_ok=True)
    (repo / ".ai" / "tasks" / "TASK-121.yaml").write_text(
        """task_id: TASK-121
revision: 1
goal: Repair interrupted implementation.
problem: Production failed candidate needs repair.
assumptions: []
scope:
  inspect: []
  modify: [src/sample.py]
non_goals: []
constraints:
  hard:
    - Bounded mutation authority only.
acceptance:
  - id: AC1
    condition: Candidate is repaired.
verification:
  required:
    - git diff --check
""",
        encoding="utf-8",
    )
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "sample.py").write_text("# initial implementation\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "failed candidate for RUN-121-004")
    failed_head_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    failed_run_id = "RUN-121-004"
    state = tmp_path / "supersession-state"
    state.mkdir()
    run = {
        "run_id": failed_run_id,
        "task": {"id": "TASK-121", "revision": 1},
        "executor": "codex",
        "base_sha": base_sha,
        "workspace": str(repo),
        "head_sha": failed_head_sha,
        "status": "ACTIVE",
    }
    failure = {
        "kind": "FAILURE",
        "run_id": failed_run_id,
        "task": {"id": "TASK-121", "revision": 1},
        "executor": "codex",
        "base_sha": base_sha,
        "failed_head_sha": failed_head_sha,
        "phase": "EXECUTION",
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["src/sample.py"],
            "outside_task_scope": [],
        },
    }
    run_path = state / "run.json"
    failure_path = state / "failure.json"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    transport_failure(
        repo,
        run_id=failed_run_id,
        head_sha=failed_head_sha,
        run_path=run_path,
        failure_path=failure_path,
    )
    failure_ref = f"refs/heads/aios/failure-artifacts/{failed_run_id}"
    failure_sha = git(repo, "ls-remote", "--refs", "origin", failure_ref).split()[0]

    # --- Revision 1: Legacy CONTINUE_IMPLEMENTATION ---
    rev1_payload = {
        "repair_id": "REPAIR-121-004",
        "failed_run_id": failed_run_id,
        "failed_head_sha": failed_head_sha,
        "task": {"id": "TASK-121", "revision": 1},
        "action": "CONTINUE_IMPLEMENTATION",
        "modification_scope": ["src/sample.py"],
        "instructions": ["Continue implementation from interrupted candidate."],
        "constraints": ["Bounded mutation authority only."],
    }
    rev1_res = historical_repair_fixture(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
            {"failed_run_id": failed_run_id},
            {"expected_failed_head_sha": failed_head_sha},
            rev1_payload,
        ),
        repo=repo,
    )
    assert rev1_res.status == "CANONICALIZED"
    assert rev1_res.canonical_destination == f"refs/heads/aios/repair/{failed_run_id}"
    rev1_sha = rev1_res.canonical_sha

    current_r1 = resolve_remote_repair_authorization(repo, failed_run_id)
    assert current_r1.revision == 1
    assert current_r1.commit_sha == rev1_sha

    # --- Revision 2: First supersession (CONTINUE_IMPLEMENTATION -> FINALIZE_CANDIDATE) ---
    rev2_payload = {
        **rev1_payload,
        "action": "FINALIZE_CANDIDATE",
        "modification_scope": [],
        "instructions": ["Finalize existing candidate without further mutation."],
    }
    rev2_res = historical_repair_fixture(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
            {"failed_run_id": failed_run_id},
            {
                "expected_failed_head_sha": failed_head_sha,
                "expected_current_repair_sha": rev1_sha,
                "expected_failure_artifacts_sha": failure_sha,
            },
            rev2_payload,
        ),
        repo=repo,
    )
    assert rev2_res.status == "CANONICALIZED"
    assert rev2_res.canonical_destination == f"refs/heads/aios/repair-supersession/{failed_run_id}/2"
    rev2_sha = rev2_res.canonical_sha

    # Verify revision 1 immutable record was preserved and not rewritten
    assert git(repo, "ls-remote", "--refs", "origin", f"refs/heads/aios/repair/{failed_run_id}").split()[0] == rev1_sha
    current_r2 = resolve_remote_repair_authorization(repo, failed_run_id)
    assert current_r2.revision == 2
    assert current_r2.commit_sha == rev2_sha
    assert current_r2.predecessor_sha == rev1_sha

    # Negative check AC3: Stale revision-1 selection after revision 2 exists fails closed
    with pytest.raises(AuthoringIngressError, match="expected current REPAIR SHA"):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                {"failed_run_id": failed_run_id},
                {
                    "expected_failed_head_sha": failed_head_sha,
                    "expected_current_repair_sha": rev1_sha,
                    "expected_failure_artifacts_sha": failure_sha,
                },
                {**rev2_payload, "instructions": ["Stale revision 1 intent."]},
            ),
            repo=repo,
        )

    # --- Revision 3: Second supersession (AC1: extending revision 2 to revision 3 without collision) ---
    rev3_payload = {
        **rev1_payload,
        "action": "FINALIZE_CANDIDATE",
        "modification_scope": [],
        "instructions": ["Finalize candidate with refined recovery intent."],
    }
    rev3_res = historical_repair_fixture(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
            {"failed_run_id": failed_run_id},
            {
                "expected_failed_head_sha": failed_head_sha,
                "expected_current_repair_sha": rev2_sha,
                "expected_failure_artifacts_sha": failure_sha,
            },
            rev3_payload,
        ),
        repo=repo,
    )
    assert rev3_res.status == "CANONICALIZED"
    assert rev3_res.canonical_destination == f"refs/heads/aios/repair-supersession/{failed_run_id}/3"
    rev3_sha = rev3_res.canonical_sha

    # --- AC2: Verify all 3 immutable records preserved, bindings intact, revision 3 sole current tip ---
    assert git(repo, "ls-remote", "--refs", "origin", f"refs/heads/aios/repair/{failed_run_id}").split()[0] == rev1_sha
    assert git(repo, "ls-remote", "--refs", "origin", f"refs/heads/aios/repair-supersession/{failed_run_id}/2").split()[0] == rev2_sha
    assert git(repo, "ls-remote", "--refs", "origin", f"refs/heads/aios/repair-supersession/{failed_run_id}/3").split()[0] == rev3_sha

    # Verify commit ancestry chain
    assert git(repo, "rev-parse", f"{rev3_sha}^") == rev2_sha
    assert git(repo, "rev-parse", f"{rev2_sha}^") == rev1_sha
    assert git(repo, "rev-parse", f"{rev1_sha}^") == failed_head_sha

    # Verify deterministic current tip resolution
    current_r3 = resolve_remote_repair_authorization(repo, failed_run_id)
    assert current_r3.revision == 3
    assert current_r3.commit_sha == rev3_sha
    assert current_r3.predecessor_sha == rev2_sha
    assert current_r3.failure_artifacts_sha == failure_sha
    assert json.loads(current_r3.repair) == rev3_payload

    lifecycle = resolve_remote_task_lifecycle(repo, task_id="TASK-121", task_revision=1)
    assert lifecycle.repair_selectors == ((failed_run_id, rev3_sha, current_r3.repair),)

    # --- AC6: Idempotent replay of exact current revision 3 ---
    replay_r3 = execute_ingress(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
            {"failed_run_id": failed_run_id},
            {
                "expected_failed_head_sha": failed_head_sha,
                "expected_current_repair_sha": rev3_sha,
                "expected_failure_artifacts_sha": failure_sha,
            },
            rev3_payload,
        ),
        repo=repo,
    )
    assert replay_r3.status == "IDEMPOTENT"
    assert replay_r3.canonical_sha == rev3_sha

    # --- AC3: Stale selector rejection after revision 3 exists ---
    # Attempting to supersede using revision 1 SHA
    with pytest.raises(AuthoringIngressError, match="expected current REPAIR SHA"):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                {"failed_run_id": failed_run_id},
                {
                    "expected_failed_head_sha": failed_head_sha,
                    "expected_current_repair_sha": rev1_sha,
                    "expected_failure_artifacts_sha": failure_sha,
                },
                {**rev3_payload, "instructions": ["Stale revision 1 intent."]},
            ),
            repo=repo,
        )

    # Attempting to supersede using revision 2 SHA
    with pytest.raises(AuthoringIngressError, match="expected current REPAIR SHA"):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                {"failed_run_id": failed_run_id},
                {
                    "expected_failed_head_sha": failed_head_sha,
                    "expected_current_repair_sha": rev2_sha,
                    "expected_failure_artifacts_sha": failure_sha,
                },
                {**rev3_payload, "instructions": ["Stale revision 2 intent."]},
            ),
            repo=repo,
        )

    # Replaying superseded revision 2 fails closed
    with pytest.raises(AuthoringIngressError, match="expected current REPAIR SHA"):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                {"failed_run_id": failed_run_id},
                {
                    "expected_failed_head_sha": failed_head_sha,
                    "expected_current_repair_sha": rev2_sha,
                    "expected_failure_artifacts_sha": failure_sha,
                },
                rev2_payload,
            ),
            repo=repo,
        )

    # --- AC4: Operator / Preflight / Execution binding binds only revision 3 ---
    from aios_renew.operator import OperatorError, preflight_repair, run_repair

    # Stale revision 1 selector fails closed in preflight
    pf_r1 = preflight_repair(failed_run_id, repo=repo, required_repair_sha=rev1_sha)
    assert pf_r1.status == "BLOCKED"
    assert pf_r1.reason_code == "CANONICAL_LINEAGE_INVALID"

    # Stale revision 2 selector fails closed in preflight
    pf_r2 = preflight_repair(failed_run_id, repo=repo, required_repair_sha=rev2_sha)
    assert pf_r2.status == "BLOCKED"
    assert pf_r2.reason_code == "CANONICAL_LINEAGE_INVALID"

    # Exact current revision 3 succeeds in preflight
    pf_r3 = preflight_repair(failed_run_id, repo=repo, required_repair_sha=rev3_sha)
    assert pf_r3.status == "READY"
    assert pf_r3.authorization_sha == rev3_sha

    # Direct run_repair execution fails closed on stale revision 1 or revision 2 selector
    with pytest.raises(OperatorError, match="canonical REPAIR selector changed after observation"):
        run_repair(failed_run_id=failed_run_id, repo=repo, required_repair_sha=rev1_sha, executor="codex")

    with pytest.raises(OperatorError, match="canonical REPAIR selector changed after observation"):
        run_repair(failed_run_id=failed_run_id, repo=repo, required_repair_sha=rev2_sha, executor="codex")

    # --- AC3: Negative lineage cases ---
    # Case A: Revision discontinuity (gap: commit with revision 5 pushed to /5, skipping revision 4)
    gap_meta = {
        "format": "AIOS_REPAIR_SUPERSESSION",
        "version": 1,
        "failed_run_id": failed_run_id,
        "authorization_revision": 5,
        "predecessor_repair_sha": rev3_sha,
        "failure_artifacts_sha": failure_sha,
    }
    git(repo, "checkout", "--quiet", "--detach", rev3_sha)
    (repo / ".ai" / "transport" / "repair-supersession.json").write_text(json.dumps(gap_meta), encoding="utf-8")
    git(repo, "add", ".ai/transport/repair-supersession.json")
    git(repo, "commit", "--quiet", "-m", "discontinuous revision 5")
    gap_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", f"{gap_sha}:refs/heads/aios/repair-supersession/{failed_run_id}/5")
    git(repo, "checkout", "--quiet", "main")
    with pytest.raises(ReviewTransportError, match="discontinuous"):
        resolve_remote_repair_authorization(repo, failed_run_id, remote=str(remote))
    git(repo, "push", "--quiet", "origin", f":refs/heads/aios/repair-supersession/{failed_run_id}/5")

    # Case B: Competing successor / ambiguous selector
    git(repo, "push", "--quiet", "origin", f"{rev3_sha}:refs/heads/aios/repair-supersession/{failed_run_id}/03")
    with pytest.raises(ReviewTransportError, match="ambiguous|malformed|identity"):
        resolve_remote_repair_authorization(repo, failed_run_id, remote=str(remote))
    git(repo, "push", "--quiet", "origin", f":refs/heads/aios/repair-supersession/{failed_run_id}/03")

    # Case C: Broken predecessor binding in successor metadata
    broken_meta = {
        "format": "AIOS_REPAIR_SUPERSESSION",
        "version": 1,
        "failed_run_id": failed_run_id,
        "authorization_revision": 4,
        "predecessor_repair_sha": rev1_sha,
        "failure_artifacts_sha": failure_sha,
    }
    git(repo, "checkout", "--quiet", "--detach", rev1_sha)
    (repo / ".ai" / "transport" / "repair-supersession.json").write_text(json.dumps(broken_meta), encoding="utf-8")
    git(repo, "add", ".ai/transport/repair-supersession.json")
    git(repo, "commit", "--quiet", "-m", "broken revision 4")
    broken_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", f"{broken_sha}:refs/heads/aios/repair-supersession/{failed_run_id}/4")
    git(repo, "checkout", "--quiet", "main")
    with pytest.raises(ReviewTransportError, match="predecessor chain is broken"):
        resolve_remote_repair_authorization(repo, failed_run_id, remote=str(remote))
    git(repo, "push", "--quiet", "origin", f":refs/heads/aios/repair-supersession/{failed_run_id}/4")

    # Case D: Stale canonical FAILURE identity
    stale_failure_meta = {
        "format": "AIOS_REPAIR_SUPERSESSION",
        "version": 1,
        "failed_run_id": failed_run_id,
        "authorization_revision": 4,
        "predecessor_repair_sha": rev3_sha,
        "failure_artifacts_sha": "0" * 40,
    }
    git(repo, "checkout", "--quiet", "--detach", rev3_sha)
    (repo / ".ai" / "transport" / "repair-supersession.json").write_text(json.dumps(stale_failure_meta), encoding="utf-8")
    git(repo, "add", ".ai/transport/repair-supersession.json")
    git(repo, "commit", "--quiet", "-m", "stale failure revision 4")
    stale_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", f"{stale_sha}:refs/heads/aios/repair-supersession/{failed_run_id}/4")
    git(repo, "checkout", "--quiet", "main")
    with pytest.raises(ReviewTransportError, match="canonical REPAIR FAILURE identity is stale"):
        resolve_remote_repair_authorization(repo, failed_run_id, remote=str(remote))
    git(repo, "push", "--quiet", "origin", f":refs/heads/aios/repair-supersession/{failed_run_id}/4")

    # --- Continuation admission refusal ---
    continuation = tmp_path / "continuation-worker"
    git(tmp_path, "clone", "--quiet", str(remote), str(continuation))
    git(continuation, "switch", "--quiet", "main")
    git(continuation, "config", "user.name", "AIOS Test")
    git(continuation, "config", "user.email", "test@example.invalid")
    (continuation / ".ai" / "transport").mkdir(parents=True, exist_ok=True)
    (continuation / ".ai" / "transport" / "repair.json").write_text(
        json.dumps({"failed_run_id": failed_run_id}), encoding="utf-8"
    )
    git(continuation, "add", ".ai/transport/repair.json")
    git(continuation, "commit", "--quiet", "-m", "continuation admitted")
    git(continuation, "push", "--quiet", "origin", f"HEAD:refs/heads/aios/artifacts/RUN-121-005")

    with pytest.raises(AuthoringIngressError, match="continuation already exists"):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REPAIR",
                {"failed_run_id": failed_run_id},
                {
                    "expected_failed_head_sha": failed_head_sha,
                    "expected_current_repair_sha": rev3_sha,
                    "expected_failure_artifacts_sha": failure_sha,
                },
                {**rev3_payload, "instructions": ["Attempting supersession after continuation."]},
            ),
            repo=repo,
        )


def test_author_repair_failed_remediation_production_topology_ac1_to_ac6(tmp_path):
    repo, remote, base_sha = setup_test_repo(tmp_path)
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "TASK-105.yaml").write_text(TASK_105_SOURCE, encoding="utf-8")
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "sample.py").write_text("# original sample\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "base commit with task and sample")
    base_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    source_run_id = "RUN-105-001"
    review_id = "REVIEW-105-001"
    finding_id = "F1"
    remediation_run_id = "RUN-105-002"

    # Failed remediation candidate commit
    (repo / "src" / "sample.py").write_text("# broken remediation attempt\n", encoding="utf-8")
    git(repo, "add", "src/sample.py")
    git(repo, "commit", "--quiet", "-m", "failed remediation head candidate")
    failed_head_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    state = tmp_path / "remediation_state"
    state.mkdir(parents=True, exist_ok=True)

    embedded_run = {
        "run_id": remediation_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "antigravity",
        "base_sha": base_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    execution_record = {
        "review_id": review_id,
        "finding": {
            "id": finding_id,
            "basis": "AC1",
            "action": "CODE_FIX",
            "location": "src/sample.py",
            "issue": "Remediation needed for sample.",
            "expected": "Corrected sample.",
        },
        "remediation": {
            "finding_id": finding_id,
            "action": "CODE_FIX",
            "reviewed_sha": base_sha,
            "modification_scope": ["src/sample.py"],
            "affected_verification": ["git diff --check"],
            "constraints": ["Bounded mutation authority only."],
        },
        "run": embedded_run,
        "original_constraints": ["Bounded mutation authority only."],
    }
    predecessor_record = {
        "source_run_id": source_run_id,
        "review_id": review_id,
        "finding_id": finding_id,
        "reviewed_sha": base_sha,
    }
    remediation_run_payload = {
        "kind": "REMEDIATION",
        "predecessor": predecessor_record,
        "execution": execution_record,
    }
    failure_payload = {
        "kind": "FAILURE",
        "run_id": remediation_run_id,
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "antigravity",
        "base_sha": base_sha,
        "failed_head_sha": failed_head_sha,
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["src/sample.py"],
            "outside_task_scope": [],
        },
    }

    run_path = state / "run.json"
    failure_path = state / "failure.json"
    run_path.write_text(json.dumps(remediation_run_payload), encoding="utf-8")
    failure_path.write_text(json.dumps(failure_payload), encoding="utf-8")

    transport_failure(
        repo,
        run_id=remediation_run_id,
        head_sha=failed_head_sha,
        run_path=run_path,
        failure_path=failure_path,
    )

    repair_payload = {
        "repair_id": "REPAIR-105-002",
        "failed_run_id": remediation_run_id,
        "failed_head_sha": failed_head_sha,
        "task": {"id": "TASK-105", "revision": 1},
        "action": "CODE_FIX",
        "modification_scope": ["src/sample.py"],
        "instructions": ["Fix the broken remediation candidate."],
        "constraints": ["Bounded mutation authority only."],
    }
    envelope = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REPAIR",
        identity={"failed_run_id": remediation_run_id},
        expected_state={"expected_failed_head_sha": failed_head_sha},
        payload=repair_payload,
    )

    # AC1 & AC2: Author repair succeeds for failed REMEDIATION, removing the historical blocker
    result = historical_repair_fixture(envelope, repo=repo)
    assert result.status == "CANONICALIZED"
    assert result.canonical_destination == f"refs/heads/aios/repair/{remediation_run_id}"
    assert_exact_metadata_delta(
        repo,
        result.canonical_sha,
        failed_head_sha,
        ".ai/transport/repair.json",
        json.dumps(
            repair_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"),
    )

    # Idempotent replay
    replay = execute_ingress(envelope, repo=repo)
    assert replay.status == "IDEMPOTENT"
    assert replay.replayed is True

    # Lineage preservation: failure-artifacts ref preserves exact REMEDIATION wrapper and FAILURE identity
    artifacts_ref = f"refs/heads/aios/failure-artifacts/{remediation_run_id}"
    artifacts_sha = git(repo, "ls-remote", "--refs", "origin", artifacts_ref).split()[0]
    preserved_run_raw = git(repo, "show", f"{artifacts_sha}:.ai/transport/run.json")
    preserved_run = json.loads(preserved_run_raw)
    assert preserved_run["kind"] == "REMEDIATION"
    assert preserved_run["predecessor"]["source_run_id"] == source_run_id
    assert preserved_run["predecessor"]["finding_id"] == finding_id
    assert preserved_run["execution"]["run"]["run_id"] == remediation_run_id
    assert preserved_run["execution"]["run"]["base_sha"] == base_sha

    # AC4: Multi-generation supersession on failed remediation (Revision 2)
    rev2_payload = {
        **repair_payload,
        "repair_id": "REPAIR-105-002-R2",
        "instructions": ["Updated instructions for revision 2."],
    }
    rev2_env = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_REPAIR",
        identity={"failed_run_id": remediation_run_id},
        expected_state={
            "expected_failed_head_sha": failed_head_sha,
            "expected_current_repair_sha": result.canonical_sha,
            "expected_failure_artifacts_sha": artifacts_sha,
        },
        payload=rev2_payload,
    )
    rev2_res = historical_repair_fixture(rev2_env, repo=repo)
    assert rev2_res.status == "CANONICALIZED"
    assert rev2_res.canonical_destination == f"refs/heads/aios/repair-supersession/{remediation_run_id}/2"

    current = resolve_remote_repair_authorization(repo, remediation_run_id)
    assert current.revision == 2
    assert current.commit_sha == rev2_res.canonical_sha


def test_author_repair_failed_remediation_malformed_negatives(tmp_path):
    repo, remote, base_sha = setup_test_repo(tmp_path)
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "TASK-105.yaml").write_text(TASK_105_SOURCE, encoding="utf-8")
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "sample.py").write_text("# original\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "base commit")
    base_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    (repo / "src" / "sample.py").write_text("# failed\n", encoding="utf-8")
    git(repo, "add", "src/sample.py")
    git(repo, "commit", "--quiet", "-m", "failed candidate")
    failed_head_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    state = tmp_path / "neg_state"
    state.mkdir(parents=True, exist_ok=True)
    run_path = state / "run.json"
    failure_path = state / "failure.json"

    def build_failure(run_id: str, *, failure_base: str = base_sha, fail_task: dict | None = None) -> dict:
        payload = {
            "kind": "FAILURE",
            "run_id": run_id,
            "task": fail_task or {"id": "TASK-105", "revision": 1},
            "executor": "antigravity",
            "base_sha": failure_base,
            "failed_head_sha": failed_head_sha,
            "candidate": {
                "transportable": True,
                "repairable": True,
                "dirty": False,
                "descends_from_base": True,
                "changed_files": ["src/sample.py"],
                "outside_task_scope": [],
            },
        }
        return payload

    repair_payload = {
        "repair_id": "REPAIR-105-001",
        "failed_run_id": "RUN-105-NEG",
        "failed_head_sha": failed_head_sha,
        "task": {"id": "TASK-105", "revision": 1},
        "action": "CODE_FIX",
        "modification_scope": ["src/sample.py"],
        "instructions": ["Fix."],
        "constraints": ["Bounded."],
    }

    def try_repair(run_doc: dict, fail_doc: dict, run_id: str = "RUN-105-NEG") -> None:
        run_path.write_text(json.dumps(run_doc), encoding="utf-8")
        failure_path.write_text(json.dumps(fail_doc), encoding="utf-8")
        transport_failure(repo, run_id=run_id, head_sha=failed_head_sha, run_path=run_path, failure_path=failure_path)
        payload = {**repair_payload, "failed_run_id": run_id}
        env = IngressEnvelope(
            format="AIOS_INGRESS_ENVELOPE",
            version=1,
            operation="AUTHOR_REPAIR",
            identity={"failed_run_id": run_id},
            expected_state={"expected_failed_head_sha": failed_head_sha},
            payload=payload,
        )
        execute_ingress(env, repo=repo)

    # 1. Missing execution in REMEDIATION wrapper
    with pytest.raises(AuthoringIngressError, match="canonical REMEDIATION execution must be a mapping"):
        try_repair({"kind": "REMEDIATION"}, build_failure("RUN-105-N01"), "RUN-105-N01")

    # 2. Non-mapping execution in REMEDIATION wrapper
    with pytest.raises(AuthoringIngressError, match="canonical REMEDIATION execution must be a mapping"):
        try_repair({"kind": "REMEDIATION", "execution": "not-a-map"}, build_failure("RUN-105-N02"), "RUN-105-N02")

    # 3. Missing run in REMEDIATION.execution
    with pytest.raises(AuthoringIngressError, match="canonical REMEDIATION execution.run must be a mapping"):
        try_repair({"kind": "REMEDIATION", "execution": {}}, build_failure("RUN-105-N03"), "RUN-105-N03")

    # 4. Non-mapping run in REMEDIATION.execution
    with pytest.raises(AuthoringIngressError, match="canonical REMEDIATION execution.run must be a mapping"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": 123}}, build_failure("RUN-105-N04"), "RUN-105-N04")

    # 5. REMEDIATION.execution.run run_id mismatch
    mismatch_run = {
        "run_id": "RUN-DIFFERENT",
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "antigravity",
        "base_sha": base_sha,
        "workspace": str(repo),
        "status": "ACTIVE",
    }
    with pytest.raises(AuthoringIngressError, match="RUN run_id mismatch"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": mismatch_run}}, build_failure("RUN-105-N05"), "RUN-105-N05")

    # 6. Missing base_sha in REMEDIATION.execution.run
    no_base_run = {
        "run_id": "RUN-105-N06",
        "task": {"id": "TASK-105", "revision": 1},
        "executor": "antigravity",
        "workspace": str(repo),
        "status": "ACTIVE",
    }
    with pytest.raises(AuthoringIngressError, match="RUN base_sha is invalid"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": no_base_run}}, build_failure("RUN-105-N06"), "RUN-105-N06")

    # 7. Invalid base_sha format (non-hex) in REMEDIATION.execution.run
    bad_base_run = {**no_base_run, "run_id": "RUN-105-N07", "base_sha": "not-a-valid-sha"}
    with pytest.raises(AuthoringIngressError, match="RUN base_sha is invalid"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": bad_base_run}}, build_failure("RUN-105-N07"), "RUN-105-N07")

    # 8. base_sha is not a canonical commit in git
    non_commit_run = {**no_base_run, "run_id": "RUN-105-N08", "base_sha": "0" * 40}
    with pytest.raises(AuthoringIngressError, match="RUN base_sha is not a canonical commit"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": non_commit_run}}, build_failure("RUN-105-N08", failure_base="0" * 40), "RUN-105-N08")

    # 9. Contradictory base_sha between FAILURE and RUN
    valid_base_run = {**no_base_run, "run_id": "RUN-105-N09", "base_sha": base_sha}
    with pytest.raises(AuthoringIngressError, match="FAILURE base_sha does not match RUN"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": valid_base_run}}, build_failure("RUN-105-N09", failure_base=failed_head_sha), "RUN-105-N09")

    # 10. Missing task in execution.run
    no_task_run = {
        "run_id": "RUN-105-N10",
        "executor": "antigravity",
        "base_sha": base_sha,
        "workspace": str(repo),
        "status": "ACTIVE",
    }
    with pytest.raises(AuthoringIngressError, match="cannot resolve task_id from RUN"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": no_task_run}}, build_failure("RUN-105-N10"), "RUN-105-N10")

    # 11. Invalid task revision in execution.run
    bad_rev_run = {
        "run_id": "RUN-105-N11",
        "task": {"id": "TASK-105", "revision": "not-an-int"},
        "executor": "antigravity",
        "base_sha": base_sha,
        "workspace": str(repo),
        "status": "ACTIVE",
    }
    with pytest.raises(AuthoringIngressError, match="cannot resolve task revision from RUN"):
        try_repair({"kind": "REMEDIATION", "execution": {"run": bad_rev_run}}, build_failure("RUN-105-N11"), "RUN-105-N11")

    # 12. Unknown RUN kind
    with pytest.raises(AuthoringIngressError, match="unknown canonical RUN kind"):
        try_repair({"kind": "UNSUPPORTED_WRAPPER"}, build_failure("RUN-105-N12"), "RUN-105-N12")

    # Verify no repair refs were created for any failed negative case
    for n in range(1, 13):
        nid = f"RUN-105-N{n:02d}"
        ref = f"refs/heads/aios/repair/{nid}"
        output = git(repo, "ls-remote", "--refs", "origin", ref)
        assert not output.strip(), f"repair ref unexpectedly created for {nid}"


# ===========================================================================
# TASK-139 / Issue #208: Production-topology SUBMIT_REVIEW for antigravity-minimax
# ===========================================================================


def test_issue_208_submit_review_canonical_antigravity_minimax_run_succeeds(tmp_path):
    task_id = "TASK-138"
    run_id = "RUN-138-001"
    task_source = TASK_105_SOURCE.replace("TASK-105", task_id)

    lineage = setup_candidate_lineage(
        tmp_path,
        run_id=run_id,
        task_id=task_id,
        task_source=task_source,
        run_override={"executor": "antigravity-minimax"},
    )
    repo = lineage["repo"]
    candidate_sha = lineage["candidate_sha"]

    cr_review = f"""\
review_id: REVIEW-138-001
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance:
  AC1: FAIL
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: src/sample.py
    issue: TASK-138 candidate defect found during live review.
    expected: Fix defect.
"""
    envelope = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": run_id},
        expected_state={"expected_candidate_sha": candidate_sha},
        payload=cr_review,
    )
    res = execute_ingress(envelope, repo=repo)
    assert res.status == "CANONICALIZED"
    assert res.canonical_destination == f"refs/heads/aios/review-decision/{run_id}"
    assert_exact_metadata_delta(
        repo,
        res.canonical_sha,
        candidate_sha,
        ".ai/reviews/REVIEW-138-001.yaml",
        cr_review.encode("utf-8"),
    )

    obs = observe_unified_state(task_id, repo=repo)
    assert obs.lifecycle_state == "CORRECTION"
    assert obs.next_action == "AUTHOR_REMEDIATION"
    assert obs.finding_id == "F1"


def test_submit_review_unsupported_executor_fails_closed(tmp_path):
    lineage = setup_candidate_lineage(
        tmp_path,
        run_id="RUN-105-001",
        run_override={"executor": "unsupported-executor"},
    )
    repo = lineage["repo"]
    candidate_sha = lineage["candidate_sha"]

    pass_review = f"""\
review_id: REVIEW-105-001
reviewed_sha: {candidate_sha}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
"""
    envelope = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="SUBMIT_REVIEW",
        identity={"run_id": "RUN-105-001"},
        expected_state={"expected_candidate_sha": candidate_sha},
        payload=pass_review,
    )
    with pytest.raises(AuthoringIngressError, match="executor"):
        execute_ingress(envelope, repo=repo)


def test_submit_review_antigravity_minimax_in_clean_process_without_adapter_import(tmp_path):
    import sys

    task_id = "TASK-138"
    run_id = "RUN-138-001"
    task_source = TASK_105_SOURCE.replace("TASK-105", task_id)

    lineage = setup_candidate_lineage(
        tmp_path,
        run_id=run_id,
        task_id=task_id,
        task_source=task_source,
        run_override={"executor": "antigravity-minimax"},
    )
    repo = lineage["repo"]
    candidate_sha = lineage["candidate_sha"]

    code = f"""
import sys
from pathlib import Path

# Verify adapter has not been imported
assert "aios_renew.antigravity_minimax_adapter" not in sys.modules

from aios_renew.authoring_ingress import IngressEnvelope, execute_ingress

repo = Path({str(repo)!r})
candidate_sha = {candidate_sha!r}
run_id = {run_id!r}

review_yaml = f\"\"\"\\
review_id: REVIEW-138-001
reviewed_sha: {{candidate_sha}}
mode: PRIMARY
verdict: PASS
acceptance:
  AC1: PASS
findings: []
\"\"\"

envelope = IngressEnvelope(
    format="AIOS_INGRESS_ENVELOPE",
    version=1,
    operation="SUBMIT_REVIEW",
    identity={{"run_id": run_id}},
    expected_state={{"expected_candidate_sha": candidate_sha}},
    payload=review_yaml,
)

res = execute_ingress(envelope, repo=repo)
assert res.status == "CANONICALIZED"

# Prove the adapter module was never imported
assert "aios_renew.antigravity_minimax_adapter" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0


def _integrated_remediation_repair_review_lineage(
    root: Path,
    *,
    execution_base_mutation: str | None = None,
) -> dict[str, object]:
    """Build RUN-140-008 -> RUN-140-009 -> REPAIR using canonical transports."""

    from aios_renew.correction_integration import integrate_correction

    repo, remote, _ = setup_test_repo(root, task_id="TASK-140")
    task_id = "TASK-140"
    source_run_id = "RUN-140-008"
    remediation_run_id = "RUN-140-009"
    repair_run_id = "RUN-140-010"
    task_source = TASK_105_SOURCE.replace("TASK-105", task_id)
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / f"{task_id}.yaml").write_text(task_source, encoding="utf-8")
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "sample.py").write_text("# reviewed\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "TASK-140 reviewed candidate")
    reviewed_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")

    state = root / "integrated-review-state"
    state.mkdir()
    source_run = {
        "run_id": source_run_id,
        "task": {"id": task_id, "revision": 1},
        "executor": "codex",
        "base_sha": reviewed_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    source_result = {
        "result": {
            "head_sha": reviewed_sha,
            "claims": [{
                "id": "C1",
                "satisfies": ["AC1"],
                "claim": "The source candidate was reviewed.",
                "evidence": ["E1"],
            }],
            "changed_files": [],
            "unresolved": [],
        },
        "evidence": [{
            "evidence_id": "E1",
            "run_id": source_run_id,
            "subject_sha": reviewed_sha,
            "type": "TEST",
            "source": {"command": "git diff --check"},
            "result": {"exit_code": 0, "summary": "clean"},
            "raw": {"path": ".ai/evidence/E1.log"},
        }],
    }
    source_run_path = state / "source-run.json"
    source_result_path = state / "source-result.json"
    source_run_path.write_text(json.dumps(source_run), encoding="utf-8")
    source_result_path.write_text(json.dumps(source_result), encoding="utf-8")
    transport_post_pass(
        repo,
        run_id=source_run_id,
        head_sha=reviewed_sha,
        run_path=source_run_path,
        result_path=source_result_path,
    )

    review_source = f"""review_id: REVIEW-140-008
reviewed_sha: {reviewed_sha}
mode: PRIMARY
verdict: CHANGES_REQUIRED
acceptance:
  AC1: FAIL
findings:
  - id: F1
    basis: AC1
    action: CODE_FIX
    location: src/sample.py
    issue: The reviewed sample needs correction.
    expected: Correct the sample.
"""
    review_dir = repo / ".ai" / "reviews"
    remediation_dir = repo / ".ai" / "remediations"
    review_dir.mkdir(parents=True, exist_ok=True)
    remediation_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "REVIEW-140-008.yaml").write_text(review_source, encoding="utf-8")
    (remediation_dir / "F1.yaml").write_text(
        f"""finding_id: F1
action: CODE_FIX
reviewed_sha: {reviewed_sha}
modification_scope: [src/sample.py]
affected_verification: [git diff --check]
constraints: [Bounded mutation authority only.]
""",
        encoding="utf-8",
    )
    git(repo, "add", ".ai")
    git(repo, "commit", "--quiet", "-m", "TASK-140 review and remediation")
    git(
        repo,
        "push",
        "--quiet",
        "origin",
        f"HEAD:refs/heads/aios/remediation/{source_run_id}-F1",
    )

    git(repo, "reset", "--hard", "--quiet", reviewed_sha)
    (repo / "UNRELATED.txt").write_text("authorized main advance\n", encoding="utf-8")
    git(repo, "add", "UNRELATED.txt")
    git(repo, "commit", "--quiet", "-m", "advance authorized main")
    authorized_main_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")
    integration = integrate_correction(
        task_id,
        task_revision=1,
        cumulative_tip_run_id=source_run_id,
        cumulative_tip_candidate_sha=reviewed_sha,
        authorized_main_sha=authorized_main_sha,
        repo=repo,
    )
    git(
        repo,
        "push",
        "--quiet",
        "origin",
        f"{integration.integration_candidate_sha}:{integration.integration_ref}",
    )

    git(repo, "reset", "--hard", "--quiet", integration.integration_candidate_sha)
    (repo / "src" / "sample.py").write_text("# failed remediation\n", encoding="utf-8")
    git(repo, "add", "src/sample.py")
    git(repo, "commit", "--quiet", "-m", "failed integrated remediation")
    failed_head_sha = git(repo, "rev-parse", "HEAD")
    execution_base = {
        "version": 1,
        "kind": "INTEGRATED",
        "cumulative_tip_run_id": source_run_id,
        "cumulative_tip_candidate_sha": reviewed_sha,
        "authorized_main_sha": authorized_main_sha,
        "integration_candidate_sha": integration.integration_candidate_sha,
        "integration_id": integration.integration_id,
    }
    if execution_base_mutation == "authorized_main":
        execution_base["authorized_main_sha"] = reviewed_sha
    elif execution_base_mutation == "integration_candidate":
        execution_base["integration_candidate_sha"] = failed_head_sha

    remediation_run = {
        "run_id": remediation_run_id,
        "task": {"id": task_id, "revision": 1},
        "executor": "codex",
        "base_sha": integration.integration_candidate_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    remediation_run_payload = {
        "kind": "REMEDIATION",
        "predecessor": {
            "source_run_id": source_run_id,
            "review_id": "REVIEW-140-008",
            "finding_id": "F1",
            "reviewed_sha": reviewed_sha,
        },
        "execution_base": execution_base,
        "execution": {
            "review_id": "REVIEW-140-008",
            "finding": {
                "id": "F1",
                "basis": "AC1",
                "action": "CODE_FIX",
                "location": "src/sample.py",
                "issue": "The reviewed sample needs correction.",
                "expected": "Correct the sample.",
            },
            "remediation": {
                "finding_id": "F1",
                "action": "CODE_FIX",
                "reviewed_sha": reviewed_sha,
                "modification_scope": ["src/sample.py"],
                "affected_verification": ["git diff --check"],
                "constraints": ["Bounded mutation authority only."],
            },
            "run": remediation_run,
            "original_constraints": ["Bounded mutation authority only."],
        },
    }
    failure = {
        "kind": "FAILURE",
        "run_id": remediation_run_id,
        "task": {"id": task_id, "revision": 1},
        "executor": "codex",
        "base_sha": integration.integration_candidate_sha,
        "failed_head_sha": failed_head_sha,
        "phase": "VERIFICATION",
        "error": {"type": "RuntimeVerificationError", "message": "failed"},
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": False,
            "descends_from_base": True,
            "changed_files": ["src/sample.py"],
            "outside_task_scope": [],
        },
    }
    remediation_run_path = state / "remediation-run.json"
    failure_path = state / "failure.json"
    remediation_run_path.write_text(json.dumps(remediation_run_payload), encoding="utf-8")
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    transport_failure(
        repo,
        run_id=remediation_run_id,
        head_sha=failed_head_sha,
        run_path=remediation_run_path,
        failure_path=failure_path,
    )

    repair = {
        "repair_id": "REPAIR-140-009",
        "failed_run_id": remediation_run_id,
        "failed_head_sha": failed_head_sha,
        "task": {"id": task_id, "revision": 1},
        "action": "CODE_FIX",
        "modification_scope": ["src/sample.py"],
        "instructions": ["Repair the failed integrated remediation."],
        "constraints": ["Bounded mutation authority only."],
    }
    # Invalid integrated lineage is historical input to Reviewer regression
    # tests; prospective authoring must reject it rather than acquire a flow.
    author = historical_repair_fixture if execution_base_mutation else execute_audited_ingress
    authorization = author(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE",
            1,
            "AUTHOR_REPAIR",
            {"failed_run_id": remediation_run_id},
            {"expected_failed_head_sha": failed_head_sha},
            repair,
        ),
        repo=repo,
    )

    (repo / "src" / "sample.py").write_text("# repaired remediation\n", encoding="utf-8")
    git(repo, "add", "src/sample.py")
    git(repo, "commit", "--quiet", "-m", "repair integrated remediation")
    repaired_sha = git(repo, "rev-parse", "HEAD")
    repair_run = {
        "run_id": repair_run_id,
        "task": {"id": task_id, "revision": 1},
        "executor": "codex",
        "base_sha": failed_head_sha,
        "workspace": str(repo),
        "head_sha": None,
        "status": "ACTIVE",
    }
    repair_lineage = {
        "failed_run_id": remediation_run_id,
        "root_base_sha": reviewed_sha,
        "result_base_sha": integration.integration_candidate_sha,
        "failed_head_sha": failed_head_sha,
        "failure": failure,
        "task": {"task_id": task_id, "revision": 1},
        "repair": repair,
        "repair_authorization_sha": authorization.canonical_sha,
        "run": repair_run,
    }
    repair_result = {
        "result": {
            "head_sha": repaired_sha,
            "claims": [{
                "id": "C1",
                "satisfies": ["AC1"],
                "claim": "The integrated remediation was repaired.",
                "evidence": ["E1"],
            }],
            "changed_files": ["src/sample.py"],
            "unresolved": [],
        },
        "evidence": [{
            "evidence_id": "E1",
            "run_id": repair_run_id,
            "subject_sha": repaired_sha,
            "type": "TEST",
            "source": {"command": "git diff --check"},
            "result": {"exit_code": 0, "summary": "clean"},
            "raw": {"path": ".ai/evidence/E1.log"},
        }],
    }
    repair_run_path = state / "repair-run.json"
    repair_result_path = state / "repair-result.json"
    repair_lineage_path = state / "repair-lineage.json"
    repair_run_path.write_text(json.dumps(repair_run), encoding="utf-8")
    repair_result_path.write_text(json.dumps(repair_result), encoding="utf-8")
    repair_lineage_path.write_text(json.dumps(repair_lineage), encoding="utf-8")
    transport_post_pass(
        repo,
        run_id=repair_run_id,
        head_sha=repaired_sha,
        run_path=repair_run_path,
        result_path=repair_result_path,
        lineage_path=repair_lineage_path,
    )
    return {
        "repo": repo,
        "remote": remote,
        "run_id": repair_run_id,
        "reviewed_sha": reviewed_sha,
        "repaired_sha": repaired_sha,
        "failed_head_sha": failed_head_sha,
        "integration_candidate_sha": integration.integration_candidate_sha,
        "integration_ref": integration.integration_ref,
    }


def test_submit_review_reconstructs_exact_integrated_remediation_repair_delta(tmp_path):
    lineage = _integrated_remediation_repair_review_lineage(tmp_path)
    repo = lineage["repo"]
    repaired_sha = lineage["repaired_sha"]
    assert lineage["failed_head_sha"] != lineage["reviewed_sha"]
    assert lineage["integration_candidate_sha"] != lineage["reviewed_sha"]

    wrong_review = f"""review_id: REVIEW-140-010-WRONG
reviewed_sha: {repaired_sha}
mode: DELTA
verdict: PASS
prior_finding_id: F2
acceptance:
  AC1: PASS
findings: []
"""
    with pytest.raises(AuthoringIngressError, match="exact repaired DELTA finding"):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE",
                1,
                "SUBMIT_REVIEW",
                {"run_id": lineage["run_id"]},
                {"expected_candidate_sha": repaired_sha},
                wrong_review,
            ),
            repo=repo,
        )

    review = wrong_review.replace("REVIEW-140-010-WRONG", "REVIEW-140-010").replace(
        "prior_finding_id: F2", "prior_finding_id: F1"
    )
    result = execute_ingress(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE",
            1,
            "SUBMIT_REVIEW",
            {"run_id": lineage["run_id"]},
            {"expected_candidate_sha": repaired_sha},
            review,
        ),
        repo=repo,
    )
    assert result.status == "CANONICALIZED"
    assert_exact_metadata_delta(
        repo,
        result.canonical_sha,
        repaired_sha,
        ".ai/reviews/REVIEW-140-010.yaml",
        review.encode("utf-8"),
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_ref", "canonical ref .* is missing or ambiguous"),
        ("moved_ref", "remote integration ref does not match"),
        ("authorized_main", "integration_id does not match"),
        ("integration_candidate", "candidate SHA does not match RUN base_sha"),
    ],
)
def test_submit_review_rejects_forged_or_unavailable_integration_evidence(
    tmp_path, mutation, message
):
    execution_mutation = mutation if mutation in {"authorized_main", "integration_candidate"} else None
    lineage = _integrated_remediation_repair_review_lineage(
        tmp_path, execution_base_mutation=execution_mutation
    )
    remote = lineage["remote"]
    if mutation == "missing_ref":
        git(remote, "update-ref", "-d", lineage["integration_ref"])
    elif mutation == "moved_ref":
        git(
            remote,
            "update-ref",
            lineage["integration_ref"],
            lineage["failed_head_sha"],
        )
    review = f"""review_id: REVIEW-140-010
reviewed_sha: {lineage['repaired_sha']}
mode: DELTA
verdict: PASS
prior_finding_id: F1
acceptance:
  AC1: PASS
findings: []
"""
    with pytest.raises(AuthoringIngressError, match=message):
        execute_ingress(
            IngressEnvelope(
                "AIOS_INGRESS_ENVELOPE",
                1,
                "SUBMIT_REVIEW",
                {"run_id": lineage["run_id"]},
                {"expected_candidate_sha": lineage["repaired_sha"]},
                review,
            ),
            repo=lineage["repo"],
        )
