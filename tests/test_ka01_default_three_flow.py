"""Prospective ordinary-entrypoint coverage; Runtime owns executing this file.

Synthetic sessions below test deterministic counterexamples. The mandatory
installed-native cases retain the actual process, model, observer and verifier.
Canonical source/failure/Review/authorization refs use real isolated Git repos.
"""

from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess

import jsonschema
import pytest
import yaml

from aios_renew import codex_adapter, codex_live_transport as native, ka01_admission as ka01, operator
from aios_renew.artifacts import ResultPackage, validate_structural_result
from aios_renew.execution_profile import default_execution_profile
from aios_renew.intra_run_proof_contract import parse_native_operation_checkpoint, parse_native_primary_checkpoint
from aios_renew.run import Run
from aios_renew.review_transport import transport_failure
from aios_renew.runtime import RuntimeCompletion, persist_failure, persist_preverification_candidate, primary_completion_policy
from aios_renew.verification_contract import VerificationContractError
from tests.git_fixture_support import commit_fixture_state
from tests.operator_test_support import git, static_payload
from tests.test_ka01_v13_activation import ProductionFixture
from tests.test_ka01_v13_intra_run_native import FixtureSession


COMMAND = "python -m pytest tests/test_output.py -n 0 --dist no --max-worker-restart 0"
GUARD = "python -m pytest tests/test_unrelated.py -n 0 --dist no --max-worker-restart 0"
PROOF = ("from pathlib import Path\n"
    "def test_output():\n    assert Path('OUTPUT.txt').read_text() in ('reviewed\\n', 'complete\\n')\n")


def with_original_guard(source):
    task = yaml.safe_load(source)
    task["verification"]["required"].append(GUARD)
    return yaml.safe_dump(task, sort_keys=False)


class ThreeFlowFixture(ProductionFixture):
    def __init__(self, root, monkeypatch, *, operation="PRIMARY", action="CODE_FIX", first_fail=True,
                 historical=False, policy="minimum-sufficient-v1", reusable=False, proof_source=PROOF, omit_files=(),
                 task_transform=with_original_guard):
        super().__init__(root, monkeypatch, interactive=True, default=True, first_fail=first_fail,
            policy=policy, proof_source=proof_source, task_transform=task_transform,
            omit_files=omit_files,
            extra_files={"tests/test_unrelated.py": "def test_unrelated():\n    assert True\n",
                # Historical ordinary verification may write Python/pytest
                # caches, just as in the real repository's ignored layout.
                ".gitignore": "__pycache__/\n.pytest_cache/\n"})
        self.root, self.control = root, self.repo
        self.operation, self.action = operation, action
        self.reusable = reusable
        self.first_fail = first_fail
        self.commands_per_checkpoint = 1 if operation == "REMEDIATION" else 2
        self.output = "incomplete\n" if first_fail else "complete\n"
        self.source_id = "RUN-101-000"
        self.authorization_ref = self.authorization_sha = None
        self.root_base = self.base
        original = operator._activate_ka01_default

        def observe(**kwargs):
            self.owner = kwargs["runtime"]
            self.run = self.owner.run
            self.repo = self.owner.repo
            original(**kwargs)
            self.policy = self.owner._ka01_budget.policy
        monkeypatch.setattr(operator, "_activate_ka01_default", observe)
        if operation == "REPAIR":
            self.seed_repair()
        elif operation == "REMEDIATION":
            self.seed_remediation()
        if historical:
            assert operation != "PRIMARY"
            # Keep control on canonical main and execute only the exact
            # failed/reviewed child in the original isolated subject path.
            git(self.control, "checkout", "--quiet", "main")
            git(self.control, "reset", "--hard", self.base)

    @staticmethod
    def commit(repo, path, message):
        return commit_fixture_state(repo, paths=(path,), message=message,
            user_name="Three-flow fixture", user_email="three-flow@example.invalid")

    def author(self, ref, files):
        author = self.root / ("authority-" + self.operation.lower())
        subprocess.run(("git", "clone", "--quiet", str(self.remote), str(author)), check=True)
        git(author, "checkout", "--quiet", "--detach", self.subject)
        git(author, "config", "user.name", "Canonical family author fixture")
        git(author, "config", "user.email", "authority@example.invalid")
        for name, content in files.items():
            path = author / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            if name.startswith(".ai/reviews/"):
                git(author, "add", name)
                git(author, "commit", "--quiet", "-m", "exact canonical Reviewer decision")
                git(author, "push", "--quiet", "origin", "HEAD:refs/heads/aios/review-decision/" + self.source_id)
        git(author, "add", ".ai")
        git(author, "commit", "--quiet", "-m", "exact family authorization")
        sha = git(author, "rev-parse", "HEAD")
        git(author, "push", "--quiet", "origin", "HEAD:" + ref)
        self.authorization_ref, self.authorization_sha = ref, sha

    def seed_repair(self):
        (self.repo / "OUTPUT.txt").write_text("reviewed\n" if self.reusable else "failed original candidate\n", encoding="utf-8")
        self.subject = self.commit(self.repo, "OUTPUT.txt", "failed original candidate")
        prior = Run.from_task(run_id=self.source_id, task=self.task, executor="codex",
            base_sha=self.base, workspace=str(self.repo))
        path = self.state.runs / (self.source_id + ".json")
        path.write_bytes(ka01._bytes(asdict(prior)))
        if self.reusable:
            payload = static_payload()
            payload["result"].update(head_sha=self.subject, changed_files=["OUTPUT.txt"])
            persist_preverification_candidate(self.state.preverification / (self.source_id + ".json"),
                task=self.task, run=prior, subject_sha=self.subject,
                package=ResultPackage(validate_structural_result(payload["result"]), ()))
        persist_failure(self.repo, state=self.state, task=self.task, run=prior, run_path=path,
            failure=operator.OperatorError("original completion interrupted"), transport=not self.reusable)
        if self.reusable:
            # Publish the exact preserved structure with its original failure.
            # A local-only package cannot override an absent canonical sidecar.
            transport_failure(self.repo, run_id=self.source_id, head_sha=self.subject,
                run_path=path, failure_path=self.state.failures / (self.source_id + ".json"),
                preverification_path=self.state.preverification / (self.source_id + ".json"))
        repair = dict(repair_id="REPAIR-101-DEFAULT", failed_run_id=self.source_id,
            failed_head_sha=self.subject, task=asdict(prior.task), action=self.action,
            modification_scope=[] if self.reusable else ["OUTPUT.txt"], instructions=[
                "Commit OUTPUT.txt containing complete followed by a newline.",
                "For this bounded transport exercise the first candidate alone must contain incomplete followed by a newline; after factual Runtime feedback independently correct it."],
            constraints=list(self.task.constraints.hard))
        self.author("refs/heads/aios/repair/" + self.source_id,
            {".ai/transport/repair.json": json.dumps(repair)})

    def seed_remediation(self):
        (self.repo / "OUTPUT.txt").write_text("reviewed\n", encoding="utf-8")
        self.subject = self.commit(self.repo, "OUTPUT.txt", "original reviewed candidate")
        prior = Run.from_task(run_id=self.source_id, task=self.task, executor="codex",
            base_sha=self.base, workspace=str(self.repo))
        path = self.state.runs / (self.source_id + ".json")
        path.write_bytes(ka01._bytes(asdict(prior)))
        payload = static_payload()
        payload["result"].update(head_sha=self.subject, changed_files=["OUTPUT.txt"])
        # Seed an already-admitted historical owner, using actual original
        # Runtime verification and transport rather than a fake PASS artifact.
        owner = RuntimeCompletion(repo=self.repo, state=self.state, task=self.task, run=prior,
            run_path=path, verification_runner=subprocess.run, observation_tracker=None,
            error_type=operator.OperatorError)
        owner.complete(ResultPackage(validate_structural_result(payload["result"]), ()),
            primary_completion_policy(self.task, base_sha=self.base))
        expected = "Commit OUTPUT.txt containing complete followed by a newline."
        if self.first_fail:
            # The original TASK already has a reviewed candidate. Bind this
            # exercise's C1 to the new narrow REMEDIATION rather than relying
            # on the inherited TASK's ambiguous first-candidate constraint.
            expected = (
                "In this newly admitted REMEDIATION RUN, commit OUTPUT.txt containing "
                "incomplete followed by a newline as C1 and return that committed "
                "candidate without completing the correction. The already reviewed "
                "TASK candidate is not C1 for this RUN. After factual Runtime feedback, "
                "independently commit OUTPUT.txt containing complete followed by a "
                "newline as C2 in the same original native session and RUN.")
        review = dict(review_id="REVIEW-101-001", reviewed_sha=self.subject, mode="PRIMARY",
            verdict="CHANGES_REQUIRED", acceptance={"AC1": "FAIL"}, findings=[dict(id="R1", basis="AC1",
                action="CODE_FIX", location="OUTPUT.txt", issue="Output needs the approved complete value.",
                expected=expected)])
        remediation = dict(finding_id="R1", action="CODE_FIX", reviewed_sha=self.subject,
            modification_scope=["OUTPUT.txt"], verification=dict(policy=self.task.verification.policy, affected=[COMMAND]),
            constraints=dict(hard=list(self.task.constraints.hard)))
        self.author("refs/heads/aios/remediation/" + self.source_id + "-R1", {
            ".ai/reviews/REVIEW-101-001.yaml": yaml.safe_dump(review),
            ".ai/remediations/REMEDIATION-101-001-R1.yaml": yaml.safe_dump(remediation)})

    def execute(self, **changes):
        arguments = dict(executor="codex", repo=self.control, native_runner=self.native,
            verification_runner=self.verify, model=self.profile.model,
            reasoning_effort=self.profile.reasoning_effort)
        arguments.update(changes)
        if self.operation == "PRIMARY":
            return operator.run_task(self.task.task_id, **arguments)
        if self.operation == "REPAIR":
            return operator.run_repair(self.source_id, **arguments)
        return operator.run_remediation(self.task.task_id, source_run_id=self.source_id,
            finding_id="R1", approved_remediation_sha=self.authorization_sha, **arguments)


