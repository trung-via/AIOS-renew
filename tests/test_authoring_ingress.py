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


@pytest.mark.parametrize("review_fault", ["old-sha", "delta", "main-moved"])
def test_publication_recovery_review_ingress_requires_fresh_exact_primary_and_current_main(tmp_path, monkeypatch, review_fault):
    from aios_renew import operator
    from tests.test_publication import recover_publication_fixture, review_source
    lineage, main, _, runner = recover_publication_fixture(tmp_path, monkeypatch)
    recovered = operator.recover_publication_source(lineage["run_id"], decision_sha=lineage["decision_sha"],
        expected_main_sha=main, repo=lineage["repo"], verification_runner=runner)
    reviewed = lineage["candidate_sha"] if review_fault == "old-sha" else recovered.head_sha
    payload = review_source(reviewed).replace("REVIEW-063-001", "REVIEW-" + recovered.run_id[4:])
    if review_fault == "delta":
        payload = payload.replace("mode: PRIMARY", "mode: DELTA\nprior_finding_id: fabricated")
    elif review_fault == "main-moved":
        (lineage["repo"] / "moved.txt").write_text("concurrent main\n", encoding="utf-8")
        from tests.test_publication import git
        git(lineage["repo"], "add", "moved.txt")
        git(lineage["repo"], "commit", "--quiet", "-m", "concurrent main")
        git(lineage["repo"], "push", "--quiet", "origin", "HEAD:refs/heads/main")
    from tests.test_publication import git
    before = git(lineage["remote"], "rev-parse", "refs/heads/main")
    with pytest.raises(AuthoringIngressError):
        execute_ingress(IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "SUBMIT_REVIEW",
            {"run_id": recovered.run_id}, {"expected_candidate_sha": recovered.head_sha}, payload), repo=lineage["repo"])
    assert git(lineage["remote"], "rev-parse", "refs/heads/main") == before
    assert git(lineage["remote"], "show-ref", "--verify", "--hash",
               f"refs/heads/aios/review-decision/{recovered.run_id}", check=False) == ""


def new_task_envelope(main_sha: str) -> IngressEnvelope:
    return IngressEnvelope(
        "AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_TASK", {"task_id": "TASK-105"},
        {"expected_main_sha": main_sha}, V2_TASK_105_SOURCE,
    )


def origin_authoring_fixture(tmp_path, repo, main_sha, *, route=None):
    from aios_renew import origin_authoring_proof as proofs, origin_bootstrap as origin
    from tests.test_origin_bootstrap import Adapter, URL_A
    registry = origin.OriginRegistry(tmp_path / "authoring-origin.json")
    result = origin.bootstrap(registry, Adapter(registry, route or URL_A))
    selector = dict(kind="ORIGIN_AFFINE", route_handle=result.route_handle, generation=result.generation)
    envelope = replace(new_task_envelope(main_sha),
        payload=V2_TASK_105_SOURCE.replace("{kind: LEGACY_REPOSITORY_DEFAULT_ROUTE}", json.dumps(selector)),
        origin_authoring_proof=result.authoring_proof)
    attempt = "github-issue:trung-via/AIOS-renew#107@trung-via/run:900/attempt:1"
    binding = proofs.binding_for(envelope, attempt)
    receipt = proofs.admit_local(registry, binding, "c" * 64)
    admission = proofs.authenticate_receipt(receipt, binding, "c" * 64)
    return registry, envelope, admission


@pytest.fixture
def origin_admission_key(monkeypatch):
    monkeypatch.setenv("AIOS_ORIGIN_ADMISSION_KEY", "c" * 64)
    monkeypatch.setenv("GITHUB_REPOSITORY", "trung-via/AIOS-renew")
    monkeypatch.setenv("GITHUB_RUN_ID", "900")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")


@pytest.mark.parametrize("route", ("A", "B"))
def test_origin_task_admitted_before_mutation_and_same_attempt_replay(tmp_path, route, origin_admission_key):
    from aios_renew import origin_authoring_proof as proofs
    from tests.test_origin_bootstrap import URL_A, URL_B
    repo, remote, main_sha = setup_test_repo(tmp_path)
    registry, envelope, admission = origin_authoring_fixture(tmp_path, repo, main_sha,
                                                           route=URL_A if route == "A" else URL_B)
    result = execute_ingress(envelope, repo=repo, origin_admission=admission)
    assert result.status == "CANONICALIZED"
    replay = execute_ingress(envelope, repo=repo, origin_admission=admission)
    assert replay.status == "IDEMPOTENT" and replay.canonical_sha == result.canonical_sha
    task = yaml.safe_load(git(repo, "show", f"{result.canonical_sha}:.ai/tasks/TASK-105.yaml"))
    assert task["return_affinity"]["route_handle"] == admission.receipt["route_handle"]
    assert task["return_affinity"]["generation"] == admission.receipt["generation"]
    assert not {"origin_authoring_proof", "origin_admission", "carrier_attempt", "signature"} & set(task)
    assert git(remote, "rev-parse", "refs/heads/main") == result.canonical_sha
    changed = proofs.binding_for(envelope, admission.receipt["carrier_attempt"].replace("attempt:1", "attempt:2"))
    with pytest.raises(proofs.OriginProofError):
        proofs.admit_local(registry, changed, "c" * 64)
    # Semantic equality on an existing rev-1 TASK does not bypass admission.
    with pytest.raises(AuthoringIngressError, match="admitted origin"):
        execute_ingress(envelope, repo=repo)


@pytest.mark.parametrize("fault", ("absent_admission", "payload_selector_only", "missing_proof",
                                    "copied_selector", "generation", "task", "main", "body", "stale",
                                    "cross_attempt", "missing_key", "forged_signature", "ambiguous_main"))
def test_origin_author_task_provenance_failure_precedes_all_git_mutation(tmp_path, monkeypatch, fault, origin_admission_key):
    from aios_renew import origin_authoring_proof as proofs
    repo, remote, main_sha = setup_test_repo(tmp_path)
    _, envelope, admission = origin_authoring_fixture(tmp_path, repo, main_sha)
    if fault in {"absent_admission", "payload_selector_only"}:
        admission = None
        if fault == "payload_selector_only":
            envelope = replace(envelope, origin_authoring_proof=None)
    elif fault == "missing_proof":
        envelope = replace(envelope, origin_authoring_proof=None)
    elif fault in {"copied_selector", "generation", "task", "body"}:
        body = yaml.safe_load(envelope.payload)
        if fault == "copied_selector":
            body["return_affinity"]["route_handle"] = "page-origin-v1:" + "0" * 64
        elif fault == "generation":
            body["return_affinity"]["generation"] = 2
        elif fault == "task":
            body["task_id"] = "TASK-106"
            envelope = replace(envelope, identity={"task_id": "TASK-106"})
        else:
            body["goal"] = "A substituted goal."
        envelope = replace(envelope, payload=body)
    elif fault == "main":
        envelope = replace(envelope, expected_state={"expected_main_sha": "0" * 40})
    elif fault == "ambiguous_main":
        envelope = replace(envelope, expected_state={"expected_main_sha": main_sha, "main_sha": "0" * 40})
    elif fault == "cross_attempt":
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    elif fault == "missing_key":
        monkeypatch.delenv("AIOS_ORIGIN_ADMISSION_KEY")
    elif fault == "forged_signature":
        receipt = dict(admission.receipt, signature="0" * 64)
        admission = proofs.AdmittedOrigin(json.dumps(receipt))
    else:
        monkeypatch.setattr(proofs, "_clock", lambda: admission.receipt["expires_at"])
    for name in ("_hash_blob", "_git_env", "_commit_tree", "_publish_ingress_ref"):
        monkeypatch.setattr(authoring_ingress_module, name, lambda *a, **k: pytest.fail("premature Git mutation"))
    with pytest.raises(AuthoringIngressError, match="origin"):
        execute_ingress(envelope, repo=repo, origin_admission=admission)
    assert git(repo, "rev-parse", "HEAD") == main_sha
    assert git(remote, "rev-parse", "refs/heads/main") == main_sha
    assert not (repo / ".ai/tasks/TASK-105.yaml").exists()


def test_origin_revision_preserves_selector_without_current_chat_proof(tmp_path, origin_admission_key):
    repo, _, main_sha = setup_test_repo(tmp_path)
    _, envelope, admission = origin_authoring_fixture(tmp_path, repo, main_sha)
    first = execute_ingress(envelope, repo=repo, origin_admission=admission)
    selector = yaml.safe_load(envelope.payload)["return_affinity"]
    revised = replace(envelope, expected_state={"expected_main_sha": first.canonical_sha},
        payload=V2_TASK_105_R2_SOURCE.replace("{kind: LEGACY_REPOSITORY_DEFAULT_ROUTE}", json.dumps(selector)),
        origin_authoring_proof=None, audited_handoff=None)
    # A new chat's proof is never an implicit ownership or generation transfer.
    with pytest.raises(AuthoringIngressError, match="revisions"):
        execute_ingress(replace(revised, origin_authoring_proof=envelope.origin_authoring_proof), repo=repo)
    for change in (dict(selector, generation=2), dict(selector, route_handle="page-origin-v1:" + "0" * 64),
                   {"kind": "LEGACY_REPOSITORY_DEFAULT_ROUTE"}):
        payload = dict(yaml.safe_load(revised.payload), return_affinity=change)
        with pytest.raises(AuthoringIngressError, match="transfer return_affinity"):
            execute_ingress(replace(revised, payload=payload), repo=repo)
    result = execute_ingress(revised, repo=repo)
    assert yaml.safe_load(git(repo, "show", f"{result.canonical_sha}:.ai/tasks/TASK-105.yaml"))["return_affinity"] == selector


