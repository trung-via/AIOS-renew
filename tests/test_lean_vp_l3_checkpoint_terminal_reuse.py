"""L3-02 conformance definitions: real Git, subprocess proof and native authority.

Only GitHub HTTP transport is replayed by the inherited fixture. No affirmative
admission, proof, observation, source-validity or terminal validator is mocked.
Runtime owns executing these tests and constructing canonical EVIDENCE.
"""

import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from aios_renew import runtime as completion
from aios_renew import verification as verifier
from aios_renew.artifacts import Claim, Result, ResultPackage, validate_result_package, validate_evidence, validate_result
from aios_renew.intra_run_proof_contract import (
    CHECKPOINT_RESOURCE_LIMITS, checkpoint_resource_facts, parse_runtime_checkpoint,
    format_runtime_checkpoint,
)
from aios_renew.proof_coverage_contract import validate_proof_contract, proof_mapping_digest
from aios_renew.publication import _run_from_data
from aios_renew.review_transport import L1_SELECTION_PATH, public_run, public_result
from aios_renew.runtime import RuntimeCompletion, primary_completion_policy, persist_failure
from aios_renew.task import parse_task
from aios_renew.verification_contract import (
    ApplicabilityDeclarationPin, ProofCheckpointRequest, ProofSchedulerRequest,
    VerificationContractError, CHECKPOINT_EXECUTION_FORMAT, MAX_ORIGINAL_PROOF_BYTES,
    checkpoint_request_data, format_checkpoint_request, parse_checkpoint_request,
    pytest_collection_identity, verification_digest, scheduler_plan_data,
)
from tests.git_fixture_support import _copy_loose_objects, _tree_files
from tests.test_lean_vp_l2_original_evidence import encoded, snapshot
from tests.test_lean_vp_l3_proof_scheduler import SchedulerFixture, add_obligation