class OperationFixtureSession(FixtureSession):
    """Synthetic guard fixture only; installed-native cases never use it."""
    def turn(self, **kwargs):
        self.output_schemas.append(kwargs["output_schema"])
        turn = super().turn(**kwargs)
        data = turn.output
        if self.f.operation == "REMEDIATION":
            data["package"]["result"]["claims"] = []
        if self.f.operation == "REPAIR":
            # Preserve the structural REPAIR contract: Runtime derives the
            # cumulative root delta; native may return no changed-files list.
            data["package"]["result"]["changed_files"] = []
        self.last_turn = replace(turn, output_json=ka01._bytes(data))
        return self.last_turn


def synthetic_session(f, monkeypatch, hook=None):
    sessions = []
    def construct(**kwargs):
        session = OperationFixtureSession(f, **kwargs)
        session.output_schemas = []
        session.hook = hook
        sessions.append(session)
        return session
    monkeypatch.setattr(codex_adapter, "CodexLiveTransport", construct)
    return sessions


def checkpoints(f):
    return [parse_native_operation_checkpoint((f.owner._checkpoint_directory() / f"n{i:03d}.json").read_bytes())
        for i in range(1, len(f.owner._interactive_checkpoints) + 1)]


def assert_original_completion(f, summary, *, corrected=True):
    budget = f.owner._ka01_budget
    records = checkpoints(f)
    assert budget.default and budget.production and budget.interactive and budget.operation == f.operation
    assert budget.native_calls == 1 and budget.pending is None and budget.stopped
    assert len(records) == (2 if corrected else 1)
    assert summary.ka01["operation"] == f.operation and summary.ka01["configuration"] == ka01.DEFAULT_MODE
    assert summary.ka01["consent_sha256"] is summary.ka01["selection_sha256"] is None
    assert summary.ka01["effective_limits"] == ka01.DEFAULTS
    assert summary.ka01["checkpoints_spent"] == len(records)
    assert summary.ka01["corrections_spent"] == int(corrected)
    assert summary.ka01["review"] == summary.ka01["publication"] == "NOT_OBSERVED"
    assert not f.issues and not git(f.remote, "for-each-ref", "--format=%(refname)", "refs/heads/aios/ka01-v13-consent")
    assert records[-1].data["failed_identities"] == []
    for record in records:
        assert record.data["binding"]["operation"] == f.operation
        assert record.data["binding"]["session"] == budget.session_identity
        assert record.data["binding"]["operation_source_sha256"] == summary.ka01["operation_source_sha256"]
        with pytest.raises(VerificationContractError):
            parse_native_primary_checkpoint(record.content_json.encode("ascii"))
        from aios_renew.artifacts import ArtifactValidationError, validate_evidence
        malformed = record.data["evidence"][0]
        malformed["verification"]["binding"]["original_proof"] = {}
        with pytest.raises(ArtifactValidationError):
            validate_evidence(malformed)
    expected_commands = (COMMAND,) if f.operation == "REMEDIATION" else (COMMAND, GUARD)
    assert len(f.verification_calls) == len(records) * len(expected_commands)
    package = json.loads(summary.result_path.read_bytes())
    assert tuple(e["source"]["command"] for e in package["evidence"]) == expected_commands
    assert {e["subject_sha"] for e in package["evidence"]} == {summary.head_sha}
    assert package["result"]["changed_files"] == ["OUTPUT.txt"]
    assert bool(package["result"]["claims"]) == (f.operation != "REMEDIATION")
    if f.operation != "REMEDIATION":
        assert {ac for c in package["result"]["claims"] for ac in c["satisfies"]} == {a.id for a in f.task.acceptance}
    if corrected:
        first, last = records
        assert first.data["failed_identities"] and last.data["predecessor_digest"] == first.digest
        assert first.data["binding"]["candidate_sha"] != last.data["binding"]["candidate_sha"]
        envelope = json.loads(f.owner._interactive_feedback[1])
        assert envelope["feedback"]["operation"] == f.operation
        assert envelope["feedback"]["acceptance_ids"] == ([] if f.operation == "REMEDIATION" else ["AC1"])
    assert len(list(f.state.results.glob(summary.run_id + ".json"))) == 1
    assert not (f.state.failures / (summary.run_id + ".json")).exists()
    assert git(f.remote, "rev-parse", "refs/heads/aios/review/" + summary.run_id) == summary.head_sha
    assert git(f.remote, "rev-parse", "refs/heads/main") == f.base
    if f.operation == "REPAIR":
        source = json.loads(budget.authority)["source"]
        assert source["failed_head_sha"] == summary.failed_head_sha == f.subject
        assert source["root_base_sha"] == f.root_base and source["predecessor"] == f.source_id
    if f.operation == "REMEDIATION":
        source = json.loads(budget.authority)["source"]
        assert source["reviewed_sha"] == summary.reviewed_sha == f.subject
        assert source["finding_id"] == "R1" and source["review_id"] == "REVIEW-101-001"