@pytest.mark.parametrize("boundary", ("before_tree", "before_publication"))
def test_direct_origin_task_rechecks_provenance_during_mutation(
    tmp_path, monkeypatch, origin_admission_key, boundary
):
    from aios_renew import origin_authoring_proof as proofs

    repo, remote, main_sha = setup_test_repo(tmp_path)
    _, envelope, admission = origin_authoring_fixture(tmp_path, repo, main_sha)
    def expire():
        monkeypatch.setattr(proofs, "_clock", lambda: admission.receipt["expires_at"])
    if boundary == "before_tree":
        original = authoring_ingress_module._git
        def expire_after_status(repo, *args, **kwargs):
            result = original(repo, *args, **kwargs)
            if args == ("status", "--porcelain"):
                expire()
            return result
        monkeypatch.setattr(authoring_ingress_module, "_git", expire_after_status)
        monkeypatch.setattr(authoring_ingress_module, "_hash_blob", lambda *a: pytest.fail("premature blob mutation"))
    else:
        original = authoring_ingress_module._commit_tree
        def expire_after_commit(*args, **kwargs):
            commit = original(*args, **kwargs)
            expire()
            return commit
        monkeypatch.setattr(authoring_ingress_module, "_commit_tree", expire_after_commit)
    with pytest.raises(AuthoringIngressError, match="origin"):
        execute_ingress(envelope, repo=repo, origin_admission=admission)
    assert git(repo, "rev-parse", "HEAD") == main_sha
    assert git(remote, "rev-parse", "refs/heads/main") == main_sha
    assert not (repo / ".ai/tasks/TASK-105.yaml").exists()


def test_explicit_legacy_authoring_stays_separate_from_origin_proofs(tmp_path):
    repo, _, main_sha = setup_test_repo(tmp_path)
    legacy = new_task_envelope(main_sha)
    with pytest.raises(AuthoringIngressError, match="legacy"):
        execute_ingress(replace(legacy, origin_authoring_proof="origin-authoring-v1:" + "0" * 64), repo=repo)
    result = execute_ingress(legacy, repo=repo)
    assert yaml.safe_load(git(repo, "show", f"{result.canonical_sha}:.ai/tasks/TASK-105.yaml"))["return_affinity"] == {
        "kind": "LEGACY_REPOSITORY_DEFAULT_ROUTE"}


def test_origin_admission_is_not_an_envelope_or_task_authority_field():
    raw = {"format": "AIOS_INGRESS_ENVELOPE", "version": 1, "operation": "AUTHOR_TASK",
           "identity": {"task_id": "TASK-105"}, "expected_state": {"expected_main_sha": "a" * 40},
           "payload": V2_TASK_105_SOURCE, "origin_admission": {"status": "ADMITTED"}}
    with pytest.raises(AuthoringIngressError, match="unknown field"):
        parse_envelope(raw)
    del raw["origin_admission"]
    for invalid in (None, {}, "page-origin-v1:" + "a" * 64, "origin-authoring-v1:" + "a" * 65):
        with pytest.raises(AuthoringIngressError, match="origin_authoring_proof"):
            parse_envelope(dict(raw, origin_authoring_proof=invalid))


def test_h4c1_new_authoring_without_explicit_classification_is_non_mutating(tmp_path):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    envelope = new_task_envelope(main_sha)
    envelope = replace(envelope, payload=envelope.payload.replace(
        "return_affinity: {kind: LEGACY_REPOSITORY_DEFAULT_ROUTE}\n", ""))
    with pytest.raises(AuthoringIngressError, match="explicit return_affinity"):
        execute_ingress(envelope, repo=repo)
    assert git(repo, "rev-parse", "HEAD") == main_sha
    assert git(remote, "rev-parse", "refs/heads/main") == main_sha


@pytest.mark.parametrize("change", ["route", "generation", "legacy", "missing"])
def test_h4c1_revision_cannot_transfer_existing_affinity(change):
    from aios_renew.return_affinity import require_authored_affinity
    from aios_renew.task import parse_task
    selector = dict(kind="ORIGIN_AFFINE", route_handle="page-origin-v1:" + "a" * 64, generation=1)
    existing = parse_task(V2_TASK_105_SOURCE.replace(
        "return_affinity: {kind: LEGACY_REPOSITORY_DEFAULT_ROUTE}",
        "return_affinity: " + json.dumps(selector)))
    payload = yaml.safe_load(V2_TASK_105_R2_SOURCE)
    payload["return_affinity"] = dict(selector)
    require_authored_affinity(payload, existing)
    if change == "route":
        payload["return_affinity"]["route_handle"] = "page-origin-v1:" + "b" * 64
    elif change == "generation":
        payload["return_affinity"]["generation"] = 2
    elif change == "legacy":
        payload["return_affinity"] = {"kind": "LEGACY_REPOSITORY_DEFAULT_ROUTE"}
    else:
        del payload["return_affinity"]
    with pytest.raises(ValueError, match="return_affinity"):
        require_authored_affinity(payload, existing)


def test_author_task_without_handoff_has_no_audit_dependencies(tmp_path, monkeypatch):
    from aios_renew import brain_audit, brain_context, brain_sync, decision_packet

    repo, remote, main_sha = setup_test_repo(tmp_path)
    def retired_dependency(*args, **kwargs):
        pytest.fail("AUTHOR_TASK called retired Brain audit plumbing")
    for name in ("_validate_handoff_shape", "_validate_authoring_handoff", "_compose_authoring_packet",
                 "_recheck_authoring_binding", "_authoring_refs", "_prove_authoring_inputs"):
        monkeypatch.setattr(authoring_ingress_module, name, retired_dependency)
    for module, names in ((brain_audit, ("construct_stage1", "validate_stage2", "parse_profile_registry")),
                          (brain_sync, ("observe_brain_sync",)),
                          (brain_context, ("compose_brain_work_context", "resolve_flow")),
                          (decision_packet, ("compile_decision_packet",))):
        for name in names:
            monkeypatch.setattr(module, name, retired_dependency)
    envelope = parse_envelope({
        "format": "AIOS_INGRESS_ENVELOPE", "version": 1, "operation": "AUTHOR_TASK",
        "identity": {"task_id": "TASK-105"}, "expected_state": {"expected_main_sha": main_sha},
        "payload": V2_TASK_105_SOURCE,
    })
    first = execute_ingress(envelope, repo=repo)
    revised = replace(envelope, expected_state={"expected_main_sha": first.canonical_sha},
                      payload=V2_TASK_105_R2_SOURCE)
    second = execute_ingress(revised, repo=repo)
    assert first.status == second.status == "CANONICALIZED"
    assert git(remote, "rev-parse", "refs/heads/main") == second.canonical_sha
    assert execute_ingress(revised, repo=repo).replayed


@pytest.mark.parametrize("revision", (1, 2))
@pytest.mark.parametrize("fault", ("malformed", "identity", "policy_absent", "policy_v1",
                                    "empty_verification", "missing_affinity", "stale_main", "dirty_main"))
def test_direct_author_task_safety_failures_precede_mutation(tmp_path, monkeypatch, revision, fault):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    envelope = new_task_envelope(main_sha)
    if revision == 2:
        main_sha = execute_ingress(envelope, repo=repo).canonical_sha
        envelope = replace(envelope, expected_state={"expected_main_sha": main_sha}, payload=V2_TASK_105_R2_SOURCE)
    body = yaml.safe_load(envelope.payload)
    if fault == "malformed":
        del body["goal"]
    elif fault == "identity":
        body["task_id"] = "TASK-OTHER"
    elif fault == "policy_absent":
        del body["verification"]["policy"]
    elif fault == "policy_v1":
        body["verification"]["policy"] = "minimum-sufficient-v1"
    elif fault == "empty_verification":
        body["verification"]["required"] = []
    elif fault == "missing_affinity":
        del body["return_affinity"]
    elif fault == "stale_main":
        envelope = replace(envelope, expected_state={"expected_main_sha": "0" * 40})
    else:
        (repo / "README.md").write_text("dirty main\n", encoding="utf-8")
    for name in ("_hash_blob", "_git_env", "_commit_tree", "_publish_ingress_ref"):
        monkeypatch.setattr(authoring_ingress_module, name, lambda *a, **k: pytest.fail("premature Git mutation"))
    with pytest.raises(AuthoringIngressError):
        execute_ingress(replace(envelope, payload=body), repo=repo)
    assert git(repo, "rev-parse", "HEAD") == main_sha
    assert git(remote, "rev-parse", "refs/heads/main") == main_sha


@pytest.mark.parametrize("revision", (1, 2, "replay"))
@pytest.mark.parametrize("handoff", ({}, "legacy", {
    "format": "AIOS_AUDITED_AUTHORING_HANDOFF", "version": 1, "stage1": {}, "stage2": {},
}))
def test_author_task_rejects_any_handoff_before_git_mutation(tmp_path, monkeypatch, revision, handoff):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    envelope = new_task_envelope(main_sha)
    if revision in (2, "replay"):
        first = execute_ingress(envelope, repo=repo)
        main_sha = first.canonical_sha
        if revision == 2:
            envelope = replace(envelope, expected_state={"expected_main_sha": main_sha},
                               payload=V2_TASK_105_R2_SOURCE)
    for name in ("_validate_handoff_shape", "_hash_blob", "_git_env", "_commit_tree", "_publish_ingress_ref"):
        monkeypatch.setattr(authoring_ingress_module, name, lambda *a, **k: pytest.fail("premature mutation or audit"))
    with pytest.raises(AuthoringIngressError, match="AUTHOR_TASK does not accept audited_handoff"):
        execute_ingress(replace(envelope, audited_handoff=handoff), repo=repo)
    assert git(repo, "rev-parse", "HEAD") == main_sha
    assert git(remote, "rev-parse", "refs/heads/main") == main_sha


@pytest.mark.parametrize("handoff", (None, {}, "legacy", {
    "format": "AIOS_AUDITED_AUTHORING_HANDOFF", "version": 1, "stage1": {}, "stage2": {},
}))
def test_author_task_parser_rejects_supplied_handoff_even_null(handoff):
    raw = {"format": "AIOS_INGRESS_ENVELOPE", "version": 1, "operation": "AUTHOR_TASK",
           "identity": {"task_id": "TASK-105"}, "expected_state": {"expected_main_sha": "a" * 40},
           "payload": V2_TASK_105_SOURCE, "audited_handoff": handoff}
    with pytest.raises(AuthoringIngressError, match="AUTHOR_TASK does not accept audited_handoff"):
        parse_envelope(raw)