class NativeCheckpointFixture(SchedulerFixture):
    """An admitted RUN with a separately selected full native execution review."""

    def __init__(self, root, monkeypatch, *, multi=False, failing=False):
        self.multi, self.failing, self.calls = multi, failing, []
        super().__init__(root, monkeypatch)
        # A genuine ACTIVE target has no terminal package. Keep only admission.
        (self.target.state / "results" / (self.target.run_id + ".json")).unlink()
        self.target.raw_path.unlink()
        self.target.raw_path.parent.rmdir()
        (self.target.state / "verification" / self.target.run_id).rmdir()
        admission = self.commit_files({".ai/transport/run.json": public_run(encoded(self.target.run))})
        _copy_loose_objects(self.objects, self.remote / "objects")
        self.set_ref("refs/heads/aios/artifacts/" + self.target.run_id, admission)
        self.target_request = replace(self.target_request, artifacts_sha=admission, condition_evidence=None)
        self.git("reset", "--hard", self.target.sha)
        self.state = SimpleNamespace(**{name: self.target.state / name for name in (
            "staging", "preverification", "verification", "results", "failures", "observations", "repairs")})
        self.task = parse_task(_tree_files(self.repo, self.target.tree)[".ai/tasks/TASK-005.yaml"].decode())
        self.run = _run_from_data(self.target.run, "L3-02 native fixture")
        self.owner = RuntimeCompletion(repo=self.repo, state=self.state, task=self.task, run=self.run,
            run_path=self.target.state / "runs" / (self.target.run_id + ".json"),
            verification_runner=self.actual_runner, observation_tracker=None, error_type=RuntimeError)
        self.policy = primary_completion_policy(self.task, base_sha=self.run.base_sha)
        self.directory = self.state.verification / self.run.run_id / "l3-02"

    def actual_runner(self, command, **kwargs):
        self.calls.append(command)
        return subprocess.run(command, **kwargs)

    def git(self, *args):
        return subprocess.run(("git", "-C", str(self.repo), *args), check=True, capture_output=True).stdout

    def worktree_commit(self, files, message):
        if message == "source dependency closure":
            native = Path(__file__).resolve().parents[1]
            files = {**files, **{name: (native / name).read_bytes() for name in (
                "src/aios_renew/verification_contract.py", "src/aios_renew/proof_coverage_contract.py",
                "src/aios_renew/parallel_verification.py", "tests/bp_v4_probe_plugin.py",
                "tests/conftest.py", "tests/git_fixture_support.py", "pyproject.toml")},
                "src/aios_renew/__init__.py": b"",
                "tests/__init__.py": b"",
            }
            commands = []
            populations = [("artifacts", 0)] + ([("integration", 0), ("workers", 2)] if self.multi else [])
            for name, workers in populations:
                commands.append(f"python -m pytest tests/test_{name}.py -n {workers} --dist {'load' if workers else 'no'} --max-worker-restart 0")
                files[f"tests/test_{name}.py"] = (
                    "from pathlib import Path\ndef test_sample():\n"
                    "    assert Path('fixture-target.txt').read_text().startswith('PASS')\n").encode()
            self.source.task["verification"]["required"] = commands
            self.source.item["source"]["command"] = commands[0]
            self.source.item["verification"]["candidate"]["command"] = commands[0]
            self.source.item["verification"]["binding"]["command"] = commands[0]
        if message == "exact admitted target":
            files = {**files, "fixture-target.txt": b"FAIL\n" if self.failing else b"PASS first\n"}
        return super().worktree_commit(files, message)

    def request(self, ordinal=1, predecessor=None, *, mutate=None):
        """Construct a new canonical-shaped independent review, never a witness."""
        contract, mapping = copy.deepcopy(self.contract), copy.deepcopy(self.mapping)
        # Preserve the original mandatory semantic population at each candidate.
        if self.multi and len(contract["obligations"]) == 1:
            add_obligation(contract, mapping, "integration", integration_ref="complete-integration-boundary")
            add_obligation(contract, mapping, "workers", worker_mode="parallel", workers=2,
                concurrency_ref="independent-worker-interference", ordering_ref="complete-worker-order",
                repetition_ref="separate-native-worker-proof")
        manifests = {}
        command_units = ["fixture-proof", *(["integration-proof", "workers-proof"] if self.multi else [])]
        for proof in mapping["proofs"]:
            conditions = proof["conditions"]
            conditions["candidate_sha"] = self.target.sha
            conditions.pop("test_ref", None)
            index = command_units.index(proof["id"]) if proof["id"] in command_units else 0
            name = ("artifacts", "integration", "workers")[index]
            nodes = [f"tests/test_{name}.py::test_sample"]
            manifest = dict(format=CHECKPOINT_EXECUTION_FORMAT, version=1, proof_id=proof["id"],
                claims=proof["claims"], conditions=copy.deepcopy(conditions), command_index=index,
                profile=verifier._v2_profile(self.repo, self.target.sha, self.task.verification.required[index], os.environ),
                toolchain=verifier._v2_toolchain(), collection=dict(nodeids=nodes, identity=pytest_collection_identity(nodes)))
            if mutate is not None:
                mutate(manifest, proof)
            content = encoded(manifest)
            pin = verification_digest(manifest)
            manifests["docs/aios-proof-contracts/l3-executed/" + pin + ".json"] = content
            conditions["test_ref"] = "l3e:" + pin
            for entry in mapping["entries"]:
                if entry["proof_id"] == proof["id"]:
                    next(o for o in contract["obligations"] if o["id"] == entry["obligation_id"])["conditions"] = copy.deepcopy(conditions)
        parsed = validate_proof_contract(contract, expected_task=self.task_binding)
        mapping["contract_digest"] = parsed.digest
        for entry in mapping["entries"]:
            obligation = next(o for o in parsed.obligations if o.id == entry["obligation_id"])
            entry.update(obligation_revision=obligation.revision, obligation_digest=obligation.digest)
        contract_pin = dict(id=parsed.id, revision=parsed.revision, digest=parsed.digest)
        mapping_pin = dict(id=mapping["id"], revision=mapping["revision"], digest=proof_mapping_digest(mapping))
        files = _tree_files(self.repo, self.target.tree)
        review_task = yaml.safe_load(_tree_files(self.repo, self.tree(self.review_base))[".ai/tasks/TASK-006.yaml"])
        changed_paths = [self.contract_path, self.mapping_path, *sorted(manifests)]
        review_task["scope"]["modify"] = changed_paths
        review_task_bytes = yaml.safe_dump(review_task).encode()
        files[".ai/tasks/TASK-006.yaml"] = review_task_bytes
        self.review_base = self.commit_files(files, parents=(self.target.sha,))
        files.update({self.contract_path: encoded(contract), self.mapping_path: encoded(mapping), **manifests})
        self.reviewed = self.commit_files(files, parents=(self.review_base,))
        old = _tree_files(self.repo, self.tree(self.artifacts))
        review_run = json.loads(old[".ai/transport/run.json"])
        review_run["base_sha"] = self.review_base
        review_package = json.loads(old[".ai/transport/result.json"])
        review_package["result"].update(head_sha=self.reviewed, changed_files=changed_paths)
        for item in review_package["evidence"]:
            item["subject_sha"] = self.reviewed
        run_bytes = public_run(encoded(review_run))
        self.artifacts = self.commit_files({".ai/transport/run.json": run_bytes,
                                           ".ai/transport/result.json": public_result(encoded(review_package))})
        decision_files = {**files, ".ai/reviews/REVIEW-006-001.yaml": yaml.safe_dump(dict(
            review_id="REVIEW-006-001", reviewed_sha=self.reviewed, mode="PRIMARY", verdict="PASS",
            acceptance=dict(AC1="PASS"), findings=[])).encode()}
        self.decision = self.commit_files(decision_files, parents=(self.reviewed,))
        selected = copy.deepcopy(self.selection_request)
        selected["expected_state"]["expected_main_sha"] = self.reviewed
        scope = selected["payload"]["scope"]
        scope.update(candidate_sha=self.target.sha, contract_pin=contract_pin, mapping_pin=mapping_pin,
            review_task=dict(task_id="TASK-006", revision=1, envelope_digest=byte_digest(review_task_bytes), acceptance_ids=["AC1"]))
        scope["acceptance"][0]["obligation_ids"] = sorted(o.id for o in parsed.obligations)
        for role, sha, path in (("task", self.target.sha, ".ai/tasks/TASK-005.yaml"),
                ("contract", self.reviewed, self.contract_path), ("mapping", self.reviewed, self.mapping_path)):
            selected["payload"]["sources"][role] = self.locator(sha, path)
        selected["payload"]["review_task_source"] = self.locator(self.review_base, ".ai/tasks/TASK-006.yaml")
        selected["payload"]["run"].update(sha256=byte_digest(run_bytes),
                                         base_sha=self.review_base, candidate_sha=self.reviewed)
        self.issue_body = encoded(selected).decode()
        self.provenance = {**self.provenance, "body_sha256": byte_digest(self.issue_body.encode())}
        record = dict(format="AIOS_L1_HUMAN_REVIEW_SELECTION", version=1, request=selected,
            provenance=self.provenance, policy=dict(main_sha=self.reviewed,
                blob_sha=self.locator(self.reviewed, ".ai/brain-ingress-carriers.yaml")["blob_sha"],
                sha256=self.record["policy"]["sha256"]))
        self.selection = self.commit_files({**files, L1_SELECTION_PATH: encoded(record)}, parents=(self.reviewed,))
        self.selection_ref = "refs/heads/aios/l1-human-selection/TASK-005/" + self.target.sha
        _copy_loose_objects(self.objects, self.remote / "objects")
        for ref, sha in (("refs/heads/main", self.reviewed), (self.selection_ref, self.selection),
                ("refs/heads/aios/review/RUN-006-001", self.reviewed),
                ("refs/heads/aios/artifacts/RUN-006-001", self.artifacts),
                ("refs/heads/aios/review-decision/RUN-006-001", self.decision)):
            self.set_ref(ref, sha)
        self.contract, self.mapping, self.selection_request, self.record = contract, mapping, selected, record
        scheduler = ProofSchedulerRequest(1, self.target_request, self.reviewed,
            ApplicabilityDeclarationPin(**contract_pin), ApplicabilityDeclarationPin(**mapping_pin), "PRIMARY", self.target.sha)
        return ProofCheckpointRequest(1, scheduler, ordinal, predecessor)

    def next_candidate(self):
        self.target.sha = self.worktree_commit({"fixture-target.txt": b"PASS second causally scoped candidate\n"}, "next scoped candidate")
        self.target.tree = self.tree(self.target.sha)
        self.target_request = replace(self.target_request, candidate_sha=self.target.sha, tree_sha=self.target.tree)
        _copy_loose_objects(self.objects, self.remote / "objects")
        self.set_ref("refs/heads/aios/review/" + self.target.run_id, self.target.sha)

    def package(self):
        claims = {}
        for obligation in self.contract["obligations"]:
            claim = obligation["claim"]
            claims[claim["id"]] = Claim(claim["id"], tuple(obligation["acceptance_ids"]), claim["text"], ())
        return ResultPackage(Result(self.target.sha, tuple(claims.values()), ("fixture-target.txt",), ()), ())