@pytest.mark.parametrize("operation,action", [("PRIMARY", "CODE_FIX"), ("REPAIR", "CODE_FIX"),
    ("REPAIR", "CONTINUE_IMPLEMENTATION"), ("REMEDIATION", "CODE_FIX")])
def test_ordinary_default_without_consent_uses_original_family_and_one_terminal(tmp_path, monkeypatch, operation, action):
    f = ThreeFlowFixture(tmp_path / "default", monkeypatch, operation=operation, action=action)
    sessions = synthetic_session(f, monkeypatch)
    summary = f.execute()
    assert len(sessions) == 1 and sessions[0].calls == 2 and sessions[0].closed
    # Both C1 and C2 must receive the original TASK's acceptance contract.
    candidate = sessions[0].last_turn.output
    for schema in sessions[0].output_schemas:
        jsonschema.validate(candidate, schema)
        if operation != "REMEDIATION":
            invalid = json.loads(json.dumps(candidate))
            invalid["package"]["result"]["claims"][0]["satisfies"].append("C-SCOPE")
            with pytest.raises(jsonschema.ValidationError):
                jsonschema.validate(invalid, schema)
    assert_original_completion(f, summary)
    terminal = summary.result_path.read_bytes()
    with pytest.raises(VerificationContractError):
        f.owner.complete(ResultPackage(validate_structural_result(f.package["result"]), ()), f.policy)
    assert summary.result_path.read_bytes() == terminal


@pytest.mark.parametrize("operation", ["PRIMARY", "REPAIR", "REMEDIATION"])
def test_installed_native_default_original_session_c1_f1_c2(tmp_path, monkeypatch, operation):
    """Mandatory real native three-flow proof, never skip/xfail or substitution."""
    assert codex_adapter.CodexLiveTransport is native.CodexLiveTransport
    f = ThreeFlowFixture(tmp_path / "installed", monkeypatch, operation=operation)
    def forbidden_exec(*args, **kwargs):
        pytest.fail("BLOCKER: default attempted a one-shot or second native Executor")
    try:
        summary = f.execute(native_runner=forbidden_exec)
    except native.NativeSessionStop as exc:
        pytest.fail("BLOCKER: installed native Codex unavailable: " + str(exc), pytrace=False)
    session = f.owner._ka01_budget.session
    assert type(session) is native.CodexLiveTransport
    assert isinstance(session._original_process, subprocess.Popen)
    assert session._closed and len(session._turns) == 2
    assert_original_completion(f, summary)


@pytest.mark.parametrize("operation", ["REPAIR", "REMEDIATION"])
def test_default_historical_subject_retains_original_runtime_storage_and_control(tmp_path, monkeypatch, operation):
    f = ThreeFlowFixture(tmp_path / "historical", monkeypatch, operation=operation, historical=True)
    before = git(f.control, "rev-parse", "HEAD")
    synthetic_session(f, monkeypatch)
    summary = f.execute()
    assert f.owner.repo != f.control and not f.owner.repo.exists()
    assert git(f.control, "rev-parse", "HEAD") == before == f.base
    assert summary.result_path.parent == f.state.results
    # Captures belong to the original control owner even after subject cleanup.
    assert summary.ka01["operation"] == operation and summary.ka01["corrections_spent"] == 1


@pytest.mark.parametrize("operation", ["REPAIR", "REMEDIATION"])
def test_no_extra_ka01_consent_never_replaces_original_family_permission(tmp_path, monkeypatch, operation):
    f = ThreeFlowFixture(tmp_path / "missing-authority", monkeypatch, operation=operation)
    git(f.remote, "update-ref", "-d", f.authorization_ref)
    sessions = synthetic_session(f, monkeypatch)
    with pytest.raises(operator.OperatorError):
        f.execute()
    assert not sessions and f.owner is None and not f.verification_calls
    assert not (f.state.runs / "RUN-101-001.json").exists()


@pytest.mark.parametrize("operation", ["PRIMARY", "REPAIR", "REMEDIATION"])
@pytest.mark.parametrize("executor", ["antigravity", "antigravity-minimax"])
def test_default_unproven_provider_stops_before_any_model_call(tmp_path, monkeypatch, operation, executor):
    f = ThreeFlowFixture(tmp_path / "unsupported", monkeypatch, operation=operation)
    sessions = synthetic_session(f, monkeypatch)
    arguments = dict(executor=executor)
    if executor == "antigravity":
        profile = default_execution_profile(executor, "AUTHORIZATION", f.control)
        arguments.update(model=profile.model, reasoning_effort=profile.reasoning_effort)
    else:
        arguments.update(model=None, reasoning_effort=None)
    with pytest.raises(VerificationContractError, match="DEFAULT_NATIVE_CAPABILITY_UNSUPPORTED"):
        f.execute(**arguments)
    assert not sessions and not f.native_calls and not f.verification_calls
    observation = json.loads((f.state.observations / "RUN-101-001.ka01.json").read_bytes())
    assert observation["operation"] == operation and observation["final_status"] == "STOP"
    assert observation["native_invocations"] == 0


@pytest.mark.parametrize("defect", ["receipt", "session", "operation", "profile", "lease", "source", "authorization", "crash",
    "same-source", "no-progress", "checkpoint", "feedback", "wrong-executor"])
