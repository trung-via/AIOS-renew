"""L3-05 definitions: real Git/native boundaries and independent Human HTTP.

Only the existing GitHub HTTP reader is replayed. No admission/source/proof,
lease, callback or budget validator is replaced by an affirmative answer.
These fixtures grant no production consent. Runtime owns running the tests.
"""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import pytest
import yaml

from aios_renew import github_issue_ingress as carrier
from aios_renew import ka01_admission as ka01
from aios_renew import operator
from aios_renew import runtime as completion
from aios_renew.dispatcher import Dispatcher, DispatcherError
from aios_renew.execution_profile import ResolvedExecutionProfile
from aios_renew.intra_run_proof_contract import FactualFeedbackRequest, FeedbackConsumptionRequest
from aios_renew.run import Run, RunLeaseRegistry
from aios_renew.run_observation import RunObservationTracker
from aios_renew.runtime import RuntimeCompletion, persist_failure, repair_completion_policy, remediation_completion_policy
from aios_renew.review import parse_review, parse_remediation, RemediationExecution
from aios_renew.review_transport import public_run, public_failure, ReviewTransportError
from aios_renew.verification_contract import VerificationContractError
from tests.test_lean_vp_l3_factual_feedback import FeedbackFixture
from tests.test_lean_vp_l3_same_executor_continuation import LocalAdapter
from tests.git_fixture_support import _copy_loose_objects, _tree_files


