"""Offline owner-boundary tests; no upstream test invocation or live service.

Fixture Git is content only. Admission/ingress code runs unchanged; only native
execution and Runtime completion are intercepted so no verification is scheduled.
"""

import json
import sys
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
import yaml

from aios_renew import operator as op
from aios_renew import runtime_provenance_owner as provenance
from aios_renew.artifacts import Result, ResultPackage
from aios_renew.authoring_ingress import AuthoringIngressError, IngressEnvelope, IngressResult, execute_ingress
from aios_renew.github_issue_ingress import GitHubIssueIngressError, deliver_event
from aios_renew.execution_profile import parse_execution_profile_policy
from aios_renew.run import Run
from aios_renew.run_observation import RunObservationTracker
from aios_renew.runtime import RuntimeCompletion
from aios_renew.review_transport import transport_post_pass
from aios_renew.runtime_provenance_owner import Gap, State, join_owner_provenance, read_owner_provenance
from test_authoring_ingress import (
    TASK_105_SOURCE, audited_envelope, git, setup_candidate_lineage, setup_test_repo,
)


POLICY = {"format": "AIOS_EXECUTOR_PROFILES_POLICY", "version": 1,
          "executors": {name: {"default_model": "fixture-model", "default_reasoning_effort": "high",
                                "supported_reasoning_efforts": ["high"]}
                        for name in ("codex", "antigravity")}}


class ReachedCompletion(Exception):
    pass


def forbidden(*args, **kwargs):
    pytest.fail("native execution/verification is outside this deterministic fixture")


@pytest.fixture
def intercepted(monkeypatch):
    owners, dispatch_inputs = [], []

    def complete(owner, package, policy):
        owners.append(owner)
        raise ReachedCompletion

    def package(repo):
        return ResultPackage(Result(git(repo, "rev-parse", "HEAD"), (), (), ()), ())

    def primary_factory(**kwargs):
        def dispatch_primary(**inputs):
            dispatch_inputs.append(inputs)
            repo = kwargs["repo"]
            (repo / "src").mkdir(exist_ok=True)
            (repo / "src/sample.py").write_text("# bounded candidate\n", encoding="utf-8")
            git(repo, "add", "src/sample.py")
            git(repo, "commit", "-qm", "candidate")
            return package(repo)
        return SimpleNamespace(dispatch_primary=dispatch_primary)

    def correction_factory(**kwargs):
        def dispatch(**inputs):
            dispatch_inputs.append(inputs)
            return package(kwargs["repo"])
        return SimpleNamespace(dispatch_repair=dispatch, dispatch_remediation=dispatch)

    monkeypatch.setattr(RuntimeCompletion, "complete", complete)
    monkeypatch.setattr(op, "primary_dispatcher", primary_factory)
    monkeypatch.setattr(op, "repair_dispatcher", correction_factory)
    monkeypatch.setattr(op, "remediation_dispatcher", correction_factory)
    # Deterministic profile policy, with real bind/validate/persist behavior.
    monkeypatch.setattr(op, "load_execution_profile_policy", lambda repo: parse_execution_profile_policy(POLICY))
    return owners, dispatch_inputs


def primary_repository(tmp_path):
    repo, remote, _ = setup_test_repo(tmp_path)
    (repo / ".ai/tasks").mkdir(parents=True, exist_ok=True)
    (repo / ".ai/tasks/TASK-105.yaml").write_text(TASK_105_SOURCE, encoding="utf-8")
    (repo / ".ai/executor-profiles.yaml").write_text(yaml.safe_dump(POLICY), encoding="utf-8")
    git(repo, "add", ".ai")
    git(repo, "commit", "-qm", "admission inputs")
    git(repo, "push", "-q", "origin", "main")
    return repo, remote


def primary(repo, **overrides):
    base = git(repo, "rev-parse", "HEAD")
    blob = git(repo, "rev-parse", f"{base}:.ai/tasks/TASK-105.yaml")
    kwargs = dict(executor="codex", repo=repo, native_runner=forbidden,
                  verification_runner=forbidden, attempt=op._RunAttempt(),
                  observation_tracker=RunObservationTracker("PRIMARY"), synchronize=True,
                  preflight_sha=base, task_revision=1, task_blob_sha=blob, task_commit_sha=base)
    kwargs.update(overrides)
    op._run_task_impl("TASK-105", **kwargs)