@pytest.mark.parametrize("operation", ["PRIMARY", "REPAIR", "REMEDIATION"])
def test_default_correction_cannot_escape_original_authority(tmp_path, monkeypatch, operation, defect):
    # A different irrelevant output must leave authenticated proof facts equal;
    # interpolating the output into assertion detail would change its fingerprint.
    proof = ("from pathlib import Path\n"
        "def test_output():\n"
        "    if Path('OUTPUT.txt').read_text() not in ('reviewed\\n', 'complete\\n'):\n"
        "        raise AssertionError('required output is absent')\n") if defect == "no-progress" else PROOF
    f = ThreeFlowFixture(tmp_path / "counterexample", monkeypatch, operation=operation, proof_source=proof)
    f.receipt_defect = "source_sha" if defect == "receipt" else None
    f.same_source, f.no_progress = defect == "same-source", defect == "no-progress"
    def corrupt(session, ordinal, prompt):
        if ordinal != 2:
            return
        budget = f.owner._ka01_budget
        if defect == "session":
            session.thread_id = "wrong-thread"
        elif defect == "operation":
            budget.native_dispatcher._operation = "REPAIR" if operation != "REPAIR" else "PRIMARY"
        elif defect == "profile":
            budget.native_adapter._execution_profile = replace(f.profile, model="substituted")
        elif defect == "lease":
            budget.leases.release(budget.lease)
        elif defect == "source":
            if operation == "PRIMARY":
                # Transfer the real candidate object before moving main; an
                # invalid update-ref must not stand in for the source CAS guard.
                git(f.repo, "push", "--quiet", "origin", "HEAD:refs/heads/main")
            else:
                git(f.remote, "update-ref", "refs/heads/main", f.run.base_sha)
        elif defect == "authorization":
            if operation == "PRIMARY":
                path = f.repo / ".ai/tasks/TASK-101.yaml"
                path.write_text(path.read_text() + "\n# source changed\n")
            else:
                git(f.remote, "update-ref", "-d", f.authorization_ref)
        elif defect == "crash":
            raise KeyboardInterrupt()
        elif defect in {"checkpoint", "feedback"}:
            name = "n001.json" if defect == "checkpoint" else "n001.feedback.json"
            (f.owner._checkpoint_directory() / name).write_bytes(b"{}")
        elif defect == "wrong-executor":
            budget.native_dispatcher._selected_executor = "antigravity"
    sessions = synthetic_session(f, monkeypatch, corrupt)
    with pytest.raises((RuntimeError, VerificationContractError, KeyboardInterrupt)):
        f.execute()
    assert len(sessions) == 1 and sessions[0].closed and f.owner._ka01_budget.stopped
    assert not (f.state.results / "RUN-101-001.json").exists()
    with pytest.raises(VerificationContractError):
        f.owner._ka01_budget.open_native_session(lambda: pytest.fail("STOP cannot respawn"))


@pytest.mark.parametrize("operation", ["PRIMARY", "REPAIR", "REMEDIATION"])
@pytest.mark.parametrize("resource", ["corrections", "checkpoints", "wall", "verification"])
def test_default_finite_resources_stop_the_same_original_run(tmp_path, monkeypatch, operation, resource):
    f = ThreeFlowFixture(tmp_path / "resource", monkeypatch, operation=operation)
    sessions = synthetic_session(f, monkeypatch)
    def exhaust(f):
        budget = f.owner._ka01_budget
        if resource in {"wall", "verification"}:
            now, real = ka01.time.monotonic_ns(), ka01.time.time_ns()
            delta = budget.wall_limit_ns if resource == "wall" else budget.verification_limit_ns
            monkeypatch.setattr(ka01.time, "monotonic_ns", lambda: now + delta)
            monkeypatch.setattr(ka01.time, "time_ns", lambda: real + delta)
        else:
            # Mutation of original facts is never accepted as a new limit.
            setattr(budget, resource + "_reserved", 3)
    f.verification_hook = exhaust
    with pytest.raises(VerificationContractError):
        f.execute()
    assert len(sessions) == 1 and sessions[0].calls == 1 and sessions[0].closed
    assert not f.owner._interactive_feedback and not (f.state.results / "RUN-101-001.json").exists()


@pytest.mark.parametrize("operation", ["PRIMARY", "REPAIR", "REMEDIATION"])
def test_missing_native_observer_preflight_stops_before_coding(tmp_path, monkeypatch, operation):
    # Historical minimum-sufficient-v1 proof can pass without a native observer.
    # Omit its loader too, so seed verification is ordinary pytest; the newly
    # admitted default must independently reject the missing complete observer.
    f = ThreeFlowFixture(tmp_path / "observer", monkeypatch, operation=operation,
        omit_files=("tests/bp_v4_probe_plugin.py", "tests/conftest.py"))
    sessions = synthetic_session(f, monkeypatch)
    with pytest.raises(VerificationContractError, match="NATIVE_COMPLETE_OBSERVER_UNAVAILABLE"):
        f.execute()
    assert not sessions and not f.verification_calls


@pytest.mark.parametrize("defect", ["missing", "changed"])
def test_global_source_never_silently_downgrades_to_legacy(tmp_path, monkeypatch, defect):
    f = ThreeFlowFixture(tmp_path / "global-source", monkeypatch)
    path = f.repo / ka01.NORMATIVE_PATH
    if defect == "missing":
        git(f.repo, "rm", "--quiet", ka01.NORMATIVE_PATH)
    else:
        path.write_text(path.read_text(encoding="utf-8") + "\nUnratified policy.\n", encoding="utf-8")
        git(f.repo, "add", ka01.NORMATIVE_PATH)
    git(f.repo, "commit", "--quiet", "-m", "invalid global source")
    git(f.repo, "push", "--quiet", "origin", "HEAD:refs/heads/main")
    sessions = synthetic_session(f, monkeypatch)
    with pytest.raises(VerificationContractError):
        f.execute()
    assert not sessions and not f.native_calls and not f.verification_calls


def test_default_v2_obligations_are_preserved_without_a_second_verifier(tmp_path, monkeypatch):
    f = ThreeFlowFixture(tmp_path / "v2", monkeypatch, policy="minimum-sufficient-v2")
    synthetic_session(f, monkeypatch)
    assert_original_completion(f, f.execute())


@pytest.mark.parametrize("operation", ["PRIMARY", "REPAIR", "REMEDIATION"])
def test_default_unavailable_codex_channel_is_an_explicit_original_run_stop(tmp_path, monkeypatch, operation):
    f = ThreeFlowFixture(tmp_path / "unavailable-codex", monkeypatch, operation=operation)
    def unavailable(**kwargs):
        raise native.NativeSessionStop("CODEX_LIVE_STOP_NATIVE_BINARY_UNAVAILABLE")
    monkeypatch.setattr(codex_adapter, "CodexLiveTransport", unavailable)
    with pytest.raises(native.NativeSessionStop, match="NATIVE_BINARY_UNAVAILABLE"):
        f.execute()
    observation = json.loads((f.state.observations / "RUN-101-001.ka01.json").read_bytes())
    assert observation["operation"] == operation and observation["final_status"] == "STOP"
    assert observation["native_feedback_delivery"] == "BLOCKER_NATIVE_SESSION_UNAVAILABLE"
    assert not f.verification_calls and not f.native_calls


def test_default_cannot_manufacture_original_task_admission(tmp_path, monkeypatch):
    f = ThreeFlowFixture(tmp_path / "task-authority", monkeypatch)
    git(f.repo, "rm", "--quiet", ".ai/tasks/TASK-101.yaml")
    git(f.repo, "commit", "--quiet", "-m", "TASK authority absent")
    git(f.repo, "push", "--quiet", "origin", "HEAD:refs/heads/main")
    sessions = synthetic_session(f, monkeypatch)
    with pytest.raises(operator.OperatorError):
        f.execute()
    assert not sessions and f.owner is None and not list(f.state.runs.glob("*.json"))


@pytest.mark.parametrize("operation", ["PRIMARY", "REPAIR", "REMEDIATION"])
def test_default_first_pass_consumes_one_checkpoint_without_feedback(tmp_path, monkeypatch, operation):
    f = ThreeFlowFixture(tmp_path / "first-pass", monkeypatch, operation=operation, first_fail=False)
    sessions = synthetic_session(f, monkeypatch)
    assert_original_completion(f, f.execute(), corrected=False)
    assert sessions[0].calls == 1 and not f.owner._interactive_feedback