def byte_digest(content):
    # Byte digest, not a validity assertion. Native readers authenticate it.
    import hashlib
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def native(tmp_path, monkeypatch):
    return NativeCheckpointFixture(tmp_path / "native", monkeypatch)


def test_real_first_seal_replay_and_terminal_reuse_without_equivalent_execution(native):
    f = native
    request = f.request()
    assert scheduler_plan_data(verifier.plan_candidate_proofs(f.repo, expected=request.scheduler))["status"] == "PLANNED"
    checkpoint = f.owner.seal_checkpoint(request)
    assert checkpoint.data["request"]["ordinal"] == 1
    assert checkpoint.data["request"]["predecessor_digest"] is None
    assert len(f.calls) == 1
    assert checkpoint.data["proofs"][0]["outcome"] == "PASS"
    assert parse_runtime_checkpoint(format_runtime_checkpoint(checkpoint)) == checkpoint
    with pytest.raises(FrozenInstanceError):
        checkpoint.content_json = "forged"
    before = snapshot(f.directory)
    assert f.owner.seal_checkpoint(request) == checkpoint
    assert snapshot(f.directory) == before and len(f.calls) == 1
    assert f.owner.terminal_reuse(request, checkpoint_digest=checkpoint.digest).eligible
    outcome = f.owner.complete(f.package(), f.policy, checkpoint=request, checkpoint_digest=checkpoint.digest)
    terminal = json.loads(outcome.result_path.read_bytes())
    canonical = validate_result_package(task=f.task, run=f.run, result=validate_result(terminal["result"]),
        evidence=tuple(validate_evidence(item) for item in terminal["evidence"]))
    assert canonical.evidence[0].run_id == f.run.run_id
    assert terminal["evidence"] == checkpoint.data["evidence"]
    assert len(f.calls) == 1
    assert f.owner.complete(f.package(), f.policy, checkpoint=request, checkpoint_digest=checkpoint.digest) == outcome
    assert len(f.calls) == 1
    with pytest.raises(VerificationContractError):
        f.owner.seal_checkpoint(request)
    persist_failure(f.repo, state=f.state, task=f.task, run=f.run, run_path=f.owner.run_path,
                    failure=RuntimeError("late competing failure"), transport=False)
    assert not (f.state.failures / (f.run.run_id + ".json")).exists()