def assert_no_authority(facts):
    assert facts.schema == "owner-bound-provenance-v1"
    assert facts.source_state == State.UNKNOWN
    assert Gap.INDEPENDENT_ISSUER_UNAVAILABLE in facts.source_gaps
    assert facts.issuer_authenticated is False
    assert facts.reviewer_authority is False
    assert facts.raw_state == "RAW_UNAVAILABLE"


def test_actual_primary_owner_retains_exact_admission_profile_and_lease(tmp_path, intercepted):
    repo, _ = primary_repository(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    with pytest.raises(ReachedCompletion):
        primary(repo)
    owner = intercepted[0][0]
    facts = owner.owner_provenance
    assert facts.owner_origin == "OWNER_BOUND" and facts.status == State.OBSERVED
    identity = dict(facts.bindings)
    assert identity["task_id"] == "TASK-105" and identity["task_revision"] == 1
    assert identity["task_blob_sha"] == git(repo, "rev-parse", f"{base}:.ai/tasks/TASK-105.yaml")
    assert identity["task_authoring_commit_sha"] == identity["admission_main_sha"] == identity["base_sha"] == base
    assert identity["run_id"] == owner.run.run_id and identity["executor"] == "codex"
    assert identity["model"] == "fixture-model" and identity["reasoning_effort"] == "high"
    assert identity["guard"] == "PRIMARY_OWNER_LEASE" and identity["currentness_scope"] == "AT_ADMISSION"
    assert_no_authority(facts)
    assert Gap.REVIEW_UNAVAILABLE in join_owner_provenance(owner).gaps


def test_public_constructors_fixtures_and_capture_helpers_cannot_enroll(tmp_path, intercepted):
    repo, _ = primary_repository(tmp_path)
    with pytest.raises(ReachedCompletion):
        primary(repo)
    actual = intercepted[0][0]
    fake = RuntimeCompletion(repo=repo, state=actual.state, task=actual.task, run=actual.run,
        run_path=actual.run_path, verification_runner=forbidden, observation_tracker=None, error_type=op.OperatorError)
    provenance._observe_operator(fake)
    assert read_owner_provenance(fake).gaps == (Gap.OWNER_UNAVAILABLE,)
    # Copying sealed storage or reconstructing public facts cannot transfer it.
    fake._owner_provenance_snapshot = actual._owner_provenance_snapshot
    assert read_owner_provenance(fake).gaps == (Gap.OWNER_UNAVAILABLE,)
    assert read_owner_provenance(provenance.OwnerFacts(owner_origin="OWNER_BOUND")).gaps == (Gap.UNSUPPORTED_OWNER,)
    forged = IngressResult(operation="SUBMIT_REVIEW", canonical_sha="a" * 40)
    provenance._observe_review(forged)
    provenance._observe_carrier(forged)
    assert forged.owner_provenance.gaps == (Gap.OWNER_UNAVAILABLE,)
    with pytest.raises(TypeError):
        read_owner_provenance(actual, repo=repo)


@pytest.mark.parametrize("change,gap", [
    ("run", Gap.OWNER_BINDING_CHANGED), ("profile", Gap.OWNER_BINDING_CHANGED),
    ("lease", Gap.LEASE_BINDING_MISMATCH), ("main", Gap.MAIN_CHANGED),
    ("owner", Gap.OWNER_BINDING_CHANGED), ("detached", Gap.DETACHED_SUBJECT),
])
def test_read_only_owner_rejects_changed_bindings(tmp_path, intercepted, change, gap):
    repo, _ = primary_repository(tmp_path)
    with pytest.raises(ReachedCompletion):
        primary(repo)
    owner = intercepted[0][0]
    if change == "run":
        data = asdict(owner.run)
        data["base_sha"] = "b" * 40
        owner.run_path.write_text(json.dumps(data), encoding="utf-8")
    elif change == "profile":
        path = owner.state.execution_profiles / f"{owner.run.run_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["reasoning_effort"] = "low"
        path.write_text(json.dumps(data), encoding="utf-8")
    elif change == "lease":
        inputs = intercepted[1][0]
        inputs["leases"].release(inputs["lease"])
    elif change == "main":
        git(repo, "commit", "--allow-empty", "-qm", "later main")
    elif change == "detached":
        git(repo, "checkout", "--detach", "-q")
    else:
        owner.run = replace(owner.run, executor="antigravity")
    facts = owner.owner_provenance
    assert facts.status == State.BLOCK and gap in facts.gaps
    assert_no_authority(facts)


@pytest.mark.parametrize("overrides,message", [
    ({"task_blob_sha": "a" * 40}, "TASK blob does not match authorization"),
    ({"task_revision": 2}, "TASK id or revision does not match authorization"),
    ({"preflight_sha": "a" * 40}, "current HEAD does not match preflight state"),
])
def test_original_admission_rejections_remain_original(tmp_path, intercepted, overrides, message):
    repo, _ = primary_repository(tmp_path)
    with pytest.raises(op.OperatorError, match=message):
        primary(repo, **overrides)
    assert intercepted[0] == []


@pytest.mark.parametrize("corruption,gap", [
    ("run", Gap.RUN_BINDING_MISMATCH), ("profile", Gap.PROFILE_BINDING_MISMATCH),
    ("lease", Gap.LEASE_BINDING_MISMATCH), ("duplicate", Gap.RUN_BINDING_MISMATCH),
])
def test_handoff_rejects_corrupted_owner_metadata_before_capture(tmp_path, intercepted, monkeypatch, corruption, gap):
    repo, _ = primary_repository(tmp_path)
    factory = op.primary_dispatcher
    def corrupting_factory(**kwargs):
        dispatcher = factory(**kwargs)
        dispatch = dispatcher.dispatch_primary
        def dispatch_primary(**inputs):
            package = dispatch(**inputs)
            run = inputs["run"]
            state = op.runtime_paths(repo)
            if corruption == "lease":
                inputs["leases"].release(inputs["lease"])
                inputs["leases"].acquire(run)
            else:
                path = ((state.execution_profiles if corruption == "profile" else state.runs)
                        / f"{run.run_id}.json")
                data = json.loads(path.read_text(encoding="utf-8"))
                if corruption == "profile":
                    data["run_id"] = "RUN-105-999"
                elif corruption == "run":
                    data["executor"] = "antigravity"
                body = json.dumps(data)
                if corruption == "duplicate":
                    body = '{"run_id":"ambiguous",' + body[1:]
                path.write_text(body, encoding="utf-8")
            return package
        return SimpleNamespace(dispatch_primary=dispatch_primary)
    monkeypatch.setattr(op, "primary_dispatcher", corrupting_factory)
    with pytest.raises(ReachedCompletion):
        primary(repo)
    facts = intercepted[0][0].owner_provenance
    assert facts.status == State.BLOCK and gap in facts.gaps
    assert_no_authority(facts)


def test_capture_problem_does_not_override_completion_failure(tmp_path, intercepted, monkeypatch):
    repo, _ = primary_repository(tmp_path)
    original = op.OperatorError("original completion cause")
    owners = []
    def complete(owner, *args):
        owners.append(owner)
        raise original
    monkeypatch.setattr(RuntimeCompletion, "complete", complete)
    real_git = op._git
    def missing_main(repository, *args, **kwargs):
        if args == ("rev-parse", "refs/heads/main") and sys._getframe(1).f_code.co_name == "capture_operator":
            raise op.OperatorError("subordinate capture failure")
        return real_git(repository, *args, **kwargs)
    monkeypatch.setattr(op, "_git", missing_main)
    with pytest.raises(op.OperatorError) as raised:
        primary(repo)
    assert raised.value is original
    assert Gap.MAIN_UNAVAILABLE in owners[0].owner_provenance.gaps


def review_envelope(lineage, verdict="PASS"):
    body = {"review_id": "REVIEW-105-001", "reviewed_sha": lineage["candidate_sha"],
            "mode": "PRIMARY", "verdict": verdict, "acceptance": {"AC1": "PASS" if verdict == "PASS" else "FAIL"},
            "findings": [] if verdict == "PASS" else [{"id": "F1", "basis": "AC1", "action": "CODE_FIX",
                "location": "src/sample.py", "issue": "Defect.", "expected": "Fix defect."}]}
    return IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "SUBMIT_REVIEW", {"run_id": lineage["run_id"]},
                           {"expected_candidate_sha": lineage["candidate_sha"]}, yaml.safe_dump(body))