def test_default_does_not_force_a_model_for_authorized_executorless_reuse(tmp_path, monkeypatch):
    f = ThreeFlowFixture(tmp_path / "executorless", monkeypatch, operation="REPAIR",
        action="FINALIZE_CANDIDATE", reusable=True)
    sessions = synthetic_session(f, monkeypatch)
    # Legacy executorless completion owns its original proof; no live session
    # is created merely to enroll an inherited RUN Executor label.
    summary = f.execute(executor=None, model=None, reasoning_effort=None, verification_runner=subprocess.run)
    assert not sessions and not f.native_calls and f.owner is None and summary.ka01 is None
    assert summary.head_sha == f.subject
    assert not (f.state.execution_profiles / (summary.run_id + ".json")).exists()
    observation = json.loads((f.state.observations / (summary.run_id + ".json")).read_bytes())
    assert observation["operation"] == "REPAIR" and observation["executor_invoked"] is False


@pytest.mark.parametrize("operation", ["PRIMARY", "REPAIR", "REMEDIATION"])
def test_default_duplicate_receipt_stops_before_another_checkpoint(tmp_path, monkeypatch, operation):
    f = ThreeFlowFixture(tmp_path / "duplicate", monkeypatch, operation=operation)
    sessions = synthetic_session(f, monkeypatch)
    accept = RuntimeCompletion._accept_interactive_correction
    def duplicate(owner, **kwargs):
        accept(owner, **kwargs)
        accept(owner, **kwargs)
    monkeypatch.setattr(RuntimeCompletion, "_accept_interactive_correction", duplicate)
    with pytest.raises(VerificationContractError, match="FORGED_DUPLICATE_STALE"):
        f.execute()
    assert sessions[0].calls == 2 and len(f.owner._interactive_checkpoints) == 1
    assert f.owner._ka01_budget.corrections_spent == 1


# TASK-427: synthetic family/native fixtures exercise real isolated Git and the
# original verifier/observer. These local cases are never canonical AIOS proof.
KERNEL_SCOPE = ("src/aios_renew/runtime.py", "src/aios_renew/operator.py",
    "src/aios_renew/ka01_admission.py", "src/aios_renew/verification_contract.py")
KERNEL_PROOF = ("from pathlib import Path\n"
    "import aios_renew.verification_contract as candidate\n"
    "def test_output():\n"
    "    assert Path(candidate.__file__).resolve().is_relative_to(Path.cwd())\n"
    "    if Path('OUTPUT.txt').read_text() == 'reviewed\\n':\n        return\n"
    "    assert candidate.TASK427_CANDIDATE is True\n"
    "    assert Path('OUTPUT.txt').read_text() == 'complete\\n'\n")


class KernelUpgradeFixture(ThreeFlowFixture):
    def __init__(self, root, monkeypatch, *, operation="PRIMARY", parallel=False, first_fail=True):
        def contract(source):
            data = yaml.safe_load(with_original_guard(source))
            data["scope"]["modify"].extend(KERNEL_SCOPE)
            data["constraints"]["hard"] = [c for c in data["constraints"]["hard"] if "Do not alter" not in c]
            if parallel:
                data["verification"]["required"][0] = COMMAND.replace("-n 0 --dist no", "-n 2 --dist load")
            return yaml.safe_dump(data, sort_keys=False)
        # The exact reviewed value alone may seed the historical predecessor;
        # every upgrade candidate must exercise the changed candidate module.
        super().__init__(root, monkeypatch, operation=operation, first_fail=first_fail,
            task_transform=contract, proof_source=KERNEL_PROOF)

    def author(self, ref, files):
        for name, raw in tuple(files.items()):
            if name.endswith("repair.json"):
                data = json.loads(raw)
                data["modification_scope"].extend(KERNEL_SCOPE)
                files[name] = json.dumps(data)
            elif name.startswith(".ai/remediations/"):
                data = yaml.safe_load(raw)
                data["modification_scope"].extend(KERNEL_SCOPE)
                files[name] = yaml.safe_dump(data)
        return super().author(ref, files)


class KernelUpgradeSession(OperationFixtureSession):
    def turn(self, **kwargs):
        turn = super().turn(**kwargs)
        if self.calls == 1:
            for name in KERNEL_SCOPE:
                path = self.f.repo / name
                suffix = "\nTASK427_CANDIDATE = True\n"
                if name.endswith("verification_contract.py"):
                    # If the candidate supplies observer helpers, even a passing
                    # test cannot finish observation. The original must be used.
                    suffix += "def pytest_collection_identity(*args, **kwargs):\n    raise RuntimeError('candidate proof consumer substituted')\n"
                path.write_text(path.read_text(encoding="utf-8") + suffix, encoding="utf-8")
            head = commit_fixture_state(self.f.repo, paths=KERNEL_SCOPE, message="fixture protected Kernel upgrade",
                user_name="Kernel upgrade fixture", user_email="kernel@example.invalid")
            data = turn.output
            data["package"]["result"].update(head_sha=head,
                changed_files=[] if self.f.operation == "REPAIR" else ["OUTPUT.txt", *KERNEL_SCOPE])
            turn = replace(turn, output_json=ka01._bytes(data))
            self.last_turn = turn
        elif self.f.operation != "REPAIR":
            data = turn.output
            data["package"]["result"]["changed_files"] = ["OUTPUT.txt", *KERNEL_SCOPE]
            turn = replace(turn, output_json=ka01._bytes(data))
            self.last_turn = turn
        return turn


def kernel_session(f, monkeypatch):
    sessions = []
    def construct(**kwargs):
        session = KernelUpgradeSession(f, **kwargs)
        session.output_schemas = []
        sessions.append(session)
        return session
    monkeypatch.setattr(codex_adapter, "CodexLiveTransport", construct)
    return sessions


@pytest.mark.parametrize("operation,parallel", [("PRIMARY", False), ("PRIMARY", True),
    ("REPAIR", False), ("REMEDIATION", False)])