def test_distinct_descendant_second_checkpoint_preserves_original_facts(native):
    f = native
    first_request = f.request()
    first = f.owner.seal_checkpoint(first_request)
    original = (f.directory / "k001.json").read_bytes()
    original_raw = Path(first.data["evidence"][0]["raw"]["path"]).read_bytes()
    f.next_candidate()
    second_request = f.request(2, first.digest)
    second = f.owner.seal_checkpoint(second_request)
    assert second.data["request"]["predecessor_digest"] == first.digest
    assert second.data["population_identity"] == first.data["population_identity"]
    assert second.data["resources_before"] == first.data["resources_after"]
    assert second.data["resources_after"]["executions"] == 2
    assert second.data["evidence"][0]["evidence_id"] != first.data["evidence"][0]["evidence_id"]
    assert (f.directory / "k001.json").read_bytes() == original
    assert Path(first.data["evidence"][0]["raw"]["path"]).read_bytes() == original_raw
    assert not f.owner.terminal_reuse(first_request, checkpoint_digest=first.digest).eligible
    assert f.owner.terminal_reuse(second_request, checkpoint_digest=second.digest).eligible
    f.owner.complete(f.package(), f.policy, checkpoint=second_request, checkpoint_digest=second.digest)
    assert len(f.calls) == 2