class BudgetFixture(FeedbackFixture):
    def worktree_commit(self, files, message):
        if message == "source dependency closure":
            files = {**files, ka01.NORMATIVE_PATH: (
                Path(__file__).resolve().parents[1] / ka01.NORMATIVE_PATH).read_bytes()}
        return super().worktree_commit(files, message)

    def __init__(self, root, monkeypatch, *, mode="fail", multi=False):
        super().__init__(root, monkeypatch, mode=mode, multi=multi)
        tracker = RunObservationTracker("PRIMARY")
        tracker.admit(self.run)
        self.owner = RuntimeCompletion(repo=self.repo, state=self.state, task=self.task, run=self.run,
            run_path=self.owner.run_path, verification_runner=self.actual_runner,
            observation_tracker=tracker, error_type=RuntimeError)
        self.profile = ResolvedExecutionProfile(self.run.run_id, self.run.executor,
            "explicit-offline-model", "high", "EXPLICIT", "EXPLICIT")
        self.leases = RunLeaseRegistry()
        self.lease = self.leases.acquire(self.run)
        self.issues = {}
        monkeypatch.setattr(carrier, "_github_selection_json", self.budget_http)

    def budget_http(self, repository, suffix):
        if suffix.startswith(("/issues/", "/selection-edit-history/")):
            number = int(suffix.rsplit("/", 1)[1])
            if number in self.issues:
                issue = self.issues[number]
                if suffix.startswith("/issues/"):
                    return copy.deepcopy(issue)
                return dict(data=dict(repository=dict(nameWithOwner=repository, databaseId=101,
                    issue=dict(id=issue["node_id"], number=number, body=issue["body"], title=issue["title"],
                        createdAt=issue["created_at"], lastEditedAt=issue.get("edited_at")))))
            if number != 201:
                raise carrier.GitHubIssueIngressError("controlled Issue does not exist")
        return self.http(repository, suffix)

    def issue(self, number, body):
        raw = ka01._bytes(body)
        self.issues[number] = dict(number=number, id=10000 + number, node_id=f"I_fixture_{number}",
            title="[AIOS BRAIN INGRESS]", state="open", body=raw.decode("ascii"),
            repository_url="https://api.github.com/repos/fixture-owner/fixture-repository",
            user=dict(login="fixture-human", id=401, type="User"), created_at="2026-01-01T00:00:00Z")
        return dict(issue=number, sha256=ka01._digest(raw))

    def authorize(self, overrides=(), *, selection_change=None, consent_change=None,
                  override_change=None, operation="PRIMARY", policy=None, source_operation=None):
        if (source_operation or operation) == "PRIMARY":
            # The verifier binds the actual environment, including pytest's
            # phase. Produce the reviewed manifest in the same call phase as
            # native execution, rather than freezing a fixture-setup digest.
            self.native_request = self.checkpoint_request()
        self.selected_policy = policy or self.policy
        pins = []
        for index, values in enumerate(overrides):
            body = dict(format="AIOS_KA01_V13_OVERRIDE", version=1, ka01_version="1.3",
                task=asdict(self.run.task), operations=[operation], values=values)
            if override_change is not None:
                override_change(body)
            pins.append(self.issue(1000 + index, body))
        selection = dict(format="AIOS_KA01_V13_SELECTION", version=1,
            implementation=ka01.IMPLEMENTATION, ka01_version="1.3", profile=ka01.PROFILE,
            policy=self.task.verification.policy, task=asdict(self.run.task), operations=[operation],
            normative=dict(source_sha=self.run.base_sha, path=ka01.NORMATIVE_PATH, sha256=ka01.NORMATIVE_SHA256),
            overrides=pins, remote_url=self.url)
        if selection_change is not None:
            selection_change(selection)
        selection_pin = self.issue(1100, selection)
        # Construct a request, never an affirmative authority witness. Invalid
        # overrides must fail before this deliberately unhelpful consent digest.
        try:
            effective = ka01.resolve_limits(list(overrides))
        except VerificationContractError:
            effective = dict(ka01.DEFAULTS)
        source = operator._ka01_operation_source(self.owner, source_operation or operation,
            self.policy if source_operation else self.selected_policy)
        consent = dict(format="AIOS_KA01_V13_RUN_CONSENT", version=1, mode="CONTROLLED_PREACTIVATION",
            implementation=ka01.IMPLEMENTATION, ka01_version="1.3", selection_sha256=selection_pin["sha256"],
            task=asdict(self.run.task), run_id=self.run.run_id, admitted_run_sha256=source["admitted_run_sha256"],
            operation=operation, executor=self.run.executor,
            execution_profile_sha256=ka01._digest(self.profile.as_dict()),
            operation_source_sha256=ka01._digest(source), effective_limits_sha256=ka01._digest(effective))
        if consent_change is not None:
            consent_change(consent)
        consent_pin = self.issue(1101, consent)
        self.admission_request = ka01.AdmissionRequest(1, 1100, selection_pin["sha256"], 1101, consent_pin["sha256"])
        self.consent_ref = "refs/heads/aios/ka01-v13-consent/" + self.run.run_id
        self.consent_carrier = self.commit_files({".ai/transport/ka01-v13.json": ka01._bytes(asdict(self.admission_request))})
        _copy_loose_objects(self.objects, self.remote / "objects")
        self.set_ref(self.consent_ref, self.consent_carrier)
        return self.admission_request

    def admit(self, **changes):
        arguments = dict(runtime=self.owner, request=self.admission_request, operation="PRIMARY",
            execution_profile=self.profile, policy=self.selected_policy, leases=self.leases, lease=self.lease)
        arguments.update(changes)
        return operator.preactivate_ka01_v13(**arguments)

    def invoke(self, body):
        adapter = LocalAdapter(self, body, profile=self.profile)
        adapter.leases, adapter.lease = self.leases, self.lease
        adapter.dispatcher = Dispatcher(selected_executor=self.run.executor, operation="PRIMARY",
            adapter_factories={self.run.executor: lambda: adapter}, execution_profile=self.profile,
            prospective_runtime=self.owner)
        adapter.dispatcher.dispatch_primary(task=self.task, run=self.run, lease=self.lease, leases=self.leases)
        return adapter

    def prepare_native(self):
        checkpoint = self.owner.seal_checkpoint(self.native_request)
        source = FactualFeedbackRequest(1, self.native_request, checkpoint.digest)
        feedback = self.owner.prepare_feedback(source)
        return checkpoint, FeedbackConsumptionRequest(1, source, feedback.digest)

    def original_correction_run(self, operation):
        """Build controlled original admission using real first-party sources.

        This constructs no positive native correction callback. Both paths must
        authenticate independently and then report missing continuity as BLOCKED.
        """
        if operation == "REPAIR":
            predecessor, previous_run = self.run.run_id, self.run
            persist_failure(self.repo, state=self.state, task=self.task, run=self.run,
                run_path=self.owner.run_path, failure=RuntimeError("bounded fixture failure"), transport=False)
            failure_raw = (self.state.failures / (predecessor + ".json")).read_bytes()
            artifact = self.commit_files({".ai/transport/run.json": public_run(ka01._bytes(asdict(previous_run))),
                ".ai/transport/failure.json": public_failure(failure_raw)})
            # The inherited native fixture used a run-only admission artifact.
            # Replace that synthetic preterminal pointer with its actual FAILURE
            # source; never leave conflicting RESULT/FAILURE artifact families.
            (self.remote / ("refs/heads/aios/artifacts/" + predecessor)).unlink()
            (self.remote / ("refs/heads/aios/review/" + predecessor)).unlink()
            self.set_ref("refs/heads/aios/failure/" + predecessor, self.target.sha)
            self.set_ref("refs/heads/aios/failure-artifacts/" + predecessor, artifact)
            repair = dict(repair_id="REPAIR-005-003", failed_run_id=predecessor,
                failed_head_sha=self.target.sha, task=asdict(self.run.task), action="CODE_FIX",
                modification_scope=["fixture-target.txt"], instructions=["Correct the original failed obligation."],
                constraints=list(self.task.constraints.hard))
            authorization = self.commit_files({".ai/transport/repair.json": ka01._bytes(repair)})
            self.set_ref("refs/heads/aios/repair/" + predecessor, authorization)
            _copy_loose_objects(self.objects, self.remote / "objects")
            resolved = operator._resolve_historical_repair_admission(self.repo, predecessor, admission={})
            self._new_original_run(self.target.sha, operation)
            lineage = self.state.repairs / (self.run.run_id + ".json")
            lineage.parent.mkdir(parents=True, exist_ok=True)
            execution = dict(failed_run_id=predecessor, root_base_sha=resolved.root_base_sha,
                result_base_sha=resolved.result_base_sha, failed_head_sha=self.target.sha, failure=resolved.failure,
                task=operator._executor_task_data(self.task), repair=repair, run=asdict(self.run),
                repair_authorization_sha=authorization)
            lineage.write_bytes(ka01._bytes(execution))
            self.policy = repair_completion_policy(self.task, root_base_sha=resolved.root_base_sha,
                result_base_sha=resolved.result_base_sha, failed_head_sha=self.target.sha, action="CODE_FIX",
                modification_scope=repair["modification_scope"], lineage_path=lineage,
                origin_affected_verification=resolved.origin_affected_verification)
        else:
            predecessor = self.source.run_id
            finding = dict(id="F1", basis="AC1", action="CODE_FIX", location="fixture-target.txt",
                issue="The original selected finding remains outstanding.", expected="Correct only this finding.")
            review_data = dict(review_id="REVIEW-005-CORRECTION", reviewed_sha=self.source.sha, mode="PRIMARY",
                verdict="CHANGES_REQUIRED", acceptance=dict(AC1="FAIL"), findings=[finding])
            remediation_data = dict(finding_id="F1", action="CODE_FIX", reviewed_sha=self.source.sha,
                modification_scope=["fixture-target.txt"], verification=dict(policy=self.task.verification.policy,
                    affected=list(self.task.verification.required)),
                constraints=dict(hard=list(self.task.constraints.hard)))
            files = _tree_files(self.repo, self.source.tree)
            files.update({".ai/reviews/REVIEW-005-CORRECTION.yaml": yaml.safe_dump(review_data).encode(),
                ".ai/remediations/REMEDIATION-005-F1.yaml": yaml.safe_dump(remediation_data).encode()})
            authorization = self.commit_files(files, parents=(self.source.sha,))
            self.set_ref("refs/heads/aios/remediation/" + predecessor + "-F1", authorization)
            _copy_loose_objects(self.objects, self.remote / "objects")
            self._new_original_run(self.source.sha, operation)
            review = parse_review(yaml.safe_dump(review_data))
            remediation = parse_remediation(yaml.safe_dump(remediation_data))
            execution = RemediationExecution(review.review_id, review.findings[0], remediation,
                self.run, self.task.constraints.hard)
            self.owner.run_path.write_bytes(ka01._bytes(dict(kind="REMEDIATION", execution=asdict(execution),
                remediation_authorization_sha=authorization,
                predecessor=dict(source_run_id=predecessor, review_id=review.review_id, finding_id="F1",
                                 reviewed_sha=self.source.sha))))
            self.policy = remediation_completion_policy(execution)

    def _new_original_run(self, base, operation):
        self.run = Run.from_task(run_id="RUN-005-003", task=self.task, executor=self.run.executor,
                                 base_sha=base, workspace=str(self.repo))
        path = self.owner.run_path.parent / (self.run.run_id + ".json")
        path.write_bytes(ka01._bytes(asdict(self.run)))
        tracker = RunObservationTracker(operation)
        tracker.admit(self.run)
        self.owner = RuntimeCompletion(repo=self.repo, state=self.state, task=self.task, run=self.run,
            run_path=path, verification_runner=self.actual_runner, observation_tracker=tracker, error_type=RuntimeError)
        self.directory = self.state.verification / self.run.run_id / "l3-02"
        self.profile = ResolvedExecutionProfile(self.run.run_id, self.run.executor,
            "explicit-new-correction-model", "high", "EXPLICIT", "EXPLICIT")
        self.leases = RunLeaseRegistry()
        self.lease = self.leases.acquire(self.run)