def test_kernel_upgrade_original_runtime_verifies_protected_candidate_and_returns_standard_terminal(
        tmp_path, monkeypatch, operation, parallel):
    f = KernelUpgradeFixture(tmp_path / "kernel", monkeypatch, operation=operation, parallel=parallel)
    sessions = kernel_session(f, monkeypatch)
    summary = f.execute()
    budget = f.owner._ka01_budget
    records = checkpoints(f)
    assert len(sessions) == 1 and sessions[0].calls == 2 and sessions[0].closed
    assert budget.default and budget.native_calls == 1 and budget.pending is None
    assert summary.ka01["effective_limits"] == ka01.DEFAULTS
    assert summary.ka01["checkpoints_spent"] == 2 and summary.ka01["corrections_spent"] == 1
    assert summary.ka01["review"] == summary.ka01["publication"] == "NOT_OBSERVED"
    authority = json.loads(budget.authority)["kernel_upgrade"]
    assert authority["implementation"] == ka01.KERNEL_UPGRADE_IMPLEMENTATION
    assert authority["source_main_sha"] == f.base and authority["run_id"] == summary.run_id
    assert authority["execution_profile_sha256"] == ka01._digest(budget.profile.as_dict())
    assert set(authority["implementation_source"]) == set(ka01.KERNEL_IMPLEMENTATION_PATHS)
    assert set(authority["proof_source"]) == set(ka01.KERNEL_PROOF_PATHS)
    for record in records:
        assert record.data["version"] == 2
        original = record.data["binding"]["original_proof"]
        assert original["source_main_sha"] == f.base and original["admission_sha256"] == ka01._digest(budget.admission)
        for evidence in record.data["evidence"]:
            binding = evidence["verification"]["binding"]
            assert binding["original_proof"] == original
            assert binding["profile"]["isolation_policy"] == "original-runtime-observer-candidate-imports-v1"
            assert binding["profile"]["repository_tool_blobs"]["src/aios_renew/verification_contract.py"] == git(
                f.repo, "rev-parse", f.base + ":src/aios_renew/verification_contract.py")
        forged = record.data
        forged["binding"]["original_proof"]["source_main_sha"] = "0" * 40
        with pytest.raises(VerificationContractError, match="ORIGINAL_PROOF_MISMATCH"):
            parse_native_operation_checkpoint(ka01._bytes(forged))
        with pytest.raises(VerificationContractError):
            parse_native_primary_checkpoint(record.content_json.encode("ascii"))
    assert records[0].data["failed_identities"] and not records[1].data["failed_identities"]
    assert len(f.verification_calls) == 2 * f.commands_per_checkpoint
    result = json.loads(summary.result_path.read_bytes())
    assert set(result["result"]["changed_files"]) == {"OUTPUT.txt", *KERNEL_SCOPE}
    assert {e["subject_sha"] for e in result["evidence"]} == {summary.head_sha}
    assert git(f.remote, "rev-parse", "refs/heads/aios/review/" + summary.run_id) == summary.head_sha
    assert git(f.remote, "rev-parse", "refs/heads/main") == f.base
    assert not (f.state.failures / (summary.run_id + ".json")).exists()


@pytest.mark.parametrize("defect,code", [
    ("old-norm", "KERNEL_UPGRADE_BOOTSTRAP_REQUIRED"),
    ("wrong-source", "KERNEL_UPGRADE_RUNNING_IMPLEMENTATION_SOURCE_MISMATCH"),
    ("mutable-runtime", "KERNEL_UPGRADE_RUNTIME_SOURCE_IS_MUTABLE"),
    ("missing-observer", "KERNEL_UPGRADE_ORIGINAL_OBSERVER_UNAVAILABLE"),
    ("missing-toolchain", "KERNEL_UPGRADE_TOOLCHAIN_UNAVAILABLE"),
    ("substituted-helper", "KERNEL_UPGRADE_RUNNING_MODULE_SUBSTITUTED"),
    ("missing-original-module", "KERNEL_UPGRADE_ORIGINAL_RUNTIME_UNAVAILABLE"),
    ("substituted-runtime-spec", "KERNEL_UPGRADE_RUNNING_MODULE_SUBSTITUTED"),
])
def test_kernel_upgrade_bootstrap_refuses_before_run_or_executor(tmp_path, monkeypatch, defect, code):
    from aios_renew import runtime as runtime_module
    f = KernelUpgradeFixture(tmp_path / "rejected", monkeypatch)
    sessions = kernel_session(f, monkeypatch)
    if defect == "mutable-runtime":
        monkeypatch.setattr(runtime_module, "__file__", str(f.repo / "src/aios_renew/runtime.py"))
    elif defect == "missing-original-module":
        import sys
        # This is an intentional missing-module counterexample. The cold-entry
        # tests separately measure actual availability without pytest imports.
        monkeypatch.setitem(sys.modules, "aios_renew.proof_coverage_contract", None)
    elif defect == "substituted-runtime-spec":
        import importlib.util
        monkeypatch.setattr(runtime_module, "__spec__", importlib.util.spec_from_file_location(
            runtime_module.__name__, f.repo / "src/aios_renew/runtime.py"))
    elif defect == "substituted-helper":
        from aios_renew import parallel_verification
        substituted = tmp_path / "parallel_verification.py"
        substituted.write_bytes(Path(parallel_verification.__file__).read_bytes())
        monkeypatch.setattr(parallel_verification, "__file__", str(substituted))
    elif defect == "missing-toolchain":
        from aios_renew import verification
        def unavailable():
            raise verification.ProbeError("fixture missing original toolchain")
        monkeypatch.setattr(verification, "_v2_toolchain", unavailable)
    else:
        path = ka01.NORMATIVE_PATH if defect == "old-norm" else "src/aios_renew/runtime.py" if defect == "wrong-source" else "tests/bp_v4_probe_plugin.py"
        if defect == "missing-observer":
            git(f.repo, "rm", path)
        else:
            (f.repo / path).write_text("# unavailable original source\n", encoding="utf-8")
            git(f.repo, "add", path)
        git(f.repo, "commit", "--quiet", "-m", "fixture unavailable bootstrap")
        git(f.repo, "push", "--quiet", "origin", "HEAD:refs/heads/main")
    with pytest.raises(VerificationContractError, match=code):
        f.execute()
    assert not sessions and not f.verification_calls and f.owner is None
    assert not list(f.state.runs.glob("*.json")) and not list(f.state.failures.glob("*.json"))
    assert not list(f.state.results.glob("*.json"))
    refusals = list(f.state.admission_failures.glob("*.json"))
    assert len(refusals) == 1
    refusal = json.loads(refusals[0].read_bytes())
    assert refusal["phase"] == "CANONICAL_CONTRACT_ADMISSION"
    assert refusal["executor_invoked"] is False
    assert code in str(refusal)


@pytest.mark.parametrize("defect", ["wrong-source", "cross-run", "forged-checkpoint", "unauthorized-scope", "observer-substitution"])
def test_kernel_upgrade_rejects_forged_or_unauthorized_proof_before_terminal(tmp_path, monkeypatch, defect):
    from aios_renew import verification, runtime as runtime_module
    f = KernelUpgradeFixture(tmp_path / "forged", monkeypatch, first_fail=defect != "observer-substitution")
    sessions = kernel_session(f, monkeypatch)
    changes = {}
    original = verification._execute_checkpoint_verification
    def corrupt(*args, **kwargs):
        evidence = original(*args, **kwargs)
        first = evidence[0]
        record = json.loads(ka01._bytes(first.verification))
        if defect == "wrong-source":
            record["binding"]["original_proof"]["source_main_sha"] = first.subject_sha
        elif defect == "cross-run":
            record["binding"]["original_proof"]["admission_sha256"] = "0" * 64
        else:
            record["candidate"]["complete"] = True
            record["candidate"]["reports"] = []
        return (replace(first, verification=record), *evidence[1:])
    if defect == "unauthorized-scope":
        def widen(session, ordinal, prompt):
            (f.repo / "UNAUTHORIZED.txt").write_text("forged permission", encoding="utf-8")
            commit_fixture_state(f.repo, paths=("UNAUTHORIZED.txt",), message="fixture unauthorized mutation",
                user_name="Kernel fixture", user_email="kernel@example.invalid")
        # Attach after creation, before the first candidate.
        construct = codex_adapter.CodexLiveTransport
        def hooked(**kwargs):
            session = construct(**kwargs)
            session.hook = widen
            return session
        monkeypatch.setattr(codex_adapter, "CodexLiveTransport", hooked)
    elif defect == "observer-substitution":
        original_verify = f.verify
        def substituted(command, **kwargs):
            # Tampering outside the intact candidate tree must reject even
            # an otherwise passing subprocess observation.
            path = Path(kwargs["cwd"]).parent / "original-proof" / "tests/bp_v4_probe_plugin.py"
            path.write_bytes(path.read_bytes() + b"\n# Substituted original observer.\n")
            return original_verify(command, **kwargs)
        changes = dict(verification_runner=substituted)
    else:
        monkeypatch.setattr(runtime_module, "_execute_checkpoint_verification", corrupt)
    with pytest.raises((VerificationContractError, operator.OperatorError)):
        f.execute(**changes)
    assert len(sessions) == 1 and sessions[0].calls == 1 and sessions[0].closed
    assert not list(f.state.results.glob("RUN-101-001.json"))
    assert not f.owner._interactive_feedback