def test_distinct_integration_worker_order_and_repetition_proofs_remain_mandatory(tmp_path, monkeypatch):
    f = NativeCheckpointFixture(tmp_path / "full", monkeypatch, multi=True)
    request = f.request()
    checkpoint = f.owner.seal_checkpoint(request)
    assert {p["proof_id"] for p in checkpoint.data["proofs"]} == {"fixture-proof", "integration-proof", "workers-proof"}
    assert len(f.calls) == 3
    assert len({p["evidence_id"] for p in checkpoint.data["proofs"]}) == 3
    units = {u["proof_id"]: u for u in checkpoint.data["plan"]["units"]}
    assert units["workers-proof"]["proof"]["conditions"]["workers"] == 2
    f.owner.complete(f.package(), f.policy, checkpoint=request, checkpoint_digest=checkpoint.digest)
    assert len(f.calls) == 3


def test_raw_native_failure_seals_outstanding_facts_but_cannot_finalize(tmp_path, monkeypatch):
    f = NativeCheckpointFixture(tmp_path / "failure", monkeypatch, failing=True)
    request = f.request()
    checkpoint = f.owner.seal_checkpoint(request)
    assert checkpoint.data["proofs"][0]["outcome"] == "FAIL"
    assert checkpoint.data["outstanding"] == ["fixture-obligation"]
    assert not f.owner.terminal_reuse(request, checkpoint_digest=checkpoint.digest).eligible
    with pytest.raises(VerificationContractError):
        f.owner.complete(f.package(), f.policy, checkpoint=request, checkpoint_digest=checkpoint.digest)
    assert len(f.calls) == 1
    assert not (f.state.results / (f.run.run_id + ".json")).exists()


@pytest.mark.parametrize("defect", ["raw", "observation", "main", "run", "candidate", "failure", "conditions", "history"])
def test_changed_native_roots_or_proof_never_grant_terminal_reuse(native, defect):
    f = native
    request = f.request()
    checkpoint = f.owner.seal_checkpoint(request)
    if defect == "raw":
        Path(checkpoint.data["evidence"][0]["raw"]["path"]).write_bytes(b"changed original raw")
    elif defect == "observation":
        (f.directory / "k001/observation-001.json").write_bytes(b"{}")
    elif defect == "main":
        f.set_ref("refs/heads/main", f.source.sha)
    elif defect == "run":
        f.owner.run_path.write_bytes(encoded({**f.target.run, "status": "COMPLETED"}))
    elif defect == "candidate":
        f.next_candidate()
    elif defect == "failure":
        path = f.state.failures / (f.run.run_id + ".json")
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'{"original_failure":"preserved"}')
    elif defect == "conditions":
        os.environ["L3_NATIVE_CHANGED_CONDITION"] = "changed"
    else:
        content = json.loads((f.directory / "k001.json").read_bytes())
        content["resources_after"]["executions"] = None
        (f.directory / "k001.json").write_bytes(encoded(content))
    try:
        assert not f.owner.terminal_reuse(request, checkpoint_digest=checkpoint.digest).eligible
        assert len(f.calls) == 1
    finally:
        os.environ.pop("L3_NATIVE_CHANGED_CONDITION", None)


@pytest.mark.parametrize("defect", ["gap", "predecessor", "same", "base", "operation", "tree", "forged-run"])
def test_checkpoint_identity_and_operation_cannot_authorize_new_effect(native, defect):
    f = native
    request = f.request()
    first = f.owner.seal_checkpoint(request)
    if defect == "gap":
        altered = replace(request, ordinal=3, predecessor_digest=first.digest)
    elif defect == "predecessor":
        altered = replace(request, ordinal=2, predecessor_digest="1" * 64)
    elif defect == "same":
        altered = replace(request, ordinal=2, predecessor_digest=first.digest)
    elif defect == "base":
        target = replace(request.scheduler.target, candidate_sha=f.run.base_sha, tree_sha=f.tree(f.run.base_sha))
        altered = replace(request, scheduler=replace(request.scheduler, target=target, subject_sha=f.run.base_sha))
    elif defect == "operation":
        altered = replace(request, scheduler=replace(request.scheduler, operation="REPAIR"))
    elif defect == "tree":
        altered = replace(request, scheduler=replace(request.scheduler, target=replace(request.scheduler.target, tree_sha=f.source.tree)))
    else:
        altered = replace(request, scheduler=replace(request.scheduler, target=replace(request.scheduler.target, run_sha256="1" * 64)))
    before = snapshot(f.directory)
    with pytest.raises((VerificationContractError, RuntimeError)):
        f.owner.seal_checkpoint(altered)
    assert len(f.calls) == 1 and snapshot(f.directory) == before