@pytest.fixture
def budgeted(tmp_path, monkeypatch):
    return BudgetFixture(tmp_path / "admission-budget", monkeypatch)


def test_exact_defaults_frozen_on_original_run_and_real_native_replay(budgeted):
    f = budgeted
    before = asdict(f.task), asdict(f.run)
    f.authorize()
    admitted = f.admit()
    assert admitted["authority"]["effective_limits"] == dict(max_corrections=2, max_checkpoints=3,
        max_run_wall_minutes=240, max_runtime_verification_minutes=180, max_tokens=None)
    assert admitted["production_activation"] == "NOT_ACTIVATED"
    assert admitted["continuation"] == "NOT_AUTHORIZED"
    assert admitted["authority"]["consent"]["provenance"]["actor"] == "fixture-human"
    original_admission = (f.directory.parent / "ka01-v13/admission.json").read_bytes()

    def body(adapter):
        checkpoint, request = f.prepare_native()
        first = adapter.consume(request)
        assert first.data["status"] == "LOCAL_ACKNOWLEDGED"
        assert first.data["authority"]["continuation"] == "NOT_AUTHORIZED"
        budget = f.owner._ka01_budget
        counters = budget.checkpoints_reserved, budget.checkpoints_spent, budget.corrections_reserved, budget.corrections_spent
        assert counters == (1, 1, 1, 1)
        spent = budget.verification_spent_ns
        assert f.owner.seal_checkpoint(f.native_request) == checkpoint
        assert adapter.consume(request) == first
        assert counters == (budget.checkpoints_reserved, budget.checkpoints_spent, budget.corrections_reserved, budget.corrections_spent)
        assert budget.verification_spent_ns > spent
        assert budget.verification_reserved_ns == budget.verification_settled_ns
    adapter = f.invoke(body)
    assert len(f.calls) == len(adapter.observed) == 1
    assert (f.directory.parent / "ka01-v13/admission.json").read_bytes() == original_admission
    assert (asdict(f.task), asdict(f.run)) == before
    admitted["authority"]["effective_limits"]["max_corrections"] = 100
    assert f.owner._ka01_budget.limits["max_corrections"] == 2