@pytest.mark.parametrize("verdict", ["PASS", "CHANGES_REQUIRED"])
def test_actual_review_ingress_binds_recorded_decision_without_actor_authority(tmp_path, verdict):
    lineage = setup_candidate_lineage(tmp_path)
    repo, remote = lineage["repo"], lineage["remote"]
    before = git(remote, "rev-parse", "main")
    envelope = review_envelope(lineage, verdict)
    result = execute_ingress(envelope, repo=repo)
    facts = result.owner_provenance
    bindings = dict(facts.bindings)
    assert facts.owner_origin == "OWNER_BOUND" and facts.status == State.UNKNOWN
    assert bindings["run_id"] == lineage["run_id"] and bindings["task_revision"] == 1
    assert bindings["artifacts_sha"] == git(remote, "rev-parse", f"refs/heads/aios/artifacts/{lineage['run_id']}")
    assert bindings["candidate_sha"] == lineage["candidate_sha"]
    assert bindings["decision_sha"] == result.canonical_sha and bindings["recorded_verdict"] == verdict
    assert Gap.CARRIER_ORIGIN_UNAVAILABLE in facts.gaps
    assert_no_authority(facts)
    assert git(remote, "rev-parse", "main") == before
    assert "owner_provenance" not in result.as_dict()
    replay = execute_ingress(envelope, repo=repo)
    assert replay.replayed and replay.canonical_sha == result.canonical_sha
    assert dict(replay.owner_provenance.bindings)["review_sha256"] == bindings["review_sha256"]
    assert Gap.REPLAY_BINDING_UNAVAILABLE in replay.owner_provenance.gaps
    with pytest.raises(AuthoringIngressError, match="conflicting review decision"):
        execute_ingress(replace(envelope, payload=envelope.payload + "# conflict\n"), repo=repo)