def test_two_native_writers_exclude_one_effect_and_exact_replay_is_read_only(native):
    f = native
    request = f.request()
    def deliver():
        try:
            return f.owner.seal_checkpoint(request)
        except VerificationContractError:
            return None
    with ThreadPoolExecutor(max_workers=2) as threads:
        values = list(threads.map(lambda _: deliver(), range(2)))
    accepted = [v for v in values if v is not None]
    assert accepted and len({v.digest for v in accepted}) == 1
    assert len(f.calls) == 1
    assert f.owner.seal_checkpoint(request) == accepted[0]


def test_interrupted_native_execution_preserves_intent_and_cannot_retry(native):
    f = native
    request = f.request()
    def interrupted(*args, **kwargs):
        f.calls.append("interrupted native launch")
        raise KeyboardInterrupt()
    f.owner.verification_runner = interrupted
    with pytest.raises(KeyboardInterrupt):
        f.owner.seal_checkpoint(request)
    fence = f.directory.parent / ".runtime-mutation"
    original = (fence / "intent.json").read_bytes()
    with pytest.raises(VerificationContractError):
        f.owner.seal_checkpoint(request)
    assert len(f.calls) == 1 and (fence / "intent.json").read_bytes() == original
    assert not (f.directory / "k001.json").exists()


@pytest.mark.parametrize("defect", ["unknown-profile", "unknown-population", "narrow-population", "planned-only"])
def test_no_real_complete_native_binding_means_no_pass_checkpoint(native, defect):
    f = native
    if defect == "planned-only":
        request = ProofCheckpointRequest(1, f.scheduler_request(), 1, None)
    else:
        def mutate(manifest, proof):
            if defect == "unknown-profile":
                manifest["profile"]["environment_digest"] = "0" * 64
            elif defect == "unknown-population":
                manifest["collection"] = {}
            else:
                nodes = ["tests/test_artifacts.py::test_unexecuted"]
                manifest["collection"] = dict(nodeids=nodes, identity=pytest_collection_identity(nodes))
        request = f.request(mutate=mutate)
    with pytest.raises((VerificationContractError, RuntimeError)):
        f.owner.seal_checkpoint(request)
    assert not (f.directory / "k001.json").exists()
    if defect != "narrow-population":
        assert not f.calls


@pytest.mark.parametrize("operation", ["REPAIR", "REMEDIATION"])
def test_correction_locators_and_primary_proof_never_supply_correction_authority(native, operation):
    f = native
    request = f.request()
    altered = replace(request, scheduler=replace(request.scheduler, operation=operation))
    with pytest.raises(VerificationContractError):
        f.owner.seal_checkpoint(altered)
    assert not f.calls and not f.directory.exists()


@pytest.mark.parametrize("defect", ["extra", "missing", "duplicate", "float", "encoding", "oversized", "gap-type"])
def test_closed_bounded_checkpoint_request(native, defect):
    request = native.request()
    data = checkpoint_request_data(request)
    if defect == "extra":
        data["authenticated"] = True
    elif defect == "missing":
        del data["ordinal"]
    elif defect == "float":
        data["version"] = 1.0
    elif defect == "gap-type":
        data["ordinal"] = True
    content = encoded(data)
    if defect == "duplicate":
        content = content.replace(b'"ordinal":1', b'"ordinal":1,"ordinal":1', 1)
    elif defect == "encoding":
        content += b"\n"
    elif defect == "oversized":
        content = b" " * (MAX_ORIGINAL_PROOF_BYTES + 1)
    with pytest.raises(VerificationContractError):
        parse_checkpoint_request(content)
    assert parse_checkpoint_request(format_checkpoint_request(request)) == request
    assert not native.calls


def test_unknown_and_exhausted_accounting_are_not_zero_or_retry_permission():
    zero = {key: 0 for key in CHECKPOINT_RESOURCE_LIMITS}
    for name in zero:
        for value in (None, True, -1, CHECKPOINT_RESOURCE_LIMITS[name] + 1):
            with pytest.raises(VerificationContractError):
                checkpoint_resource_facts({**zero, name: value})