@pytest.mark.parametrize("field,value", [
    ("max_corrections", 0), ("max_corrections", 4), ("max_checkpoints", 1), ("max_checkpoints", 7),
    ("max_run_wall_minutes", 300), ("max_runtime_verification_minutes", 90), ("max_tokens", None)])
def test_each_independently_authenticated_override_only_replaces_its_field(budgeted, field, value):
    f = budgeted
    f.authorize([{field: value}])
    admitted = f.admit()
    assert admitted["authority"]["effective_limits"] == {**ka01.DEFAULTS, field: value}
    assert len(admitted["authority"]["overrides"]) == 1
    assert admitted["authority"]["overrides"][0]["provenance"]["issue_number"] == 1000


@pytest.mark.parametrize("values", [
    {"max_corrections": True}, {"max_corrections": -1}, {"max_corrections": 1.5},
    {"max_checkpoints": 0}, {"max_checkpoints": False}, {"max_run_wall_minutes": 0},
    {"max_run_wall_minutes": 120}, {"max_runtime_verification_minutes": 241},
    {"max_runtime_verification_minutes": "60"}, {"max_tokens": 0}, {"max_tokens": True},
    {"max_tokens": 1.5}, {"unknown_budget": 1}, {}])
def test_malformed_merged_limits_do_not_fall_back_to_defaults(budgeted, values):
    f = budgeted
    f.authorize([values])
    with pytest.raises(VerificationContractError):
        f.admit()
    assert f.calls == []
    assert (f.directory.parent / ".runtime-mutation/intent.json").is_file()
    with pytest.raises(VerificationContractError):
        f.owner.seal_checkpoint(f.native_request)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), 0.00000000000000001])
def test_nonfinite_and_lossy_time_values_are_rejected(value):
    with pytest.raises(VerificationContractError):
        ka01.resolve_limits([{"max_runtime_verification_minutes": value}])