def test_kernel_upgrade_retains_exact_task426_filename_guard_without_original_admission(tmp_path, monkeypatch):
    from types import SimpleNamespace
    actual = yaml.safe_load((Path(__file__).resolve().parents[1] / ".ai/tasks/TASK-426.yaml").read_text(encoding="utf-8"))
    assert set(KERNEL_SCOPE) <= set(actual["scope"]["modify"])
    f = ThreeFlowFixture(tmp_path / "original-guard", monkeypatch)
    owner = RuntimeCompletion(repo=f.repo, state=f.state, task=f.task, run=f.run,
        run_path=f.state.runs / (f.run.run_id + ".json"), verification_runner=f.verify,
        observation_tracker=None, error_type=operator.OperatorError)
    owner._ka01_budget = SimpleNamespace(default=True, operation="PRIMARY",
        policy=replace(f.policy, result_scope=tuple(actual["scope"]["modify"])))
    with pytest.raises(VerificationContractError, match="NATIVE_BOUND_PROOF_CONSUMER_MUTATION_UNSUPPORTED"):
        owner._preflight_interactive_policy()
    assert not f.native_calls and not f.verification_calls and not list(f.state.runs.glob("*.json"))


def test_ordinary_task_cannot_mutate_protected_consumers_without_upgrade_scope(tmp_path, monkeypatch):
    f = ThreeFlowFixture(tmp_path / "unadmitted-protected", monkeypatch)
    def mutate(session, ordinal, prompt):
        name = "src/aios_renew/runtime.py"
        path = f.repo / name
        path.write_text(path.read_text(encoding="utf-8") + "\n# Unauthorized protected mutation.\n", encoding="utf-8")
        commit_fixture_state(f.repo, paths=(name,), message="fixture unauthorized protected mutation",
            user_name="Kernel fixture", user_email="kernel@example.invalid")
    sessions = synthetic_session(f, monkeypatch, hook=mutate)
    with pytest.raises((VerificationContractError, operator.OperatorError)):
        f.execute()
    assert len(sessions) == 1 and sessions[0].calls == 1 and sessions[0].closed
    assert not f.verification_calls and not list(f.state.results.glob("*.json"))


@pytest.mark.parametrize("admissible", [False, True])
def test_kernel_upgrade_exact_authoring_identity_and_existing_self_host_wake(tmp_path, monkeypatch, capsys, admissible):
    f = KernelUpgradeFixture(tmp_path / "wake", monkeypatch, first_fail=False)
    sessions = kernel_session(f, monkeypatch)
    blob = git(f.repo, "rev-parse", f.base + ":.ai/tasks/" + f.task.task_id + ".yaml")
    if not admissible:
        from aios_renew import runtime as runtime_module
        monkeypatch.setattr(runtime_module, "__file__", str(f.repo / "src/aios_renew/runtime.py"))
    # The normal self-host consumer, with exact original authoring identity and
    # Human-selected profile. No new selector, issuer or bootstrap bearer fields.
    exit_code = operator.main(["wakeup", "task427-kernel-fixture", f.task.task_id,
        "--task-revision", str(f.task.revision), "--task-blob-sha", blob, "--task-commit-sha", f.base,
        "--executor", "codex", "--model", f.profile.model, "--reasoning-effort", f.profile.reasoning_effort,
        "--repo", str(f.control)], native_runner=f.native, verification_runner=f.verify)
    text = capsys.readouterr()
    if not admissible:
        assert exit_code != 0 and "KERNEL_UPGRADE_RUNTIME_SOURCE_IS_MUTABLE" in (text.out + text.err)
        assert not sessions and not list(f.state.runs.glob("*.json")) and not list(f.state.failures.glob("*.json"))
        dispatches = list((f.state.root / "dispatches").glob("*.json"))
        assert len(dispatches) == 1
        dispatch = json.loads(dispatches[0].read_bytes())
        assert dispatch["status"] == "FAILED" and dispatch["run_id"] is None
    else:
        assert exit_code == 0, text.out + text.err
        assert len(sessions) == 1 and sessions[0].calls == 1
        assert len(list(f.state.results.glob("*.json"))) == 1
        assert json.loads(f.owner._ka01_budget.authority)["kernel_upgrade"]["source_main_sha"] == f.base


@pytest.mark.parametrize("operation", ["PRIMARY", "REPAIR", "REMEDIATION"])
def test_default_exhausts_two_actual_improving_corrections_and_three_checkpoints(tmp_path, monkeypatch, operation):
    proof = ("from pathlib import Path\n"
        "def test_a():\n    assert Path('OUTPUT.txt').read_text().startswith('a')\n"
        "def test_b():\n    assert 'b' in Path('OUTPUT.txt').read_text()\n"
        "def test_c():\n    assert Path('OUTPUT.txt').read_text().endswith('c')\n")
    # Seed REMEDIATION with the ordinary already-reviewed proof first, then use
    # the same three independent call failures in all new-run checkpoints.
    if operation == "REMEDIATION":
        proof = proof.replace(".startswith('a')", ".startswith(('a', 'reviewed'))")
        proof = proof.replace("assert 'b' in Path('OUTPUT.txt').read_text()", "assert any(c in Path('OUTPUT.txt').read_text() for c in ('b', 'r'))")
        proof = proof.replace(".endswith('c')", ".endswith(('c', 'reviewed\\n'))")
    f = ThreeFlowFixture(tmp_path / "actual-counts", monkeypatch, operation=operation, proof_source=proof)
    f.candidates = ["x", "a", "ab"]
    sessions = synthetic_session(f, monkeypatch)
    with pytest.raises(VerificationContractError, match="CORRECTION_CAPACITY_EXHAUSTED"):
        f.execute()
    budget = f.owner._ka01_budget
    assert sessions[0].calls == 3 and budget.native_calls == 1
    assert budget.checkpoints_spent == 3 and budget.corrections_spent == 2
    assert [len(r.data["failed_identities"]) for r in checkpoints(f)] == [3, 2, 1]
    assert not (f.state.results / "RUN-101-001.json").exists()


def nonmonotone_proof(kind):
    prefix = "from pathlib import Path\nvalue = Path('OUTPUT.txt').read_text()\n"
    if kind == "cause":
        return prefix + ("def test_output():\n"
            "    if value == 'phase1':\n        raise ValueError('first causal stage')\n"
            "    if value == 'phase2':\n        raise TypeError('next causal stage')\n")
    if kind == "outcomes":
        return prefix + ("def test_first():\n    assert value != 'phase1'\n"
            "def test_next():\n    assert value != 'phase2'\n")
    return prefix + ("import pytest\n"
        "@pytest.mark.parametrize('stage', ['first'] if value == 'phase1' else ['first', 'next', 'last'])\n"
        "def test_output(stage):\n"
        "    assert value != 'phase1'\n"
        "    if value == 'phase2':\n        assert stage == 'first'\n")