def test_ambiguous_terminal_and_conflicting_replay_do_not_create_another_terminal(native):
    f = native
    request = f.request()
    checkpoint = f.owner.seal_checkpoint(request)
    package = f.package()
    outcome = f.owner.complete(package, f.policy, checkpoint=request, checkpoint_digest=checkpoint.digest)
    original = outcome.result_path.read_bytes()
    changed = replace(package, result=replace(package.result, unresolved=("divergent replay",)))
    with pytest.raises(VerificationContractError):
        f.owner.complete(changed, f.policy, checkpoint=request, checkpoint_digest=checkpoint.digest)
    outcome.result_path.write_bytes(b"partial unknown terminal effect")
    with pytest.raises(VerificationContractError):
        f.owner.complete(package, f.policy, checkpoint=request, checkpoint_digest=checkpoint.digest)
    assert len(f.calls) == 1
    assert (f.directory / "k001.json").read_bytes() == format_runtime_checkpoint(checkpoint)
    assert original != outcome.result_path.read_bytes()


def test_comparison_obligation_keeps_all_six_br_gates_and_never_executes_base(native):
    f = native
    add_obligation(f.contract, f.mapping, "baseline", base_sha=f.run.base_sha)
    request = f.request()
    plan = verifier.plan_candidate_proofs(f.repo, expected=request.scheduler)
    assert any(u["proof"]["kind"] == "comparison" for u in scheduler_plan_data(plan)["units"])
    with pytest.raises(VerificationContractError, match="BR_1_THROUGH_BR_6"):
        f.owner.seal_checkpoint(request)
    assert not f.calls and not f.directory.exists()


@pytest.mark.parametrize("fault", ["incomplete", "unstable", "changed-root"])
def test_actual_capture_uncertainty_or_currentness_move_cannot_seal(native, fault):
    f = native
    request = f.request()
    def adversarial(command, **kwargs):
        result = f.actual_runner(command, **kwargs)
        if fault == "changed-root":
            f.set_ref("refs/heads/main", f.source.sha)
        else:
            path = Path(kwargs["env"]["AIOS_BP_V4_PLUGIN_OUTPUT"])
            value = json.loads(path.read_bytes())
            canonical = value["failure_diagnostics"]["canonical"]
            canonical["complete" if fault == "incomplete" else "unstable"] = fault != "incomplete"
            path.write_bytes(encoded(value))
        return result
    f.owner.verification_runner = adversarial
    with pytest.raises((VerificationContractError, RuntimeError)):
        f.owner.seal_checkpoint(request)
    assert len(f.calls) == 1 and not (f.directory / "k001.json").exists()
    assert (f.directory.parent / ".runtime-mutation/intent.json").exists()
    with pytest.raises((VerificationContractError, RuntimeError)):
        f.owner.seal_checkpoint(request)
    assert len(f.calls) == 1


@pytest.mark.parametrize("after_write", [False, True])
def test_checkpoint_write_interruption_is_never_an_implicit_retry(native, monkeypatch, after_write):
    f = native
    request = f.request()
    write_once = completion._checkpoint_write_once
    def interrupted(path, content):
        if path == f.directory / "k001.json":
            if after_write:
                write_once(path, content)
            raise OSError("native checkpoint write interrupted")
        return write_once(path, content)
    monkeypatch.setattr(completion, "_checkpoint_write_once", interrupted)
    with pytest.raises(OSError):
        f.owner.seal_checkpoint(request)
    assert (f.directory / "k001.json").exists() == after_write
    with pytest.raises(VerificationContractError):
        f.owner.seal_checkpoint(request)
    assert len(f.calls) == 1