def test_large_finite_integer_time_override_is_converted_without_decimal_rounding():
    minutes = 10**100 + 1
    assert ka01._minutes_ns(minutes) == minutes * 60_000_000_000


@pytest.mark.parametrize("defect", ["overlap", "revision", "boolean-revision", "operation", "empty-operation", "nonexistent", "forged", "edited", "withdrawn", "changed-body"])
def test_overrides_require_exact_nonoverlapping_current_human_sources(budgeted, defect):
    f = budgeted
    change = None
    if defect in {"revision", "boolean-revision"}:
        change = lambda body: body["task"].update(revision=2 if defect == "revision" else True)
    elif defect in {"operation", "empty-operation"}:
        change = lambda body: body.update(operations=["REPAIR"] if defect == "operation" else [])
    f.authorize([{"max_corrections": 1}] * (2 if defect == "overlap" else 1), override_change=change)
    issue = f.issues[1000]
    if defect == "nonexistent":
        del f.issues[1000]
    elif defect == "forged":
        issue["user"]["login"] = "untrusted-caller"
    elif defect == "edited":
        issue["edited_at"] = "2026-01-02T00:00:00Z"
    elif defect == "withdrawn":
        issue["state"] = "closed"
    elif defect == "changed-body":
        issue["body"] = ka01._bytes({"forged": True}).decode()
    with pytest.raises((VerificationContractError, carrier.GitHubIssueIngressError)):
        f.admit()
    assert f.calls == []


@pytest.mark.parametrize("change", [
    lambda b: b.update(run_id="RUN-005-999"), lambda b: b.update(operation="REPAIR"),
    lambda b: b["task"].update(revision=2), lambda b: b.update(mode="PRODUCTION"),
    lambda b: b.update(executor="antigravity"), lambda b: b.update(execution_profile_sha256="0" * 64),
    lambda b: b.update(operation_source_sha256="0" * 64), lambda b: b.pop("effective_limits_sha256")])
def test_consent_is_separate_from_task_preferences_and_exact_to_one_run(budgeted, change):
    f = budgeted
    f.authorize([{"max_checkpoints": 4}], consent_change=change)
    with pytest.raises(VerificationContractError, match="CONSENT"):
        f.admit()
    assert f.calls == []


def test_nonnull_token_cap_stops_on_missing_complete_first_party_usage(budgeted):
    f = budgeted
    f.authorize([{"max_tokens": 100000}])
    with pytest.raises(VerificationContractError, match="CUMULATIVE_TOKENS_NOT_ESTABLISHED"):
        f.admit()
    assert f.calls == [] and f.owner._ka01_opted


def test_unambiguous_packed_first_party_selector_remains_eligible(budgeted):
    f = budgeted
    f.authorize()
    f.git("--git-dir=" + str(f.remote), "pack-refs", "--all")
    assert not (f.remote / f.consent_ref).exists()
    admitted = f.admit()
    assert admitted["status"] == "ADMITTED_PREACTIVATION"
    assert admitted["authority"]["original_selector"] == dict(ref=f.consent_ref, sha=f.consent_carrier)
    assert f.calls == []


@pytest.mark.parametrize("defect", ["missing-selector", "competing-selector", "packed-loose-conflict", "missing-consent", "withdrawn-consent"])
def test_missing_or_competing_first_party_selection_and_consent_reject(budgeted, defect):
    f = budgeted
    f.authorize()
    if defect == "missing-selector":
        (f.remote / f.consent_ref).unlink()
    elif defect == "competing-selector":
        # Two valid, genuinely advertised selectors cannot select one consent.
        (f.remote / f.consent_ref).unlink()
        f.set_ref(f.consent_ref + "/original", f.consent_carrier)
        f.set_ref(f.consent_ref + "/competing", f.consent_carrier)
        from aios_renew.verification import _app_refs
        refs = dict(_app_refs(f.repo, f.url, (f.consent_ref, f.consent_ref + "/*")))
        assert set(refs) == {f.consent_ref + "/original", f.consent_ref + "/competing"}
    elif defect == "packed-loose-conflict":
        # This inconsistent ref store is distinct from advertised competition:
        # Git hides its packed child behind the loose parent. Reject it too.
        (f.remote / "packed-refs").write_text(
            "# pack-refs with: peeled fully-peeled sorted\n" + f.consent_carrier + " " + f.consent_ref + "/competing\n")
    elif defect == "missing-consent":
        del f.issues[1101]
    else:
        f.issues[1101]["state"] = "closed"
    with pytest.raises((VerificationContractError, carrier.GitHubIssueIngressError)):
        f.admit()
    assert f.calls == []