def test_issue_policy_owner_is_distinct_from_local_and_unverified_carrier(tmp_path, monkeypatch):
    lineage = setup_candidate_lineage(tmp_path)
    envelope = review_envelope(lineage, "CHANGES_REQUIRED")
    body = yaml.safe_dump({"format": envelope.format, "version": 1, "operation": envelope.operation,
                           "identity": dict(envelope.identity), "expected_state": dict(envelope.expected_state),
                           "payload": envelope.payload})
    event = {"action": "opened", "repository": {"full_name": "owner/repository"},
             "issue": {"number": 23, "user": {"login": "owner"}, "title": "[AIOS BRAIN INGRESS]", "body": body}}
    event_path, policy_path = tmp_path / "event.json", tmp_path / "policy.yaml"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    policy_path.write_text(yaml.safe_dump({"format": "AIOS_BRAIN_INGRESS_CARRIERS_POLICY", "version": 1,
        "github_issue": {"enabled": True, "repository": "owner/repository", "authorized_actors": ["owner"],
                         "title_marker": "[AIOS BRAIN INGRESS]", "max_body_bytes": 262144}}), encoding="utf-8")
    # Protected workflow labels are not an authenticated review-origin receipt.
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    delivery = deliver_event(event_path, policy_path, repo=lineage["repo"])
    facts = delivery.ingress_result.owner_provenance
    bindings = dict(facts.bindings)
    assert bindings["carrier_policy_state"] == "OWNER_OBSERVED"
    assert bindings["carrier_actor_recorded"] == "owner" and bindings["carrier_origin"] == "UNKNOWN"
    assert_no_authority(facts)
    event["issue"]["user"]["login"] = "anonymous"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    with pytest.raises(GitHubIssueIngressError, match="not authorized"):
        deliver_event(event_path, policy_path, repo=lineage["repo"])