@pytest.mark.parametrize("complete_write", [False, True])
def test_terminal_crash_only_reconciles_an_exact_durable_fact(native, monkeypatch, complete_write):
    f = native
    request = f.request()
    checkpoint = f.owner.seal_checkpoint(request)
    result_path = f.state.results / (f.run.run_id + ".json")
    write_once = completion._checkpoint_write_once
    def interrupted(path, content):
        if path == result_path:
            write_once(path, content if complete_write else content[:20])
            raise OSError("native terminal write interrupted")
        return write_once(path, content)
    monkeypatch.setattr(completion, "_checkpoint_write_once", interrupted)
    with pytest.raises(OSError):
        f.owner.complete(f.package(), f.policy, checkpoint=request, checkpoint_digest=checkpoint.digest)
    if complete_write:
        outcome = f.owner.complete(f.package(), f.policy, checkpoint=request, checkpoint_digest=checkpoint.digest)
        assert outcome.result_path == result_path
    else:
        with pytest.raises((VerificationContractError, RuntimeError)):
            f.owner.complete(f.package(), f.policy, checkpoint=request, checkpoint_digest=checkpoint.digest)
    persist_failure(f.repo, state=f.state, task=f.task, run=f.run, run_path=f.owner.run_path,
                    failure=RuntimeError("competing terminal after crash"), transport=False)
    assert len(f.calls) == 1 and not (f.state.failures / (f.run.run_id + ".json")).exists()


def test_concurrent_terminal_and_failure_share_one_existing_runtime_owner(native):
    f = native
    request = f.request()
    checkpoint = f.owner.seal_checkpoint(request)
    def finalize():
        try:
            f.owner.complete(f.package(), f.policy, checkpoint=request, checkpoint_digest=checkpoint.digest)
        except (VerificationContractError, RuntimeError):
            pass
    def fail():
        persist_failure(f.repo, state=f.state, task=f.task, run=f.run, run_path=f.owner.run_path,
                        failure=RuntimeError("one immutable original failure"), transport=False)
    with ThreadPoolExecutor(max_workers=2) as threads:
        futures = [threads.submit(finalize), threads.submit(fail)]
        for future in futures:
            future.result()
    terminals = [p for p in (f.state.results / (f.run.run_id + ".json"), f.state.failures / (f.run.run_id + ".json")) if p.exists()]
    assert len(terminals) == 1 and len(f.calls) == 1


@pytest.mark.parametrize("operation", ["REPAIR", "REMEDIATION"])
@pytest.mark.parametrize("defect", ["exact-looking", "predecessor", "action", "finding", "authority", "source", "delta"])
def test_native_correction_lineage_is_required_even_with_full_primary_source(native, operation, defect):
    from tests.test_lean_vp_l2_reuse_conformance import correction_locator
    f = native
    request = f.request()
    locator = correction_locator(f, operation)
    changes = dict(predecessor=dict(predecessor_run_id="RUN-999-001"),
        action=dict(action="ANOTHER_ACTION"), finding=dict(finding_ids=("unrelated-finding",)),
        authority=dict(authorization_sha=f.source.sha), source=dict(predecessor_artifacts_sha=f.source.sha),
        delta=dict(delta_review_sha=None))
    if defect in changes:
        locator = replace(locator, **changes[defect])
    request = replace(request, scheduler=replace(request.scheduler, operation=operation, correction=locator))
    assert parse_checkpoint_request(format_checkpoint_request(request)) == request
    with pytest.raises(VerificationContractError):
        f.owner.seal_checkpoint(request)
    assert not f.calls and not f.directory.exists()


def test_changed_predecessor_capture_blocks_second_checkpoint_before_execution(native):
    f = native
    first_request = f.request()
    first = f.owner.seal_checkpoint(first_request)
    original_record = (f.directory / "k001.json").read_bytes()
    f.next_candidate()
    second_request = f.request(2, first.digest)
    Path(first.data["evidence"][0]["raw"]["path"]).write_bytes(b"changed original predecessor cause")
    with pytest.raises(VerificationContractError):
        f.owner.seal_checkpoint(second_request)
    assert len(f.calls) == 1 and not (f.directory / "k002.json").exists()
    assert (f.directory / "k001.json").read_bytes() == original_record


def test_unaccounted_existing_native_capture_is_never_reset_to_zero(native):
    f = native
    request = f.request()
    directory = f.state.verification / f.run.run_id
    directory.mkdir()
    original = directory / (f.run.run_id + "-V001.raw")
    original.write_bytes(b"STDOUT\noriginal interrupted proof\nSTDERR\n")
    with pytest.raises(VerificationContractError):
        f.owner.seal_checkpoint(request)
    assert original.read_bytes() == b"STDOUT\noriginal interrupted proof\nSTDERR\n"
    assert not f.calls and not f.directory.exists()