def test_exact_finite_verification_deadline_stops_before_native_proof(budgeted):
    f = budgeted
    # Six nanoseconds is an exact positive finite override, with no clock or
    # authority simulation. No new proof can fit its preparation overhead.
    f.authorize([{"max_runtime_verification_minutes": 1e-10}])
    f.admit()
    def body(adapter):
        with pytest.raises(VerificationContractError, match="CAPACITY_EXHAUSTED|VERIFICATION_EXHAUSTED"):
            f.owner.seal_checkpoint(f.native_request)
        assert f.owner._ka01_budget.checkpoints_reserved == 0
    f.invoke(body)
    assert f.calls == []


def test_zero_corrections_allows_k1_and_blocks_failed_feedback(budgeted):
    f = budgeted
    f.authorize([{"max_corrections": 0}])
    f.admit()
    def body(adapter):
        record = f.owner.seal_checkpoint(f.native_request)
        source = FactualFeedbackRequest(1, f.native_request, record.digest)
        with pytest.raises(VerificationContractError):
            f.owner.prepare_feedback(source)
        assert f.owner._ka01_budget.checkpoints_spent == 1
        assert f.owner._ka01_budget.corrections_reserved == 0
    f.invoke(body)
    assert len(f.calls) == 1


@pytest.mark.parametrize("limits", [{"max_corrections": 1}, {"max_checkpoints": 2}])
def test_independent_count_thresholds_block_new_feedback_without_charging_replay(budgeted, limits):
    f = budgeted
    f.authorize([limits])
    f.admit()
    def body(adapter):
        first, request = f.prepare_native()
        value = adapter.consume(request)
        assert adapter.consume(request) == value
        f.next_candidate()
        f.native_request = f.checkpoint_request(2, first.digest)
        second = f.owner.seal_checkpoint(f.native_request)
        assert second.data["outstanding"] == []
        assert f.owner._ka01_budget.checkpoints_spent == 2
        assert f.owner._ka01_budget.corrections_spent == 1
        # No extra checkpoint is available even with a distinct committed SHA.
        f.target.sha = f.worktree_commit({"fixture-target.txt": b"PASS third scoped candidate\n"}, "third scoped candidate")
        f.target.tree = f.tree(f.target.sha)
        f.target_request = replace(f.target_request, candidate_sha=f.target.sha, tree_sha=f.target.tree)
        _copy_loose_objects(f.objects, f.remote / "objects")
        f.set_ref("refs/heads/aios/review/" + f.run.run_id, f.target.sha)
        third = f.checkpoint_request(3, second.digest)
        with pytest.raises(VerificationContractError):
            f.owner.seal_checkpoint(third)
        assert f.owner._ka01_budget.checkpoints_reserved == 2
    f.invoke(body)
    assert len(f.calls) == 2


def test_proved_final_checkpoint_at_count_ceiling_uses_original_terminal_once(budgeted):
    f = budgeted
    f.authorize([{"max_corrections": 1}, {"max_checkpoints": 2}])
    f.admit()
    def body(adapter):
        first, request = f.prepare_native()
        adapter.consume(request)
        f.next_candidate()
        f.native_request = f.checkpoint_request(2, first.digest)
        second = f.owner.seal_checkpoint(f.native_request)
        outcome = f.owner.complete(f.package(), f.policy, checkpoint=f.native_request, checkpoint_digest=second.digest)
        assert outcome.result_path.is_file()
        assert f.owner._ka01_budget.checkpoints_spent == 2
        with pytest.raises(VerificationContractError):
            f.owner.seal_checkpoint(f.native_request)
    f.invoke(body)
    assert len(f.calls) == 2
    assert len(list(f.state.results.glob(f.run.run_id + ".json"))) == 1


def test_nonimproving_committed_candidate_cannot_buy_another_correction(budgeted):
    f = budgeted
    f.authorize()
    f.admit()
    def body(adapter):
        first, request = f.prepare_native()
        adapter.consume(request)
        f.target.sha = f.worktree_commit({"fixture-target.txt": b"FAIL second scoped candidate\n"}, "no verified improvement")
        f.target.tree = f.tree(f.target.sha)
        f.target_request = replace(f.target_request, candidate_sha=f.target.sha, tree_sha=f.target.tree)
        _copy_loose_objects(f.objects, f.remote / "objects")
        f.set_ref("refs/heads/aios/review/" + f.run.run_id, f.target.sha)
        f.native_request = f.checkpoint_request(2, first.digest)
        with pytest.raises(VerificationContractError, match="CAUSAL_PROGRESS_NOT_ESTABLISHED"):
            f.owner.seal_checkpoint(f.native_request)
        budget = f.owner._ka01_budget
        assert budget.stopped and budget.pending is not None
        assert budget.checkpoints_reserved == 2 and budget.corrections_spent == 1
        assert (f.directory / "k002.json").is_file()
        with pytest.raises(VerificationContractError):
            f.owner.seal_checkpoint(f.native_request)
    f.invoke(body)
    assert len(f.calls) == 2