@pytest.mark.parametrize("extra", ("acceptance_phase_ledger", "phase", "cross_authority_context",
                                    "canonical_shape", "terminal_lifecycle", "TASK_AUTHORING"))
def test_author_task_rejects_audit_support_inside_frozen_payload(tmp_path, monkeypatch, extra):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    body = yaml.safe_load(V2_TASK_105_SOURCE)
    if extra == "phase":
        body["acceptance"][0][extra] = "CLAIM_NOW"
    else:
        body[extra] = []
    monkeypatch.setattr(authoring_ingress_module, "_hash_blob", lambda *a: pytest.fail("premature mutation"))
    with pytest.raises(AuthoringIngressError, match="invalid TASK contract"):
        execute_ingress(replace(new_task_envelope(main_sha), payload=body), repo=repo)
    assert git(remote, "rev-parse", "refs/heads/main") == main_sha


def test_direct_authoring_semantic_formatting_and_read_only_replay(tmp_path):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    envelope = new_task_envelope(main_sha)
    body = yaml.safe_load(envelope.payload)
    envelope = replace(envelope, payload=json.dumps(dict(reversed(list(body.items()))), indent=3) + "\r\n")
    result = execute_ingress(envelope, repo=repo)
    assert result.status == "CANONICALIZED"
    assert set(git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", main_sha, result.canonical_sha).splitlines()) == {".ai/tasks/TASK-105.yaml"}
    replay = execute_ingress(envelope, repo=repo)
    assert replay.status == "IDEMPOTENT"
    assert git(remote, "rev-parse", "refs/heads/main") == result.canonical_sha
    with pytest.raises(AuthoringIngressError, match="conflicting TASK"):
        execute_ingress(replace(envelope, payload=envelope.payload.replace("Implement generic", "Change generic")), repo=repo)
    revised = replace(envelope, expected_state={"expected_main_sha": result.canonical_sha},
                      payload=V2_TASK_105_R2_SOURCE)
    assert execute_ingress(revised, repo=repo).status == "CANONICALIZED"


@pytest.mark.parametrize("fault", [
    "stage1_only", "packet", "profile", "flow", "order", "missing_lens",
    "no_decision", "substitution", "reconciliation", "payload",
])
def test_correction_audited_authoring_rejects_invalid_handoff_before_mutation(tmp_path, fault):
    repo, remote, _, _, initial = completion_gate_supersession_fixture(tmp_path)
    envelope = audited_envelope(initial, repo)
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
        handoff["stage2"]["reconciled_candidate"]["instructions"] = ["A different strategy."]
        if fault == "substitution":
            handoff["stage2"]["construct_audit"][0] = {
                "lens": handoff["stage2"]["construct_audit"][0]["lens"], "outcome": "RISK_FOUND",
                "risks": [{"risk_summary": "Strategy mismatch.", "counterexample": "Different strategy.",
                           "candidate_anchor": "instructions", "disposition": "ADDRESSED_BY_RECONCILIATION"}],
            }
    else:
        envelope = replace(envelope, payload={**envelope.payload, "instructions": ["Changed strategy."]})
    with pytest.raises(AuthoringIngressError):
        execute_ingress(replace(envelope, audited_handoff=handoff), repo=repo)
    assert not git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/aios/repair/RUN-254-001")


@pytest.mark.parametrize("input_path", [".ai/roadmap-state.yaml", ".ai/flow-cards.yaml",
                                       ".ai/brain-audit-profiles.yaml", ".ai/brain-return-contracts.yaml",
                                       ".ai/tasks/TASK-OTHER.yaml"])
def test_direct_author_task_uses_canonical_main_without_audit_projections(tmp_path, input_path):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    git(repo, "checkout", "-b", "alternate")
    path = repo / input_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((path.read_bytes() if path.exists() else b"") + b"\n# divergent audit input\n")
    projection = path.read_bytes()
    status = git(repo, "status", "--porcelain")
    result = execute_ingress(new_task_envelope(main_sha), repo=repo)
    assert result.status == "CANONICALIZED"
    assert git(remote, "rev-parse", "refs/heads/main") == result.canonical_sha
    assert git(repo, "rev-parse", "refs/heads/main") == result.canonical_sha
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "alternate"
    assert git(repo, "rev-parse", "HEAD") == main_sha
    assert path.read_bytes() == projection
    assert git(repo, "status", "--porcelain") == status
    assert git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", main_sha, result.canonical_sha) == ".ai/tasks/TASK-105.yaml"
    assert authoring_ingress_module._read_commit_blob(repo, result.canonical_sha, input_path) == authoring_ingress_module._read_commit_blob(repo, main_sha, input_path)


@pytest.mark.parametrize("checkout", ("alternate", "detached"))
def test_direct_author_task_uses_main_upstream_despite_other_transport(tmp_path, checkout):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    _, decoy_remote, decoy_sha = setup_test_repo(tmp_path / "decoy")
    git(repo, "remote", "rename", "origin", "canonical")
    git(repo, "remote", "add", "origin", str(decoy_remote))
    if checkout == "alternate":
        git(repo, "checkout", "-b", "alternate")
        git(repo, "config", "branch.alternate.remote", "origin")
        git(repo, "config", "branch.alternate.merge", "refs/heads/main")
    else:
        git(repo, "checkout", "--detach", main_sha)
    head_binding = (repo / ".git/HEAD").read_bytes()

    first = execute_ingress(new_task_envelope(main_sha), repo=repo)
    revised = replace(new_task_envelope(first.canonical_sha), payload=V2_TASK_105_R2_SOURCE)
    second = execute_ingress(revised, repo=repo)
    replay = execute_ingress(revised, repo=repo)

    assert first.status == second.status == "CANONICALIZED"
    assert replay.status == "IDEMPOTENT" and replay.canonical_sha == second.canonical_sha
    assert git(remote, "rev-parse", "refs/heads/main") == second.canonical_sha
    assert git(repo, "rev-parse", "refs/heads/main") == second.canonical_sha
    assert git(decoy_remote, "rev-parse", "refs/heads/main") == decoy_sha
    assert (repo / ".git/HEAD").read_bytes() == head_binding
    assert git(repo, "rev-parse", "HEAD") == main_sha
    assert not (repo / ".ai/tasks/TASK-105.yaml").exists()
    for parent, candidate in ((main_sha, first.canonical_sha), (first.canonical_sha, second.canonical_sha)):
        assert git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", parent, candidate) == ".ai/tasks/TASK-105.yaml"


@pytest.mark.parametrize("fault", (
    "origin_only", "ambiguous_remote", "local_remote", "unknown_remote",
    "missing_merge", "conflicting_merge", "ambiguous_merge", "missing_url",
    "ambiguous_url", "conflicting_push_url", "ambiguous_push_url",
    "unavailable_remote", "missing_remote_main", "divergent_main",
))
def test_direct_author_task_rejects_unbound_main_transport_before_mutation(tmp_path, monkeypatch, fault):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    git(repo, "checkout", "-b", "alternate")
    if fault == "origin_only":
        git(repo, "config", "--unset-all", "branch.main.remote")
    elif fault == "ambiguous_remote":
        git(repo, "config", "--add", "branch.main.remote", "other")
    elif fault in {"local_remote", "unknown_remote"}:
        git(repo, "config", "branch.main.remote", "." if fault == "local_remote" else "unknown")
    elif fault == "missing_merge":
        git(repo, "config", "--unset-all", "branch.main.merge")
    elif fault == "conflicting_merge":
        git(repo, "config", "branch.main.merge", "refs/heads/other")
    elif fault == "ambiguous_merge":
        git(repo, "config", "--add", "branch.main.merge", "refs/heads/other")
    elif fault == "missing_url":
        git(repo, "config", "--unset-all", "remote.origin.url")
    elif fault == "ambiguous_url":
        git(repo, "config", "--add", "remote.origin.url", str(tmp_path / "other.git"))
    elif fault == "conflicting_push_url":
        git(repo, "config", "remote.origin.pushurl", str(tmp_path / "other.git"))
    elif fault == "ambiguous_push_url":
        git(repo, "config", "--add", "remote.origin.pushurl", str(remote))
        git(repo, "config", "--add", "remote.origin.pushurl", str(remote))
    elif fault == "unavailable_remote":
        git(repo, "config", "remote.origin.url", str(tmp_path / "absent.git"))
    elif fault == "missing_remote_main":
        git(remote, "update-ref", "-d", "refs/heads/main")
    else:
        tree = git(repo, "rev-parse", f"{main_sha}^{{tree}}")
        moved = authoring_ingress_module._commit_tree(repo, tree, [main_sha], "stale main")
        git(remote, "fetch", "--no-tags", str(repo), moved)
        git(remote, "update-ref", "refs/heads/main", moved)
    remote_before = git(remote, "rev-parse", "--verify", "refs/heads/main", check=False)
    for name in ("_hash_blob", "_git_env", "_commit_tree", "_publish_ingress_ref"):
        monkeypatch.setattr(authoring_ingress_module, name, lambda *a, **k: pytest.fail("premature Git mutation"))

    with pytest.raises(AuthoringIngressError, match="canonical main transport"):
        execute_ingress(new_task_envelope(main_sha), repo=repo)

    assert git(repo, "rev-parse", "refs/heads/main") == main_sha
    assert git(repo, "rev-parse", "HEAD") == main_sha
    assert git(remote, "rev-parse", "--verify", "refs/heads/main", check=False) == remote_before
    assert not (repo / ".ai/tasks/TASK-105.yaml").exists()


def test_direct_author_task_rechecks_main_transport_before_publication(tmp_path, monkeypatch):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    _, decoy_remote, decoy_sha = setup_test_repo(tmp_path / "decoy")
    git(repo, "checkout", "-b", "alternate")
    original = authoring_ingress_module._commit_tree
    def change_transport(*args, **kwargs):
        commit = original(*args, **kwargs)
        git(repo, "config", "remote.origin.url", str(decoy_remote))
        return commit
    monkeypatch.setattr(authoring_ingress_module, "_commit_tree", change_transport)
    monkeypatch.setattr(authoring_ingress_module, "_publish_ingress_ref", lambda *a, **k: pytest.fail("stale transport publication"))

    with pytest.raises(AuthoringIngressError, match="transport authority changed"):
        execute_ingress(new_task_envelope(main_sha), repo=repo)

    assert git(repo, "rev-parse", "refs/heads/main") == main_sha
    assert git(remote, "rev-parse", "refs/heads/main") == main_sha
    assert git(decoy_remote, "rev-parse", "refs/heads/main") == decoy_sha


@pytest.mark.parametrize("checkout", ("main", "alternate"))
@pytest.mark.parametrize("movement", ("local_main", "remote_main"))
def test_direct_author_task_cas_rejects_concurrent_main_mutation(tmp_path, monkeypatch, movement, checkout):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    if checkout == "alternate":
        git(repo, "checkout", "-b", "alternate")
    original = authoring_ingress_module._commit_tree
    moved = []
    def move_main(*args, **kwargs):
        commit = original(*args, **kwargs)
        tree = git(repo, "rev-parse", f"{main_sha}^{{tree}}")
        concurrent = original(repo, tree, [main_sha], "concurrent main movement")
        moved.append(concurrent)
        if movement == "remote_main":
            git(remote, "fetch", "--no-tags", str(repo), concurrent)
        git(repo if movement == "local_main" else remote, "update-ref", "refs/heads/main", concurrent)
        return commit
    monkeypatch.setattr(authoring_ingress_module, "_commit_tree", move_main)
    with pytest.raises(AuthoringIngressError, match="concurrency|failed to push"):
        execute_ingress(new_task_envelope(main_sha), repo=repo)
    assert git(remote, "rev-parse", "refs/heads/main") == (moved[0] if movement == "remote_main" else main_sha)
    assert git(repo, "rev-parse", "refs/heads/main") == (moved[0] if movement == "local_main" else main_sha)
    assert not (repo / ".ai/tasks/TASK-105.yaml").exists()


def test_direct_author_task_rejects_unrelated_delta(tmp_path, monkeypatch):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    original = authoring_ingress_module._git_env
    def add_unrelated_blob(repo, env, *args):
        if args == ("write-tree",):
            blob = authoring_ingress_module._hash_blob(repo, b"unrelated change\n")
            original(repo, env, "update-index", "--add", "--cacheinfo", f"100644,{blob},README.md")
        return original(repo, env, *args)
    monkeypatch.setattr(authoring_ingress_module, "_git_env", add_unrelated_blob)
    with pytest.raises(AuthoringIngressError, match="unrelated delta"):
        execute_ingress(new_task_envelope(main_sha), repo=repo)
    assert git(repo, "rev-parse", "HEAD") == main_sha
    assert git(remote, "rev-parse", "refs/heads/main") == main_sha


@pytest.mark.parametrize("movement", ("main", "correction", "input_at_publication"))
def test_correction_audited_authoring_rechecks_freshness(tmp_path, monkeypatch, movement):
    repo, remote, main_sha, _, initial = completion_gate_supersession_fixture(tmp_path)
    envelope = audited_envelope(initial, repo)
    if movement == "input_at_publication":
        original = authoring_ingress_module._commit_tree
        def move_input(*args, **kwargs):
            commit = original(*args, **kwargs)
            path = repo / ".ai/brain-return-contracts.yaml"
            path.write_bytes(path.read_bytes() + b"\n# moved after audit\n")
            return commit
        monkeypatch.setattr(authoring_ingress_module, "_commit_tree", move_input)
    else:
        from aios_renew import brain_audit
        original = brain_audit.validate_stage2
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
        monkeypatch.setattr(brain_audit, "validate_stage2", move_ref)
    with pytest.raises(AuthoringIngressError, match="freshness"):
        execute_ingress(envelope, repo=repo)
    assert not git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/aios/repair/RUN-254-001")


def test_audited_handoff_has_one_closed_correction_surface_and_excludes_reviewer():
    envelope = {
        "format": "AIOS_INGRESS_ENVELOPE", "version": 1, "operation": "AUTHOR_REPAIR",
        "identity": {"failed_run_id": "RUN-105-001"}, "expected_state": {"expected_failed_head_sha": "a" * 40},
        "payload": {"repair_id": "REPAIR-105-001"},
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

V2_TASK_105_SOURCE = TASK_105_SOURCE.replace(
    "verification:\n", "return_affinity: {kind: LEGACY_REPOSITORY_DEFAULT_ROUTE}\nverification:\n  policy: minimum-sufficient-v2\n"
)
V2_TASK_105_R2_SOURCE = TASK_105_R2_SOURCE.replace(
    "verification:\n", "return_affinity: {kind: LEGACY_REPOSITORY_DEFAULT_ROUTE}\nverification:\n  policy: minimum-sufficient-v2\n"
)


V1_TASK_105_SOURCE = V2_TASK_105_SOURCE.replace("minimum-sufficient-v2", "minimum-sufficient-v1")
V1_TASK_105_R2_SOURCE = V2_TASK_105_R2_SOURCE.replace("minimum-sufficient-v2", "minimum-sufficient-v1")

def setup_test_repo(
    root: Path, *, task_id: str = "TASK-105", roadmap: dict | None = None,
) -> tuple[Path, Path, str]:
    """Create local repo and bare upstream git repo."""
    return materialize_git_baseline(
        root,
        files={
            "README.md": "initial repo\n",
            ".gitattributes": ".ai/** -text\n",
            ".ai/roadmap-state.yaml": yaml.safe_dump(roadmap if roadmap is not None else {
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


def completion_gate_supersession_fixture(
    tmp_path, *, roadmap=None, phase="COMPLETION_GATE", task_revision=2,
):
    """RUN-254-001's r2 blocker shape, with isolated Git identities.

    The r2 candidate is clean/transportable and fails only at COMPLETION_GATE.
    It is transported as a failed candidate, never published on fixture main.
    """
    repo, remote, _ = setup_test_repo(tmp_path, task_id="TASK-254", roadmap=roadmap)
    task_source = (TASK_105_R2_SOURCE if task_revision == 2 else TASK_105_SOURCE).replace("TASK-105", "TASK-254")
    task_path = repo / ".ai/tasks/TASK-254.yaml"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(task_source, encoding="utf-8")
    if roadmap is not None:
        (task_path.parent / "TASK-999.yaml").write_text(
            TASK_105_SOURCE.replace("TASK-105", "TASK-999"), encoding="utf-8")
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
    run = {"run_id": "RUN-254-001", "task": {"id": "TASK-254", "revision": task_revision},
           "executor": "codex", "base_sha": base, "head_sha": head,
           "workspace": str(repo), "status": "ACTIVE"}
    failure = {"kind": "FAILURE", "run_id": run["run_id"], "task": run["task"],
               "executor": "codex", "base_sha": base, "failed_head_sha": head,
               "phase": phase,
               "error": {"type": "OperatorError", "message": "RESULT has unresolved items",
                         "executor_diagnostics": {"unresolved": [
                             "Superseding REPAIR cannot obtain an audited packet while Unified State projects EXECUTE_REPAIR."]}},
               "candidate": {"transportable": True, "repairable": True, "dirty": False,
                             "descends_from_base": True, "changed_files": ["src/sample.py"],
                              "outside_task_scope": []}}
    if phase == "VERIFICATION":
        failure["error"] = {"type": "RuntimeVerificationError", "message": "bounded verification failure"}
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
                   "action": "CODE_FIX" if phase == "VERIFICATION" else "CONTINUE_IMPLEMENTATION",
                   "modification_scope": ["src/sample.py"],
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
    roadmap: dict | None = None,
) -> dict[str, object]:
    repo, remote, base_sha = setup_test_repo(root, task_id=task_id, roadmap=roadmap)
    task_dir = repo / ".ai" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / f"{task_id}.yaml").write_bytes(task_source.encode("utf-8"))
    if roadmap is not None:
        (task_dir / "TASK-999.yaml").write_bytes(task_source.replace(task_id, "TASK-999").encode("utf-8"))
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
        payload=V2_TASK_105_SOURCE,
    )
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
    conflicting_source = V2_TASK_105_SOURCE.replace("Implement generic ingress capability.", "Different goal.")
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
        payload=V2_TASK_105_R2_SOURCE,
    )
    with pytest.raises(AuthoringIngressError, match="expected main SHA mismatch"):
        execute_ingress(stale_env, repo=repo)

    # 5. Continuity violation: revision 3 when revision 1 is on main fails closed
    r3_source = V2_TASK_105_R2_SOURCE.replace("revision: 2", "revision: 3")
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
        payload=V2_TASK_105_R2_SOURCE,
    )
    r2_result = execute_ingress(r2_env, repo=repo)
    assert r2_result.status == "CANONICALIZED"
    assert "revision: 2" in (repo / ".ai" / "tasks" / "TASK-105.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("historical_source", (TASK_105_SOURCE, V1_TASK_105_SOURCE))
def test_author_task_rejects_new_legacy_but_replays_historical_revision(tmp_path, historical_source):
    repo, _, base_sha = setup_test_repo(tmp_path)
    new_envelope = IngressEnvelope(
        format="AIOS_INGRESS_ENVELOPE",
        version=1,
        operation="AUTHOR_TASK",
        identity={"task_id": "TASK-105"},
        expected_state={"expected_main_sha": base_sha},
        payload=historical_source,
    )
    with pytest.raises(AuthoringIngressError, match="minimum-sufficient-v2"):
        execute_ingress(new_envelope, repo=repo)

    task_path = repo / ".ai" / "tasks" / "TASK-105.yaml"
    task_path.parent.mkdir(parents=True)
    task_path.write_text(historical_source, encoding="utf-8")
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
            payload=historical_source,
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
        "continuation_of": first_run_id,
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

def test_author_remediation_success_and_rejections(tmp_path, monkeypatch):
    lineage = setup_candidate_lineage(tmp_path)
    repo = lineage["repo"]
    remote = lineage["remote"]
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
    assert "acceptance_phase_ledger" not in rem_env.audited_handoff["stage2"]
    cross_flow = copy.deepcopy(rem_env.audited_handoff)
    cross_flow["stage2"]["acceptance_phase_ledger"] = []
    with pytest.raises(AuthoringIngressError, match="TASK_AUTHORING-only"):
        execute_ingress(replace(rem_env, audited_handoff=cross_flow), repo=repo)
    assert git(remote, "for-each-ref", "--format=%(objectname)", f"refs/heads/aios/remediation/{run_id}-F1") == ""
    from aios_renew import brain_audit
    validate = brain_audit.validate_stage2
    movement_ref = "refs/heads/aios/repair/RUN-OTHER"
    main_sha = git(repo, "rev-parse", "refs/heads/main")
    def move_canonical_input(*args, **kwargs):
        audit = validate(*args, **kwargs)
        git(remote, "update-ref", movement_ref, main_sha)
        return audit
    with monkeypatch.context() as drift:
        drift.setattr(brain_audit, "validate_stage2", move_canonical_input)
        with pytest.raises(AuthoringIngressError, match="freshness"):
            execute_ingress(rem_env, repo=repo)
    assert git(remote, "for-each-ref", "--format=%(objectname)", f"refs/heads/aios/remediation/{run_id}-F1") == ""
    git(remote, "update-ref", "-d", movement_ref)
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


@pytest.mark.parametrize("task_policy", ("minimum-sufficient-v1", "minimum-sufficient-v2"))
def test_author_remediation_for_v1_task_requires_v1_verification(tmp_path, task_policy):
    lineage = setup_candidate_lineage(
        tmp_path, task_source=V2_TASK_105_SOURCE.replace("minimum-sufficient-v2", task_policy)
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

    policy_payload = legacy_payload.replace(
        "affected_verification: [git diff --check]",
        "verification:\n"
        f"  policy: {task_policy}\n"
        "  affected: [git diff --check]",
    )
    result = execute_audited_ingress(
        IngressEnvelope(
            format=envelope.format,
            version=envelope.version,
            operation=envelope.operation,
            identity=envelope.identity,
            expected_state=envelope.expected_state,
            payload=policy_payload,
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
    assert "acceptance_phase_ledger" not in envelope.audited_handoff["stage2"]
    cross_flow = copy.deepcopy(envelope.audited_handoff)
    cross_flow["stage2"]["acceptance_phase_ledger"] = []
    with pytest.raises(AuthoringIngressError, match="TASK_AUTHORING-only"):
        execute_ingress(replace(envelope, audited_handoff=cross_flow), repo=repo)
    assert git(remote, "for-each-ref", "--format=%(objectname)", f"refs/heads/aios/repair/{failed_run_id}") == ""
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
    # reuse that path or the previous authoring packet. Supersession needs a
    # fresh audit bound to the current authorization and exact FAILURE.
    assert execute_ingress(replace(envelope, audited_handoff=None), repo=repo).replayed
    failure_ref = f"refs/heads/aios/failure-artifacts/{failed_run_id}"
    failure_sha = git(repo, "ls-remote", "--refs", "origin", failure_ref).split()[0]
    changed = replace(envelope, payload={**repair_payload, "instructions": ["Changed strategy."]},
                      expected_state={**envelope.expected_state,
                                      "expected_current_repair_sha": result.canonical_sha,
                                      "expected_failure_artifacts_sha": failure_sha})
    with pytest.raises(AuthoringIngressError, match="Stage-1 packet, profile or construct lineage mismatch"):
        execute_ingress(changed, repo=repo)
    with pytest.raises(AuthoringIngressError, match="requires audited_handoff"):
        execute_ingress(replace(changed, audited_handoff=None), repo=repo)
    fresh_changed = audited_envelope(replace(changed, audited_handoff=None), repo)
    packet, _, _, _ = authoring_ingress_module._compose_authoring_packet(fresh_changed, repo)
    assert packet.as_dict()["subject"]["current_repair_authorization_sha"] == result.canonical_sha
    assert packet.as_dict()["subject"]["failure_artifacts_sha"] == failure_sha
    assert fresh_changed.audited_handoff["stage1"]["packet_fingerprint"] != envelope.audited_handoff["stage1"]["packet_fingerprint"]
    authoring_ingress_module._validate_authoring_handoff(fresh_changed, repo)
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
    repo, remote, _ = setup_test_repo(tmp_path, task_id="TASK-121")
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
    # The TASK belongs to the admitted baseline. Combining it with the candidate
    # would make the recorded src-only changed-files binding invalid.
    git(repo, "add", ".ai/tasks/TASK-121.yaml")
    git(repo, "commit", "--quiet", "-m", "canonical TASK-121 baseline")
    base_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "--quiet", "origin", "main")
    git(repo, "switch", "--quiet", "-c", "failed-candidate")
    git(repo, "config", "branch.failed-candidate.remote", "origin")
    git(repo, "config", "branch.failed-candidate.merge", "refs/heads/main")
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "sample.py").write_text("# initial implementation\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "failed candidate for RUN-121-004")
    failed_head_sha = git(repo, "rev-parse", "HEAD")
    assert git(repo, "diff", "--name-only", base_sha, failed_head_sha).splitlines() == ["src/sample.py"]

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
    git(repo, "switch", "--quiet", "main")
    assert git(remote, "rev-parse", "refs/heads/main") == base_sha
    failure_ref = f"refs/heads/aios/failure-artifacts/{failed_run_id}"
    failure_sha = git(repo, "ls-remote", "--refs", "origin", failure_ref).split()[0]

    # --- Revision 1: Legacy CONTINUE_IMPLEMENTATION ---
    rev2_payload = {
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
            rev2_payload,
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
        **rev2_payload,
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
        **rev2_payload,
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
    assert pf_r1.phase == "CANONICAL_CONTRACT_ADMISSION"
    assert pf_r1.reason_code == "CANONICAL_LINEAGE_INVALID"

    # Stale revision 2 selector fails closed in preflight
    pf_r2 = preflight_repair(failed_run_id, repo=repo, required_repair_sha=rev2_sha)
    assert pf_r2.status == "BLOCKED"
    assert pf_r2.phase == "CANONICAL_CONTRACT_ADMISSION"
    assert pf_r2.reason_code == "CANONICAL_LINEAGE_INVALID"

    # Exact current revision 3 succeeds in preflight
    pf_r3 = preflight_repair(failed_run_id, repo=repo, required_repair_sha=rev3_sha)
    assert pf_r3.status == "READY"
    assert pf_r3.phase == "READY"
    assert pf_r3.reason_code == "READY"
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


@pytest.mark.parametrize("carrier", ["run", "base_task", "head_task", "lineage_run", "lineage_task"])
def test_transport_affinity_rejects_exact_carrier_drift(monkeypatch, carrier):
    from copy import deepcopy
    from aios_renew import review_transport as transport
    import yaml

    selector = {"kind": "ORIGIN_AFFINE", "route_handle": "page-origin-v1:" + "a" * 64, "generation": 1}
    task = dict(yaml.safe_load(TASK_105_SOURCE), return_affinity=selector)
    run = {"run_id": "RUN-105-001", "task": {"id": "TASK-105", "revision": 1},
           "base_sha": "b" * 40, "return_affinity": selector}
    lineage = {"task": deepcopy(task), "run": deepcopy(run)}
    tasks = {"b" * 40: deepcopy(task), "c" * 40: deepcopy(task)}
    target = {"run": run, "base_task": tasks["b" * 40], "head_task": tasks["c" * 40],
              "lineage_run": lineage["run"], "lineage_task": lineage["task"]}[carrier]
    target["return_affinity"] = dict(selector, generation=2)
    monkeypatch.setattr(transport, "_read_local_blob", lambda repo, sha, name: json.dumps(tasks[sha]).encode())
    with pytest.raises(transport.ReviewTransportError, match="affinity"):
        transport._validate_transport_affinity(Path("."), json.dumps(run).encode(), "c" * 40,
                                               json.dumps(lineage).encode())


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
    # Unified State reconstructs the predecessor through the canonical Reviewer
    # decision ref, not the REVIEW copy in the remediation fixture commit.
    execute_ingress(
        IngressEnvelope(
            "AIOS_INGRESS_ENVELOPE", 1, "SUBMIT_REVIEW",
            {"run_id": source_run_id},
            {"expected_candidate_sha": reviewed_sha}, review_source,
        ),
        repo=repo,
    )
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



def test_h2_authoring_binds_transported_strategy_and_rejects_sidecar_drift(tmp_path, monkeypatch):
    from aios_renew import brain_audit

    repo, remote, _, failure_sha, initial = completion_gate_supersession_fixture(tmp_path)
    packet, _, _, _ = authoring_ingress_module._compose_authoring_packet(initial, repo)
    facts = packet.as_dict()["bounded_observations"]["strategy_facts"]
    assert facts["reusable_preverification_state"] == "ABSENT"
    assert facts["structural_result_package"] == "MISSING"
    assert facts["action_structural_eligibility"] == {
        "NO_CHANGE": "INELIGIBLE", "FINALIZE_CANDIDATE": "ELIGIBLE",
        "CONTINUE_IMPLEMENTATION": "ELIGIBLE", "CODE_FIX": "ELIGIBLE"}
    # Conflicting machine-local state has no standing in the projection.
    from aios_renew.operator import runtime_paths
    state = runtime_paths(repo)
    (state.preverification / "RUN-254-001.json").write_bytes(b"local transient garbage")
    assert authoring_ingress_module._compose_authoring_packet(initial, repo)[0].render() == packet.render()
    envelope = audited_envelope(initial, repo)
    validate = brain_audit.validate_stage2

    def move_transport(*args, **kwargs):
        audit = validate(*args, **kwargs)
        tree = authoring_ingress_module._tree_with_metadata(
            repo, failure_sha, ".ai/transport/pre-verification-candidate.json", b"{malformed",
        )
        moved = authoring_ingress_module._commit_tree(repo, tree, [failure_sha], "sidecar movement")
        git(remote, "fetch", "--no-tags", str(repo), moved)
        git(remote, "update-ref", "refs/heads/aios/failure-artifacts/RUN-254-001", moved)
        return audit

    monkeypatch.setattr(brain_audit, "validate_stage2", move_transport)
    with pytest.raises(AuthoringIngressError, match="freshness"):
        execute_ingress(envelope, repo=repo)
    assert not git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/aios/repair/RUN-254-001")


def h2_reviewed_repair_source(tmp_path):
    lineage = _integrated_remediation_repair_review_lineage(tmp_path)
    repo = lineage["repo"]
    review = f"""review_id: REVIEW-140-010
reviewed_sha: {lineage['repaired_sha']}
mode: DELTA
verdict: CHANGES_REQUIRED
prior_finding_id: F1
acceptance:
  AC1: FAIL
findings:
  - id: F2
    basis: AC1
    action: CODE_FIX
    location: src/sample.py
    issue: One additional bounded defect remains.
    expected: Correct the additional defect.
"""
    execute_ingress(IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "SUBMIT_REVIEW",
        {"run_id": lineage["run_id"]}, {"expected_candidate_sha": lineage["repaired_sha"]}, review), repo=repo)
    remediation = {"finding_id": "F2", "action": "CODE_FIX", "reviewed_sha": lineage["repaired_sha"],
                   "modification_scope": ["src/sample.py"],
                   "affected_verification": ["git diff --check"], "constraints": ["Bounded mutation authority only."]}
    envelope = IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REMEDIATION",
        {"source_run_id": lineage["run_id"], "finding_id": "F2"},
        {"expected_reviewed_sha": lineage["repaired_sha"]}, remediation)
    return lineage, envelope


def test_h2_successful_repair_delta_changes_required_authors_and_admits_remediation(tmp_path):
    from aios_renew.operator import preflight_remediation, run_remediation

    lineage, envelope = h2_reviewed_repair_source(tmp_path)
    repo = lineage["repo"]
    authorization = execute_audited_ingress(envelope, repo=repo)
    assert authorization.status == "CANONICALIZED"
    ready = preflight_remediation("TASK-140", finding_id="F2", source_run_id=lineage["run_id"], repo=repo)
    assert ready.status == "READY"
    assert ready.source_run_id == lineage["run_id"]
    assert ready.reviewed_sha == lineage["repaired_sha"]
    calls = []

    def executor(command, **kwargs):
        calls.append("executor")
        execution = json.loads(kwargs["input"].decode().split("REMEDIATION_INPUT:\n", 1)[1])
        workspace = Path(execution["run"]["workspace"])
        target = workspace / "src/sample.py"
        target.write_text(target.read_text() + "# additional correction\n", encoding="utf-8")
        git(workspace, "add", "src/sample.py")
        git(workspace, "commit", "--quiet", "-m", "bounded F2 correction")
        return subprocess.CompletedProcess(command, 0, json.dumps({"result": {
            "head_sha": git(workspace, "rev-parse", "HEAD"),
            "claims": [],
            "changed_files": ["src/sample.py"], "unresolved": []}, "evidence": []}), "")

    def verification(command, **kwargs):
        calls.append("verification")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    summary = run_remediation("TASK-140", finding_id="F2", source_run_id=lineage["run_id"],
        executor="codex", repo=repo, native_runner=executor, verification_runner=verification)
    assert calls == ["executor", "verification"]
    assert summary.head_sha != lineage["repaired_sha"]
    review = f"""review_id: REVIEW-140-F2-FINAL
reviewed_sha: {summary.head_sha}
mode: DELTA
verdict: PASS
prior_finding_id: F2
acceptance:
  AC1: PASS
findings: []
"""
    decision = execute_ingress(IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "SUBMIT_REVIEW",
        {"run_id": summary.run_id}, {"expected_candidate_sha": summary.head_sha}, review), repo=repo)
    assert decision.status == "CANONICALIZED"
    report = publish_review_decision(repo, run_id=summary.run_id, decision_sha=decision.canonical_sha)
    assert report.outcome == "PUBLISHED"
    assert git(lineage["remote"], "rev-parse", "refs/heads/main") == summary.head_sha


@pytest.mark.parametrize("defect", ["missing", "malformed", "run", "failed_head", "root", "result_base",
                                    "task", "authorization", "moved_failure", "missing_failure",
                                    "prior_review", "conflicting_kind"])
def test_h2_successful_repair_source_invalid_lineage_creates_no_remediation(tmp_path, defect):
    lineage, envelope = h2_reviewed_repair_source(tmp_path)
    repo, remote = lineage["repo"], lineage["remote"]
    source_id = lineage["run_id"]
    artifacts_ref = f"refs/heads/aios/artifacts/{source_id}"
    artifacts_sha = git(remote, "rev-parse", artifacts_ref)
    repair_bytes = authoring_ingress_module._read_commit_blob(repo, artifacts_sha, ".ai/transport/repair.json")
    repair = json.loads(repair_bytes)
    if defect == "missing_failure":
        git(remote, "update-ref", "-d", "refs/heads/aios/failure/RUN-140-009")
    elif defect == "moved_failure":
        git(remote, "update-ref", "refs/heads/aios/failure/RUN-140-009", lineage["repaired_sha"])
    elif defect == "prior_review":
        prior_ref = "refs/heads/aios/remediation/RUN-140-008-F1"
        parent = git(remote, "rev-parse", prior_ref)
        paths = git(repo, "ls-tree", "-r", "--name-only", parent, "--", ".ai/reviews").splitlines()
        original = authoring_ingress_module._read_commit_blob(repo, parent, paths[0])
        substituted = original.replace(b"review_id:", b"review_id: UNRELATED #", 1)
        tree = authoring_ingress_module._tree_with_metadata(repo, parent, paths[0], substituted, replace_existing=True)
        moved = authoring_ingress_module._commit_tree(repo, tree, [parent], "unrelated prior REVIEW")
        git(remote, "fetch", "--no-tags", str(repo), moved)
        git(remote, "update-ref", prior_ref, moved)
    else:
        if defect == "run":
            repair["run"]["run_id"] = "RUN-140-099"
        elif defect == "failed_head":
            repair["failed_head_sha"] = lineage["repaired_sha"]
        elif defect == "root":
            repair["root_base_sha"] = lineage["failed_head_sha"]
        elif defect == "result_base":
            repair["result_base_sha"] = lineage["failed_head_sha"]
        elif defect == "task":
            repair["task"]["task_id"] = "TASK-OTHER"
        elif defect == "authorization":
            repair["repair"]["instructions"] = ["Substituted authorization"]
        tree = authoring_ingress_module._tree_with_metadata(repo, artifacts_sha,
            ".ai/transport/repair.json", b"{bad" if defect == "malformed" else json.dumps(repair).encode(),
            replace_existing=True)
        if defect == "missing":
            # Build the exact transported tree with only the wrapper removed.
            index = tmp_path / "remove-wrapper-index"
            import os
            env = {**os.environ, "GIT_INDEX_FILE": str(index)}
            subprocess.run(("git", "-C", str(repo), "read-tree", tree), env=env, check=True)
            subprocess.run(("git", "-C", str(repo), "update-index", "--force-remove", ".ai/transport/repair.json"), env=env, check=True)
            tree = subprocess.run(("git", "-C", str(repo), "write-tree"), env=env, capture_output=True, text=True, check=True).stdout.strip()
        elif defect == "conflicting_kind":
            run = json.loads(authoring_ingress_module._read_commit_blob(repo, artifacts_sha, ".ai/transport/run.json"))
            run["kind"] = "UNRELATED"
            intermediate = authoring_ingress_module._commit_tree(repo, tree, [artifacts_sha], "conflict stage")
            tree = authoring_ingress_module._tree_with_metadata(repo, intermediate, ".ai/transport/run.json", json.dumps(run).encode(), replace_existing=True)
        moved = authoring_ingress_module._commit_tree(repo, tree, [artifacts_sha], "invalid source wrapper")
        git(remote, "fetch", "--no-tags", str(repo), moved)
        git(remote, "update-ref", artifacts_ref, moved)
    from aios_renew.operator import OperatorError
    with pytest.raises((AuthoringIngressError, OperatorError, RuntimeError, ValueError)):
        execute_audited_ingress(envelope, repo=repo)
    assert not git(remote, "for-each-ref", "--format=%(refname)", f"refs/heads/aios/remediation/{source_id}-F2")


def test_direct_author_task_freezes_caller_owned_payload(tmp_path, monkeypatch):
    repo, remote, main_sha = setup_test_repo(tmp_path)
    body = yaml.safe_load(V2_TASK_105_SOURCE)
    envelope = replace(new_task_envelope(main_sha), payload=body)
    original = authoring_ingress_module._hash_blob
    def mutate_caller(*args, **kwargs):
        body["goal"] = "Substituted after direct validation."
        envelope.identity["task_id"] = "TASK-OTHER"
        return original(*args, **kwargs)
    monkeypatch.setattr(authoring_ingress_module, "_hash_blob", mutate_caller)
    result = execute_ingress(envelope, repo=repo)
    raw = git(repo, "show", f"{result.canonical_sha}:.ai/tasks/TASK-105.yaml")
    assert yaml.safe_load(raw) == yaml.safe_load(V2_TASK_105_SOURCE)
    assert git(remote, "rev-parse", "refs/heads/main") == result.canonical_sha


def correction_planning_state(planning, task_id):
    item = {"id": "bo-2-3-canonical-context-pipeline", "status": "NEXT"}
    if planning == "unrelated":
        item["task_id"] = "TASK-999"
    elif planning == "matching":
        item["task_id"] = task_id
    roadmap = {"version": 1, "active_track": "brain-optimization-v1",
               "active_track_status": "ACTIVE", "sequence": [item]}
    if planning == "split_brain":
        roadmap["next_items"] = ["unrelated-planning-next"]
    return roadmap


def exact_correction_fixture(
    tmp_path, operation, planning, *, multiple_findings=False, repair_phase="VERIFICATION",
):
    if operation == "AUTHOR_REPAIR":
        return completion_gate_supersession_fixture(
            tmp_path, roadmap=correction_planning_state(planning, "TASK-254"),
            phase=repair_phase, task_revision=1 if repair_phase == "VERIFICATION" else 2)
    lineage = setup_candidate_lineage(
        tmp_path, roadmap=correction_planning_state(planning, "TASK-105"))
    repo, remote = lineage["repo"], lineage["remote"]
    run_id, head = lineage["run_id"], lineage["candidate_sha"]
    review = {"review_id": "REVIEW-105-001", "reviewed_sha": head,
              "mode": "PRIMARY", "verdict": "CHANGES_REQUIRED", "acceptance": {"AC1": "FAIL"},
              "findings": [{"id": "F1", "basis": "AC1", "action": "CODE_FIX",
                            "location": "src/sample.py", "issue": "One bounded defect.",
                            "expected": "Correct the defect."}]}
    if multiple_findings:
        review["findings"].append({**review["findings"][0], "id": "F2"})
    decision = execute_ingress(IngressEnvelope(
        "AIOS_INGRESS_ENVELOPE", 1, "SUBMIT_REVIEW", {"run_id": run_id},
        {"expected_candidate_sha": head}, yaml.safe_dump(review)), repo=repo)
    git(repo, "reset", "--hard", lineage["main_sha"])
    payload = {"finding_id": "F1", "action": "CODE_FIX", "reviewed_sha": head,
               "modification_scope": ["src/sample.py"], "affected_verification": ["git diff --check"],
               "constraints": ["Bounded mutation authority only."]}
    envelope = IngressEnvelope(
        "AIOS_INGRESS_ENVELOPE", 1, operation, {"source_run_id": run_id, "finding_id": "F1"},
        {"expected_reviewed_sha": head}, payload)
    return repo, remote, lineage["main_sha"], decision.canonical_sha, envelope


def forbid_correction_mutation(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("correction observation or rejection attempted Git mutation")
    for name in ("_hash_blob", "_git_env", "_commit_tree", "_publish_ingress_ref"):
        monkeypatch.setattr(authoring_ingress_module, name, forbidden)


@pytest.mark.parametrize("operation", ["AUTHOR_REPAIR", "AUTHOR_REMEDIATION"])
@pytest.mark.parametrize("planning", ["unauthored", "unrelated", "split_brain"])
def test_exact_correction_packet_and_audited_ingress_ignore_planning_next(tmp_path, monkeypatch, operation, planning):
    from aios_renew.brain_context import compose_brain_work_context, resolve_flow
    from aios_renew.brain_sync import observe_brain_sync

    repo, remote, main, lineage_sha, envelope = exact_correction_fixture(tmp_path, operation, planning)
    task = envelope.payload.get("task", {"id": "TASK-105", "revision": 1})
    generic = observe_brain_sync(repo=repo)
    if planning == "unauthored":
        assert generic.selection_status == "UNAUTHORED_TASK" and generic.selected_task is None
        assert resolve_flow(compose_brain_work_context(generic)).selected_flow == "TASK_AUTHORING"
    elif planning == "unrelated":
        assert generic.selected_task == {"id": "TASK-999", "revision": 1}
        assert generic.next_action == "EXECUTE_PRIMARY"
    else:
        assert generic.selection_status == "AMBIGUOUS_NEXT" and generic.selected_task is None
    before_refs = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    before_head = git(repo, "rev-parse", "HEAD")
    before_roadmap = (repo / ".ai/roadmap-state.yaml").read_bytes()
    before_task = (repo / f".ai/tasks/{task['id']}.yaml").read_bytes()
    with monkeypatch.context() as guard:
        forbid_correction_mutation(guard)
        packet, _, _, inputs = authoring_ingress_module._compose_authoring_packet(envelope, repo)
        snapshot = authoring_ingress_module._canonical_correction_snapshot(operation, envelope.identity, repo, inputs)
        work = compose_brain_work_context(snapshot)
        assert work.canonical_observation["roadmap"] == {}
        assert work.canonical_observation["selected_task"] == task
        assert work.canonical_observation["unified_state"]["task"] == task
        assert resolve_flow(work).selected_flow == operation.replace("AUTHOR_", "") + "_AUTHORING"
        with pytest.raises(AuthoringIngressError, match="requires audited_handoff"):
            execute_ingress(envelope, repo=repo)
    facts = packet.as_dict()["canonical_facts"]
    assert facts["selected_task"] == task
    assert facts["task_contract"]["task_id"] == task["id"]
    assert facts["task_contract"]["revision"] == task["revision"]
    assert facts["canonical_next_action"] == facts["unified_state_next_action"] == operation
    assert facts["roadmap"] is None
    subject = facts["correction_subject"]
    assert subject["policy"] == "EXACT_CORRECTION_SUBJECT_LINEAGE_V1"
    assert subject["selectors"] == envelope.identity and subject["task"] == task
    if operation == "AUTHOR_REPAIR":
        assert subject["artifacts_sha"] == lineage_sha
        assert packet.as_dict()["subject"]["failed_run_id"] == envelope.identity["failed_run_id"]
        assert packet.as_dict()["subject"]["failed_head_sha"] == envelope.payload["failed_head_sha"]
    else:
        assert subject["review_decision_sha"] == lineage_sha
        assert packet.as_dict()["subject"]["source_run_id"] == envelope.identity["source_run_id"]
        assert packet.as_dict()["subject"]["finding_id"] == envelope.identity["finding_id"]
        assert packet.as_dict()["subject"]["reviewed_sha"] == envelope.payload["reviewed_sha"]
    assert all(packet.as_dict()[key] is False for key in
               ("run_created", "executor_invoked", "verification_invoked", "state_mutated"))
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs
    result = execute_audited_ingress(envelope, repo=repo)
    assert result.status == "CANONICALIZED"
    parent = envelope.payload["failed_head_sha"] if operation == "AUTHOR_REPAIR" else lineage_sha
    metadata = (".ai/transport/repair.json" if operation == "AUTHOR_REPAIR" else
                f".ai/remediations/REMEDIATION-{envelope.identity['source_run_id']}-F1.yaml")
    assert git(repo, "rev-parse", f"{result.canonical_sha}^") == parent
    assert git(repo, "diff", "--name-only", parent, result.canonical_sha) == metadata
    assert git(remote, "rev-parse", "refs/heads/main") == main
    assert git(repo, "rev-parse", "HEAD") == before_head
    assert (repo / ".ai/roadmap-state.yaml").read_bytes() == before_roadmap
    assert (repo / f".ai/tasks/{task['id']}.yaml").read_bytes() == before_task
    assert observe_brain_sync(repo=repo).as_dict() == generic.as_dict()


@pytest.mark.parametrize("planning", ["unauthored", "unrelated", "split_brain"])
def test_exact_repair_supersession_keeps_audited_path_independent_of_next(tmp_path, planning):
    repo, remote, main, failure_sha, initial = exact_correction_fixture(
        tmp_path, "AUTHOR_REPAIR", planning, repair_phase="COMPLETION_GATE")
    first = execute_audited_ingress(initial, repo=repo)
    successor = replace(initial, expected_state={**initial.expected_state,
        "expected_current_repair_sha": first.canonical_sha, "expected_failure_artifacts_sha": failure_sha},
        payload={**initial.payload, "action": "FINALIZE_CANDIDATE", "modification_scope": [],
                 "instructions": ["Finalize the exact existing candidate."]})
    packet, _, _, _ = authoring_ingress_module._compose_authoring_packet(successor, repo)
    body = packet.as_dict()
    assert body["selection_basis"] == "EXPLICIT_UNEXECUTED_REPAIR_SUPERSESSION"
    assert body["canonical_facts"]["roadmap"] is None
    assert body["canonical_facts"]["canonical_next_action"] == "EXECUTE_REPAIR"
    assert body["subject"]["failure_artifacts_sha"] == failure_sha
    second = execute_audited_ingress(successor, repo=repo)
    current = resolve_remote_repair_authorization(repo, initial.identity["failed_run_id"])
    assert current.commit_sha == second.canonical_sha and current.predecessor_sha == first.canonical_sha
    assert current.revision == 2
    assert git(remote, "rev-parse", "refs/heads/main") == main


def substitute_correction_blob(repo, remote, ref, path, content):
    sha = git(remote, "rev-parse", ref)
    tree = authoring_ingress_module._tree_with_metadata(repo, sha, path, content, replace_existing=True)
    moved = authoring_ingress_module._commit_tree(repo, tree, [sha], "substituted correction fixture")
    git(remote, "fetch", "--no-tags", str(repo), moved)
    git(remote, "update-ref", ref, moved)


def substitute_transport_document(repo, remote, ref, path, change):
    sha = git(remote, "rev-parse", ref)
    document = json.loads(authoring_ingress_module._authoring_blob(repo, sha, path, "origin"))
    change(document)
    substitute_correction_blob(repo, remote, ref, path, json.dumps(document).encode("utf-8"))


@pytest.mark.parametrize("planning,fault", [
    ("matching", fault) for fault in ("wrong_selector", "stale_selector", "run_task", "run_revision",
        "failure_task", "failed_head", "run_head", "executor", "base", "non_repairable", "non_transportable",
        "missing_candidate", "competing_lineage", "unified_task", "unified_head", "malformed")
] + [(planning, "wrong_selector") for planning in ("unauthored", "unrelated", "split_brain")])
def test_exact_repair_invalid_engineering_lineage_fails_closed(tmp_path, monkeypatch, planning, fault):
    repo, remote, main, failure_sha, envelope = exact_correction_fixture(tmp_path, "AUTHOR_REPAIR", planning)
    run_id = envelope.identity["failed_run_id"]
    ref = f"refs/heads/aios/failure-artifacts/{run_id}"
    if fault == "wrong_selector":
        envelope = replace(envelope, identity={"failed_run_id": "RUN-254-099"})
    elif fault == "stale_selector":
        git(remote, "update-ref", "refs/heads/aios/failure-artifacts/RUN-254-000", failure_sha)
        envelope = replace(envelope, identity={"failed_run_id": "RUN-254-000"})
    elif fault == "missing_candidate":
        git(remote, "update-ref", "-d", f"refs/heads/aios/failure/{run_id}")
    elif fault == "malformed":
        substitute_correction_blob(repo, remote, ref, ".ai/transport/failure.json", b'{"kind": "FAILURE",')
    elif fault == "competing_lineage":
        substitute_transport_document(repo, remote, ref, ".ai/transport/run.json",
                                      lambda doc: doc.update(run_id="RUN-254-099"))
        moved = git(remote, "rev-parse", ref)
        git(remote, "update-ref", ref, failure_sha)
        git(remote, "update-ref", "refs/heads/aios/failure-artifacts/RUN-254-099", moved)
        git(remote, "update-ref", "refs/heads/aios/failure/RUN-254-099", envelope.payload["failed_head_sha"])
    elif fault.startswith("unified_"):
        real = observe_unified_state("TASK-254", repo=repo)
        altered = replace(real, task_revision=99) if fault == "unified_task" else replace(real, failed_head_sha=main)
        monkeypatch.setattr(authoring_ingress_module, "observe_unified_state", lambda *a, **k: altered)
    else:
        path = ".ai/transport/run.json" if fault.startswith("run_") else ".ai/transport/failure.json"
        def change(doc):
            if fault in {"run_task", "failure_task"}:
                doc["task"]["id"] = "TASK-999"
            elif fault == "run_revision":
                doc["task"]["revision"] = 99
            elif fault == "run_head":
                doc["head_sha"] = main
            elif fault == "failed_head":
                doc["failed_head_sha"] = main
            elif fault == "executor":
                doc["executor"] = "antigravity"
            elif fault == "base":
                doc["base_sha"] = envelope.payload["failed_head_sha"]
            elif fault == "non_repairable":
                doc["candidate"].update(repairable=False, transportable=False, dirty=True)
            else:
                doc["candidate"].update(transportable=False, changed_files=["outside.py"],
                                        outside_task_scope=["outside.py"])
        substitute_transport_document(repo, remote, ref, path, change)
    before_refs = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    forbid_correction_mutation(monkeypatch)
    with pytest.raises((ValueError, ReviewTransportError)):
        authoring_ingress_module._compose_authoring_packet(envelope, repo)
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs
    assert not git(remote, "for-each-ref", "--format=%(refname)", f"refs/heads/aios/repair/{run_id}")


@pytest.mark.parametrize("operation", ["AUTHOR_REPAIR", "AUTHOR_REMEDIATION"])
@pytest.mark.parametrize("planning", ["unauthored", "unrelated", "split_brain"])
def test_moved_immutable_correction_identity_invalidates_prior_audit(tmp_path, monkeypatch, operation, planning):
    repo, remote, _, lineage_sha, envelope = exact_correction_fixture(tmp_path, operation, planning)
    prior, _, _, _ = authoring_ingress_module._compose_authoring_packet(envelope, repo)
    audited = audited_envelope(envelope, repo)
    run_id = envelope.identity.get("failed_run_id", envelope.identity.get("source_run_id"))
    family = "failure-artifacts" if operation == "AUTHOR_REPAIR" else "review-decision"
    ref = f"refs/heads/aios/{family}/{run_id}"
    tree = git(repo, "rev-parse", f"{lineage_sha}^{{tree}}")
    # The parent list may be empty (FAILURE transport uses a root commit).
    # Preserve every parent without asking Git to resolve a nonexistent '^'.
    parents = git(repo, "show", "-s", "--format=%P", lineage_sha).split()
    moved = authoring_ingress_module._commit_tree(repo, tree, parents, "moved immutable correction identity")
    assert moved != lineage_sha
    assert git(repo, "rev-parse", f"{moved}^{{tree}}") == tree
    assert git(repo, "show", "-s", "--format=%P", moved).split() == parents
    assert git(repo, "diff", "--name-only", lineage_sha, moved) == ""
    git(remote, "fetch", "--no-tags", str(repo), moved)
    git(remote, "update-ref", ref, moved)
    fresh, _, _, _ = authoring_ingress_module._compose_authoring_packet(envelope, repo)
    assert fresh.as_dict()["subject"] == prior.as_dict()["subject"]
    for field in ("selected_task", "task_contract", "unified_state_next_action"):
        assert fresh.as_dict()["canonical_facts"][field] == prior.as_dict()["canonical_facts"][field]
    assert fresh.packet_fingerprint != audited.audited_handoff["stage1"]["packet_fingerprint"]
    before_refs = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    forbid_correction_mutation(monkeypatch)
    with pytest.raises(AuthoringIngressError, match="Stage-1 packet, profile or construct lineage mismatch"):
        execute_ingress(audited, repo=repo)
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs


@pytest.mark.parametrize("planning,fault", [
    ("matching", fault) for fault in ("source", "stale_source", "finding", "reviewed_head", "run_task",
        "run_revision", "run_head", "result_head", "missing_review", "resolved", "review_identity",
        "finding_identity", "unified_task", "competing_lineage", "malformed_result", "malformed_review")
] + [(planning, "source") for planning in ("unauthored", "unrelated", "split_brain")])
def test_exact_remediation_invalid_lineage_fails_closed(tmp_path, monkeypatch, planning, fault):
    repo, remote, main, decision_sha, envelope = exact_correction_fixture(
        tmp_path, "AUTHOR_REMEDIATION", planning, multiple_findings=fault == "competing_lineage")
    audited = audited_envelope(envelope, repo)
    run_id = envelope.identity["source_run_id"]
    ref = f"refs/heads/aios/artifacts/{run_id}"
    if fault == "source":
        envelope = replace(envelope, identity={**envelope.identity, "source_run_id": "RUN-105-099"})
    elif fault == "stale_source":
        git(remote, "update-ref", "refs/heads/aios/artifacts/RUN-105-099", git(remote, "rev-parse", ref))
        envelope = replace(envelope, identity={**envelope.identity, "source_run_id": "RUN-105-099"})
    elif fault == "finding":
        envelope = replace(envelope, identity={**envelope.identity, "finding_id": "F999"})
    elif fault == "reviewed_head":
        envelope = replace(envelope, expected_state={"expected_reviewed_sha": main})
    elif fault == "missing_review":
        git(remote, "update-ref", "-d", f"refs/heads/aios/review-decision/{run_id}")
    elif fault == "malformed_result":
        substitute_correction_blob(repo, remote, ref, ".ai/transport/result.json", b'{"result":')
    elif fault == "malformed_review":
        substitute_correction_blob(repo, remote, f"refs/heads/aios/review-decision/{run_id}",
                                   ".ai/reviews/REVIEW-105-001.yaml", b'review_id: [')
    elif fault in {"resolved", "unified_task"}:
        real = observe_unified_state("TASK-105", repo=repo)
        altered = (replace(real, outstanding_findings=()) if fault == "resolved" else
                   replace(real, task_id="TASK-999"))
        monkeypatch.setattr(authoring_ingress_module, "observe_unified_state", lambda *a, **k: altered)
    elif fault == "competing_lineage":
        for finding_id in ("F1", "F2"):
            path = f".ai/remediations/REMEDIATION-{run_id}-{finding_id}.yaml"
            body = yaml.safe_dump({**envelope.payload, "finding_id": finding_id}).encode("utf-8")
            tree = authoring_ingress_module._tree_with_metadata(repo, decision_sha, path, body)
            commit = authoring_ingress_module._commit_tree(repo, tree, [decision_sha], "competing remediation fixture")
            git(remote, "fetch", "--no-tags", str(repo), commit)
            git(remote, "update-ref", f"refs/heads/aios/remediation/{run_id}-{finding_id}", commit)
    elif fault in {"review_identity", "finding_identity"}:
        review_ref = f"refs/heads/aios/review-decision/{run_id}"
        sha = git(remote, "rev-parse", review_ref)
        path = ".ai/reviews/REVIEW-105-001.yaml"
        review = yaml.safe_load(authoring_ingress_module._authoring_blob(repo, sha, path, "origin"))
        if fault == "review_identity":
            review["review_id"] = "REVIEW-105-OTHER"
        else:
            review["findings"][0]["id"] = "F999"
        substitute_correction_blob(repo, remote, review_ref, path, yaml.safe_dump(review).encode("utf-8"))
    else:
        path = ".ai/transport/result.json" if fault == "result_head" else ".ai/transport/run.json"
        def change(doc):
            if fault == "result_head":
                doc["result"]["head_sha"] = main
            elif fault == "run_task":
                doc["task"]["id"] = "TASK-999"
            elif fault == "run_head":
                doc["head_sha"] = main
            else:
                doc["task"]["revision"] = 2
        substitute_transport_document(repo, remote, ref, path, change)
    before_refs = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    forbid_correction_mutation(monkeypatch)
    if fault == "review_identity":
        with pytest.raises(AuthoringIngressError):
            execute_ingress(audited, repo=repo)
    else:
        with pytest.raises((ValueError, ReviewTransportError)):
            authoring_ingress_module._compose_authoring_packet(envelope, repo)
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs
    if fault != "competing_lineage":
        assert not git(remote, "for-each-ref", "--format=%(refname)", f"refs/heads/aios/remediation/{run_id}-F1")
