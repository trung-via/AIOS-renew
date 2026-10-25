"""Prospective ordinary-entrypoint coverage; Runtime owns executing this file.

Synthetic sessions below test deterministic counterexamples. The mandatory
installed-native cases retain the actual process, model, observer and verifier.
Canonical source/failure/Review/authorization refs use real isolated Git repos.
"""

from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess

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
                 historical=False, policy="minimum-sufficient-v1", reusable=False, proof_source=PROOF, omit_files=()):
        super().__init__(root, monkeypatch, interactive=True, default=True, first_fail=first_fail,
            policy=policy, proof_source=proof_source, task_transform=with_original_guard,
            omit_files=omit_files,
            extra_files={"tests/test_unrelated.py": "def test_unrelated():\n    assert True\n",
                # Historical ordinary verification may write Python/pytest
                # caches, just as in the real repository's ignored layout.
                ".gitignore": "__pycache__/\n.pytest_cache/\n"})
        self.root, self.control = root, self.repo
        self.operation, self.action = operation, action
        self.reusable = reusable
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
        review = dict(review_id="REVIEW-101-001", reviewed_sha=self.subject, mode="PRIMARY",
            verdict="CHANGES_REQUIRED", acceptance={"AC1": "FAIL"}, findings=[dict(id="R1", basis="AC1",
                action="CODE_FIX", location="OUTPUT.txt", issue="Output needs the approved complete value.",
                expected="Commit OUTPUT.txt containing complete followed by a newline.")])
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
    f = ThreeFlowFixture(tmp_path / "counterexample", monkeypatch, operation=operation)
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