def test_repair_actual_admission_preserves_correction_guard_and_unknown_authorization(tmp_path, intercepted):
    repo, _ = primary_repository(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    state = op.runtime_paths(repo)
    failure = {"kind": "FAILURE", "run_id": "RUN-105-001", "task": {"id": "TASK-105", "revision": 1},
               "executor": "codex", "base_sha": base, "failed_head_sha": base,
               "candidate": {"repairable": True, "changed_files": []}, "phase": "VERIFICATION"}
    (state.failures / "RUN-105-001.json").write_text(json.dumps(failure), encoding="utf-8")
    failed_run = Run.from_task(run_id="RUN-105-001", task=op.load_task(repo, "TASK-105"),
                              executor="codex", base_sha=base, workspace=str(repo))
    (state.runs / "RUN-105-001.json").write_text(json.dumps(asdict(failed_run)), encoding="utf-8")
    repair = {"repair_id": "REPAIR-105-001", "failed_run_id": "RUN-105-001", "failed_head_sha": base,
              "task": failure["task"], "action": "CODE_FIX", "modification_scope": ["src/sample.py"],
              "instructions": ["Fix defect."], "constraints": ["Bounded mutation authority only."]}
    with pytest.raises(ReachedCompletion):
        op._run_repair_impl("RUN-105-001", executor="codex", repo=repo, repair=repair,
            required_repair_sha=None, repair_dispatch_id=None, native_runner=forbidden, verification_runner=forbidden,
            attempt=op._RunAttempt(), observation_tracker=RunObservationTracker("REPAIR"), admission={},
            model=None, reasoning_effort=None, model_source=None, effort_source=None)
    facts = intercepted[0][0].owner_provenance
    bindings = dict(facts.bindings)
    assert facts.kind == "REPAIR" and bindings["guard"] == "REPAIR_ADMISSION"
    assert bindings["failed_run_id"] == "RUN-105-001" and bindings["executor"] == "codex"
    assert bindings["root_base_sha"] == bindings["result_base_sha"] == base
    assert Gap.CONTINUATION_UNAVAILABLE in facts.gaps
    assert "lease" not in " ".join(bindings)
    assert_no_authority(facts)


def remediation_lineage(tmp_path):
    lineage = setup_candidate_lineage(tmp_path)
    repo = lineage["repo"]
    execute_ingress(review_envelope(lineage, "CHANGES_REQUIRED"), repo=repo)
    payload = {"finding_id": "F1", "action": "CODE_FIX", "reviewed_sha": lineage["candidate_sha"],
               "modification_scope": ["src/sample.py"], "affected_verification": ["git diff --check"],
               "constraints": {"hard": ["Bounded mutation authority only."]}}
    envelope = IngressEnvelope("AIOS_INGRESS_ENVELOPE", 1, "AUTHOR_REMEDIATION",
        {"source_run_id": lineage["run_id"], "finding_id": "F1"},
        {"expected_reviewed_sha": lineage["candidate_sha"]}, payload)
    authorized = execute_ingress(audited_envelope(envelope, repo), repo=repo)
    return lineage, authorized


@pytest.mark.parametrize("direct", [False, True])
def test_actual_remediation_and_direct_candidate_preserve_predecessor(tmp_path, intercepted, direct):
    lineage, authorization = remediation_lineage(tmp_path)
    repo = lineage["repo"]
    with pytest.raises(ReachedCompletion):
        if direct:
            (repo / "src/sample.py").write_text("# fixed candidate\n", encoding="utf-8")
            git(repo, "add", "src/sample.py")
            git(repo, "commit", "-qm", "direct candidate")
            op._accept_candidate_impl("TASK-105", finding_id="F1", executor="codex", repo=repo,
                verification_runner=forbidden, observation_tracker=RunObservationTracker("REMEDIATION"),
                attempt=op._RunAttempt(), admission={})
        else:
            op._run_remediation_impl("TASK-105", finding_id="F1", source_run_id=lineage["run_id"],
                approved_remediation_sha=authorization.canonical_sha, executor="codex", repo=repo,
                native_runner=forbidden, verification_runner=forbidden, attempt=op._RunAttempt(),
                observation_tracker=RunObservationTracker("REMEDIATION"), admission={})
    facts = intercepted[0][0].owner_provenance
    bindings = dict(facts.bindings)
    kind = "DIRECT_CANDIDATE" if direct else "REMEDIATION"
    assert facts.kind == kind and bindings["guard"] == kind + "_ADMISSION"
    assert bindings["remediation_authorization_sha"] == authorization.canonical_sha
    assert bindings["predecessor_source_run_id"] == lineage["run_id"]
    assert bindings["predecessor_finding_id"] == "F1"
    assert "lease" not in " ".join(bindings)
    if direct:
        assert Gap.PROFILE_UNAVAILABLE in facts.gaps
    assert_no_authority(facts)


def test_private_raw_and_paths_never_enter_public_facts(tmp_path, intercepted):
    repo, _ = primary_repository(tmp_path)
    state = op.runtime_paths(repo)
    raw = state.verification / "private.log"
    raw.write_text("SECRET RAW COMMAND ENVIRONMENT\n", encoding="utf-8")
    with pytest.raises(ReachedCompletion):
        primary(repo)
    owner = intercepted[0][0]
    facts = owner.owner_provenance
    text = json.dumps(asdict(facts))
    assert "SECRET" not in text and str(repo) not in text and str(raw) not in text
    assert "git diff --check" not in text and "workspace" not in text
    assert facts.raw_state == "RAW_UNAVAILABLE"
    assert not list(state.results.glob("*.json"))
    assert raw.read_text(encoding="utf-8") == "SECRET RAW COMMAND ENVIRONMENT\n"


def test_join_rejects_unowned_review_and_different_admitted_run(tmp_path, intercepted):
    repo, _ = primary_repository(tmp_path / "primary")
    with pytest.raises(ReachedCompletion):
        primary(repo)
    owner = intercepted[0][0]
    assert join_owner_provenance(owner, IngressResult(operation="SUBMIT_REVIEW")).status == State.BLOCK
    lineage = setup_candidate_lineage(tmp_path / "review", run_id="RUN-105-999")
    result = execute_ingress(review_envelope(lineage, "CHANGES_REQUIRED"), repo=lineage["repo"])
    assert join_owner_provenance(owner, result).gaps == (Gap.REVIEW_BINDING_MISMATCH,)
    assert_no_authority(join_owner_provenance(owner, result))


def test_exact_owner_join_retains_recorded_review_and_all_source_gaps(tmp_path, intercepted):
    repo, _ = primary_repository(tmp_path)
    with pytest.raises(ReachedCompletion):
        primary(repo)
    owner = intercepted[0][0]
    candidate = git(repo, "rev-parse", "HEAD")
    # Fixture artifact content exercises real ingress validation. It never
    # authenticates a terminal Runtime source (completion was intercepted).
    artifact = {"result": {"head_sha": candidate, "claims": [{"id": "C1", "satisfies": ["AC1"],
        "claim": "Fixture content only.", "evidence": ["E1"]}], "changed_files": ["src/sample.py"], "unresolved": []},
        "evidence": [{"evidence_id": "E1", "run_id": owner.run.run_id, "subject_sha": candidate,
            "type": "verification", "source": {"command": "git diff --check"},
            "result": {"exit_code": 0, "summary": "fixture"}, "raw": {"path": ".git/fixture/E1.log"}}]}
    artifact_path = tmp_path / "fixture-result.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    transport_post_pass(repo, run_id=owner.run.run_id, head_sha=candidate,
                        run_path=owner.run_path, result_path=artifact_path)
    lineage = {"run_id": owner.run.run_id, "candidate_sha": candidate}
    decision = execute_ingress(review_envelope(lineage, "CHANGES_REQUIRED"), repo=repo)
    facts = join_owner_provenance(owner, decision)
    assert facts.status == State.UNKNOWN
    bindings = dict(facts.bindings)
    assert bindings["review_decision_sha"] == decision.canonical_sha
    assert bindings["review_recorded_verdict"] == "CHANGES_REQUIRED"
    assert Gap.CARRIER_ORIGIN_UNAVAILABLE in facts.gaps
    assert Gap.TERMINAL_SOURCE_UNAVAILABLE in facts.gaps and Gap.RAW_UNAVAILABLE in facts.gaps
    assert_no_authority(facts)
    assert not list(owner.state.results.glob("*.json"))