@pytest.mark.parametrize("operation", ["PRIMARY", "REPAIR", "REMEDIATION"])
@pytest.mark.parametrize("kind", ["outcomes", "cause", "collection"])
def test_default_nonmonotone_authenticated_progress_keeps_all_proof_and_one_terminal(
        tmp_path, monkeypatch, operation, kind):
    f = ThreeFlowFixture(tmp_path / "nonmonotone", monkeypatch, operation=operation,
        proof_source=nonmonotone_proof(kind))
    f.candidates = ["phase1", "phase2", "complete\n"]
    sessions = synthetic_session(f, monkeypatch)
    summary = f.execute()
    records = checkpoints(f)
    assert [len(r.data["failed_identities"]) for r in records] == [1, 2 if kind == "collection" else 1, 0]
    first, second, final = [r.data for r in records]
    assert not set(second["failed_identities"]) < set(first["failed_identities"])
    assert (first["population_identity"] != second["population_identity"]) == (kind == "collection")
    if kind == "cause":
        assert first["failed_identities"] == second["failed_identities"]
        def failure_fingerprints(data):
            return [r["fingerprint"] for e in data["evidence"]
                for r in e["verification"]["candidate"]["reports"] if r["outcome"] == "FAIL"]
        assert failure_fingerprints(first) != failure_fingerprints(second)
    assert sessions[0].calls == 3 and len(sessions) == 1 and sessions[0].closed
    assert len(f.verification_calls) == 3 * f.commands_per_checkpoint
    assert summary.ka01["checkpoints_spent"] == 3 and summary.ka01["corrections_spent"] == 2
    assert summary.ka01["effective_limits"] == ka01.DEFAULTS
    assert summary.ka01["review"] == summary.ka01["publication"] == "NOT_OBSERVED"
    assert json.loads(f.owner._ka01_budget.authority)["progress_policy"] == ka01.DEFAULT_PROGRESS_POLICY
    assert len(f.owner._interactive_feedback) == len(f.owner._interactive_receipts) == 2
    assert records[1].data["predecessor_digest"] == records[0].digest
    assert records[2].data["predecessor_digest"] == records[1].digest
    package = json.loads(summary.result_path.read_bytes())
    assert package["evidence"] == final["evidence"]
    assert {e["subject_sha"] for e in package["evidence"]} == {summary.head_sha}
    assert not (f.state.failures / (summary.run_id + ".json")).exists()
    assert len(list(f.state.results.glob("*.json"))) == (2 if operation == "REMEDIATION" else 1)
    assert git(f.remote, "rev-parse", "refs/heads/main") == f.base


@pytest.mark.parametrize("defect", ["incomplete", "unstable", "unknown-exit", "error", "stale-source"])
def test_default_nonmonotone_correction_cannot_use_ineligible_proof(tmp_path, monkeypatch, defect):
    f = ThreeFlowFixture(tmp_path / "ineligible-progress", monkeypatch, proof_source=nonmonotone_proof("cause"))
    f.candidates = ["phase1", "phase2", "complete\n"]
    sessions = synthetic_session(f, monkeypatch)
    def corrupt(command, **kwargs):
        completed = f.verify(command, **kwargs)
        if len(f.verification_calls) != 3:
            return completed
        if defect == "unknown-exit":
            return subprocess.CompletedProcess(completed.args, 2, completed.stdout, completed.stderr)
        path = Path(kwargs["env"]["AIOS_BP_V4_PLUGIN_OUTPUT"])
        data = json.loads(path.read_bytes())
        canonical = data["failure_diagnostics"]["canonical"]
        if defect == "incomplete":
            canonical["complete"] = False
        elif defect == "unstable":
            canonical["unstable"] = True
        elif defect == "error":
            canonical["errors"].append("observer unavailable")
        else:
            data["subject_sha"] = f.base
        path.write_bytes(ka01._bytes(data))
        return completed
    with pytest.raises((RuntimeError, ValueError, OSError)):
        f.execute(verification_runner=corrupt)
    assert sessions[0].calls == 2 and sessions[0].closed and f.owner._ka01_budget.stopped
    assert len(checkpoints(f)) == 1 and set(f.owner._interactive_feedback) == {1}
    assert not (f.state.results / "RUN-101-001.json").exists()


def test_default_new_source_with_unchanged_proof_facts_stops(tmp_path, monkeypatch):
    proof = ("from pathlib import Path\n"
        "def test_output():\n"
        "    if Path('OUTPUT.txt').read_text():\n        raise AssertionError('unchanged obligation')\n")
    f = ThreeFlowFixture(tmp_path / "unchanged-proof", monkeypatch, proof_source=proof)
    f.candidates = ["different source one", "different source two", "complete\n"]
    sessions = synthetic_session(f, monkeypatch)
    with pytest.raises(VerificationContractError, match="NO_VERIFIED_SCOPED_PROGRESS"):
        f.execute()
    assert sessions[0].calls == 2 and len(checkpoints(f)) == 1
    assert len(f.verification_calls) == 4 and len(f.owner._interactive_feedback) == 1
    assert not (f.state.results / "RUN-101-001.json").exists()


def test_default_collection_change_cannot_hide_previously_observed_failure(tmp_path, monkeypatch):
    proof = ("from pathlib import Path\n"
        "if Path('OUTPUT.txt').read_text() == 'phase1':\n"
        "    def test_required():\n        raise AssertionError('must stay observed')\n"
        "def test_guard():\n    assert True\n")
    f = ThreeFlowFixture(tmp_path / "hidden-proof", monkeypatch, proof_source=proof)
    f.candidates = ["phase1", "complete\n"]
    sessions = synthetic_session(f, monkeypatch)
    with pytest.raises(VerificationContractError, match="NO_VERIFIED_SCOPED_PROGRESS"):
        f.execute()
    assert sessions[0].calls == 2 and len(checkpoints(f)) == 1
    assert not (f.state.results / "RUN-101-001.json").exists()


def test_default_unrelated_new_failure_does_not_establish_progress(tmp_path, monkeypatch):
    proof = ("from pathlib import Path\nvalue = Path('OUTPUT.txt').read_text()\n"
        "def test_required():\n    raise AssertionError('original failure is unchanged')\n"
        "def test_unrelated():\n    assert value != 'phase2'\n")
    f = ThreeFlowFixture(tmp_path / "unrelated-progress", monkeypatch, proof_source=proof)
    f.candidates = ["phase1", "phase2", "complete\n"]
    sessions = synthetic_session(f, monkeypatch)
    with pytest.raises(VerificationContractError, match="NO_VERIFIED_SCOPED_PROGRESS"):
        f.execute()
    assert sessions[0].calls == 2 and len(checkpoints(f)) == 1
    assert len(f.verification_calls) == 4 and len(f.owner._interactive_feedback) == 1
    assert not (f.state.results / "RUN-101-001.json").exists()