@pytest.mark.parametrize("defect", ["withdraw-selection", "withdraw-consent", "withdraw-override", "packed-selector", "profile", "lease", "scope", "clock", "wall", "verification", "ledger", "terminal"])
def test_live_guards_latch_stop_and_preserve_prior_cost_and_source(budgeted, monkeypatch, defect):
    f = budgeted
    f.authorize([{"max_corrections": 2}])
    f.admit()
    def body(adapter):
        record = f.owner.seal_checkpoint(f.native_request)
        budget = f.owner._ka01_budget
        spent, reserved = budget.verification_spent_ns, budget.checkpoints_reserved
        if defect.startswith("withdraw-"):
            number = {"withdraw-selection": 1100, "withdraw-consent": 1101, "withdraw-override": 1000}[defect]
            f.issues[number]["state"] = "closed"
        elif defect == "packed-selector":
            (f.remote / "packed-refs").write_text(
                "# pack-refs with: peeled fully-peeled sorted\n" + f.consent_carrier + " " + f.consent_ref + "/competing\n")
        elif defect == "profile":
            adapter.execution_profile = replace(f.profile, model="substitution")
        elif defect == "lease":
            f.leases.release(f.lease)
            f.leases.acquire(f.run)
        elif defect == "scope":
            (f.repo / "fixture-target.txt").write_text("uncommitted scope change")
        elif defect in {"clock", "wall"}:
            now, real = ka01.time.monotonic_ns(), ka01.time.time_ns()
            delta = budget.wall_limit_ns if defect == "wall" else -budget.wall_limit_ns
            monkeypatch.setattr(ka01.time, "monotonic_ns", lambda: now + delta)
            monkeypatch.setattr(ka01.time, "time_ns", lambda: real + delta)
        elif defect == "verification":
            budget.verification_spent_ns = budget.verification_limit_ns
        elif defect == "ledger":
            (budget.directory / "e002.json").write_bytes(b"{}")
        else:
            (f.state.results / (f.run.run_id + ".json")).write_bytes(b"{}")
        with pytest.raises((VerificationContractError, carrier.GitHubIssueIngressError)):
            f.owner.prepare_feedback(FactualFeedbackRequest(1, f.native_request, record.digest))
        for issue in f.issues.values():
            issue["state"] = "open"
        with pytest.raises(VerificationContractError):
            f.owner.seal_checkpoint(f.native_request)
        assert budget.checkpoints_reserved == reserved
        assert budget.verification_spent_ns >= spent
        assert (f.directory / "k001.json").is_file()
    f.invoke(body)
    assert len(f.calls) == 1


def test_crash_after_native_seal_cannot_repeat_or_reconstruct_authority(budgeted, monkeypatch):
    f = budgeted
    f.authorize()
    f.admit()
    original = completion._checkpoint_write_once
    def crash(path, content):
        if path.name == "e002.json":
            raise OSError("uncertain settlement")
        return original(path, content)
    monkeypatch.setattr(completion, "_checkpoint_write_once", crash)
    def body(adapter):
        with pytest.raises(OSError):
            f.owner.seal_checkpoint(f.native_request)
        assert f.owner._ka01_budget.pending is not None
        assert (f.directory / "k001.json").is_file()
    f.invoke(body)
    rebuilt = RuntimeCompletion(repo=f.repo, state=f.state, task=f.task, run=f.run,
        run_path=f.owner.run_path, verification_runner=f.actual_runner, observation_tracker=None, error_type=RuntimeError)
    with pytest.raises(VerificationContractError):
        rebuilt.seal_checkpoint(f.native_request)
    assert len(f.calls) == 1
    assert (f.directory.parent / ".runtime-mutation/intent.json").is_file()


def test_concurrent_detached_reservation_cannot_mint_capacity(budgeted):
    f = budgeted
    f.authorize()
    f.admit()
    def body(adapter):
        record = f.owner.seal_checkpoint(f.native_request)
        def detached():
            with pytest.raises(VerificationContractError):
                f.owner.seal_checkpoint(f.native_request)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(detached).result(timeout=10)
        with pytest.raises(VerificationContractError):
            f.owner.prepare_feedback(FactualFeedbackRequest(1, f.native_request, record.digest))
        assert f.owner._ka01_budget.checkpoints_reserved == 1
    f.invoke(body)
    assert len(f.calls) == 1


@pytest.mark.parametrize("operation", ["REPAIR", "REMEDIATION", "DIRECT_CANDIDATE"])
def test_primary_source_cannot_impersonate_a_correction_admission(budgeted, operation):
    f = budgeted
    policy = replace(f.policy, kind=operation)
    f.authorize(operation=operation, policy=policy, source_operation="PRIMARY")
    with pytest.raises((VerificationContractError, OSError, KeyError)):
        f.admit(operation=operation)
    assert f.calls == []


@pytest.mark.parametrize("operation", ["REPAIR", "REMEDIATION"])
def test_independent_original_correction_sources_admit_but_native_continuity_blocks(budgeted, operation):
    f = budgeted
    f.original_correction_run(operation)
    f.authorize(operation=operation)
    admitted = f.admit(operation=operation)
    assert admitted["status"] == "ADMITTED_PREACTIVATION"
    assert admitted["native_continuity"] == "BLOCKED"
    assert admitted["authority"]["operation"] == operation
    assert admitted["authority"]["source"]["predecessor"] != f.run.run_id
    assert admitted["authority"]["source"]["affected_verification_sha256"]
    constructions = []
    dispatcher = Dispatcher(selected_executor=f.run.executor, operation=operation,
        execution_profile=f.profile, prospective_runtime=f.owner,
        adapter_factories={f.run.executor: lambda: constructions.append(True)})
    with pytest.raises(DispatcherError, match="CORRECTION_NATIVE_CONTINUITY_BLOCKED"):
        if operation == "REPAIR":
            dispatcher.dispatch_repair(execution=dict(run=f.run))
        else:
            dispatcher.dispatch_remediation(execution=f.policy.remediation_execution)
    assert constructions == f.calls == []
    assert f.owner._ka01_budget.checkpoints_reserved == f.owner._ka01_budget.corrections_reserved == 0


@pytest.mark.parametrize("operation", ["REPAIR", "REMEDIATION"])
@pytest.mark.parametrize("defect", ["authorization", "predecessor", "scope", "primary-consent"])
def test_correction_sources_cannot_be_spoofed_or_inherit_primary_consent(budgeted, operation, defect):
    f = budgeted
    f.original_correction_run(operation)
    f.authorize(operation=operation, consent_change=(lambda body: body.update(operation="PRIMARY"))
                if defect == "primary-consent" else None)
    if defect != "primary-consent":
        path = f.state.repairs / (f.run.run_id + ".json") if operation == "REPAIR" else f.owner.run_path
        document = json.loads(path.read_bytes())
        if operation == "REPAIR":
            if defect == "authorization":
                document["repair_authorization_sha"] = "0" * 40
            elif defect == "predecessor":
                document["failed_run_id"] = "RUN-005-999"
            else:
                document["repair"]["modification_scope"] = ["unrelated.txt"]
        else:
            if defect == "authorization":
                document["remediation_authorization_sha"] = "0" * 40
            elif defect == "predecessor":
                document["predecessor"]["source_run_id"] = "RUN-005-999"
            else:
                document["execution"]["remediation"]["modification_scope"] = ["unrelated.txt"]
        path.write_bytes(ka01._bytes(document))
    with pytest.raises((VerificationContractError, operator.OperatorError, ReviewTransportError, ValueError)):
        f.admit(operation=operation)
    assert f.calls == []


def test_default_changes_reentry_and_replacement_owners_cannot_renew_budget(budgeted, monkeypatch):
    f = budgeted
    f.authorize()
    f.admit()
    original = (f.directory.parent / "ka01-v13/admission.json").read_bytes()
    with pytest.raises(VerificationContractError):
        f.admit()
    monkeypatch.setitem(ka01.DEFAULTS, "max_corrections", 20)
    with pytest.raises(VerificationContractError):
        f.invoke(lambda adapter: None)
    assert f.owner._ka01_budget.limits["max_corrections"] == 2
    assert (f.directory.parent / "ka01-v13/admission.json").read_bytes() == original
