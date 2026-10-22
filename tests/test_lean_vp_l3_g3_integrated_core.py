"""Controlled G3 integration: real native process and real Runtime proof.

Only the inherited original GitHub HTTP transport is replayed. The selected
peer is immutable source in the engineering base and runs through CodexAdapter,
Dispatcher and ExecutorBoundary once. Neither an adapter callback stub nor an
affirmative source/proof/budget validator supplies the positive path. Mapping
review verification below also executes a real focused program. All approvals
are isolated fixture approvals, never production consent or a G6 decision.
Runtime owns executing these definitions and constructing canonical EVIDENCE.
"""

import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
import sys

import pytest
import yaml

from aios_renew import ka01_admission as admission
from aios_renew import operator
from aios_renew import verification
from aios_renew.codex_adapter import CodexAdapter
from aios_renew.dispatcher import Dispatcher, DispatcherError
from aios_renew.intra_run_proof_contract import (
    parse_runtime_checkpoint, parse_runtime_feedback, parse_feedback_consumption,
)
from aios_renew.native_execution_policy import resolve_native_execution_policy
from aios_renew.proof_coverage_contract import validate_proof_contract, proof_mapping_digest
from aios_renew.review_transport import GitSemanticSource, L1_SELECTION_PATH, public_run, public_result
from aios_renew.runtime import evidence_data
from aios_renew.task import parse_semantic_task
from aios_renew.verification_contract import (
    VerificationContractError, scheduler_plan_data,
)
from tests.git_fixture_support import _copy_loose_objects, _tree_files
from tests.test_lean_vp_l3_admission_budget import BudgetFixture


PEER = '''import hashlib, json, subprocess
from pathlib import Path
DEFECT = "none"
def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
def read():
    return json.loads(input())
begin = read()
session, sequence = begin["session"], 0
def send(kind, **data):
    global sequence
    sequence += 1
    print(raw(dict(kind=kind, session=session, sequence=sequence, **data)).decode(), flush=True)
def git(*args):
    return subprocess.run(("git", *args), check=True, capture_output=True).stdout.decode().strip()
profile = dict(begin["profile"])
if DEFECT == "profile":
    profile["model"] = "another-model"
if DEFECT == "session":
    session = "reconstructed-session"
send("READY", run_id=begin["run"]["run_id"], profile=profile)
assert read()["kind"] == "READY_ACCEPTED"
Path("fixture-target.txt").write_text("FAIL first native committed candidate\\n")
git("add", "fixture-target.txt")
git("commit", "-m", "native original current RUN candidate")
send("CHECKPOINT", head_sha=git("rev-parse", "HEAD"))
feedback = read()
assert feedback["kind"] == "FEEDBACK"
assert feedback["feedback"]["status"] == "PREPARED"
assert feedback["feedback"]["binding"]["operation"] == begin["operation"]
assert {p["outcome"] for p in feedback["feedback"]["proofs"]} <= {"PASS", "FAIL"}
assert feedback["feedback"]["outstanding_obligation_ids"]
if DEFECT == "exit-before-ack":
    raise SystemExit(0)
digest = hashlib.sha256(raw(feedback["feedback"])).hexdigest()
if DEFECT == "digest":
    digest = "0" * 64
if DEFECT == "ordinal":
    sequence -= 1
send("ACK", receipt_id=feedback["receipt_id"], feedback_digest=digest)
consumed = read()
assert consumed["kind"] == "CONSUMED" and consumed["receipt_id"] == feedback["receipt_id"]
# The code strategy is owned only by this selected native program. Runtime's
# factual response contains no patch, command, failure detail or fix strategy.
Path("fixture-target.txt").write_text("FAIL unchanged failure\\n" if DEFECT == "no-progress" else "PASS native correction\\n")
git("add", "fixture-target.txt")
if DEFECT == "outside-scope":
    Path("unapproved-finding.txt").write_text("unapproved native mutation\\n")
    git("add", "unapproved-finding.txt")
git("commit", "-m", "native same RUN scoped correction")
send("CHECKPOINT", head_sha=git("rev-parse", "HEAD"))
sealed = read()
assert sealed["kind"] == "CHECKPOINT_SEALED"
claims = {}
for requirement in begin["semantic_task"]["proof_obligations"]:
    claim = requirement["claim"]
    row = claims.setdefault(claim["id"], dict(id=claim["id"], claim=claim["text"], satisfies=[], evidence=[]))
    row["satisfies"] = sorted(set(row["satisfies"]) | set(requirement["acceptance_ids"]))
operation = begin["operation"]
base = (begin["execution"]["result_base_sha"] if operation == "REPAIR" else
        begin["execution"]["remediation"]["reviewed_sha"] if operation == "REMEDIATION" else begin["run"]["base_sha"])
package = dict(result=dict(head_sha=git("rev-parse", "HEAD"),
    claims=[] if operation == "REMEDIATION" else list(claims.values()),
    changed_files=git("diff", "--name-only", base, "HEAD").splitlines(), unresolved=[]), evidence=[])
send("RESULT", package=package)
assert read()["kind"] == "TERMINAL"
if DEFECT == "double-terminal":
    send("RESULT", package=package)
'''


REVIEW_PROOF = '''import hashlib, json
from dataclasses import asdict
from pathlib import Path
import yaml
from aios_renew.proof_coverage_contract import validate_proof_mapping, semantic_requirement, proof_mapping_digest
def test_complete_selected_semantic_population():
    task_bytes = Path(".ai/tasks/TASK-005.yaml").read_bytes()
    task = yaml.safe_load(task_bytes)
    contract = json.loads(Path("docs/aios-proof-contracts/fixture-contract.json").read_bytes())
    mapping = json.loads(Path("docs/aios-proof-contracts/fixture-mapping.json").read_bytes())
    assert contract["task"]["envelope_digest"] == hashlib.sha256(task_bytes).hexdigest()
    coverage = validate_proof_mapping(contract, mapping, expected_task=contract["task"],
        expected_mapping=dict(id=mapping["id"], revision=mapping["revision"],
            digest=proof_mapping_digest(mapping)))
    actual = json.loads(json.dumps([asdict(semantic_requirement(o)) for o in coverage.contract.obligations]))
    assert sorted(actual, key=lambda x:x["id"]) == sorted(task["proof_obligations"], key=lambda x:x["id"])
    assert {a for o in actual for a in o["acceptance_ids"]} == {a["id"] for a in task["acceptance"]}
'''


class G3Fixture(BudgetFixture):
    def __init__(self, root, monkeypatch, *, operation="PRIMARY", defect="none", multi=False):
        self.peer_bytes = PEER.replace('DEFECT = "none"', 'DEFECT = ' + repr(defect)).encode()
        super().__init__(root, monkeypatch, multi=multi)
        self.operation = operation
        self.materialize_original_source_proof()
        if operation != "PRIMARY":
            self.original_correction_run(operation)
        self.publish_semantic_sources()
        self.native = admission.G3NativeSource("g3-native-peer.py", admission._digest(self.peer_bytes),
                                              admission._digest(Path(sys.executable).resolve().read_bytes()))
        self.profile = replace(self.profile, model="offline-native/" + self.native.sha256, reasoning_effort="none")

    def worktree_commit(self, files, message):
        if message == "source dependency closure":
            files = {**files, "g3-native-peer.py": self.peer_bytes, "fixture-target.txt": b"PASS original source\n"}
        return super().worktree_commit(files, message)

    def materialize_original_source_proof(self):
        """Replace inherited inert source captures before they can support G3.

        This is the original predecessor's actual authored proof, executed by
        Runtime on its exact immutable source; no generated PASS metadata is
        accepted as an original proof declaration for these integrated cases.
        """
        self.source.raw_path.unlink()
        with verification.materialize_verification_subject(self.repo, run_id=self.source.run_id,
                subject_sha=self.source.sha) as subject:
            evidence = verification.execute_verification(self.task.verification.required, run_id=self.source.run_id,
                subject_sha=self.source.sha, repository=subject, raw_directory=self.source.raw_path.parent,
                environment={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": os.pathsep.join((str(subject / "src"), str(subject / "tests")))})
        assert evidence and all(e.result.exit_code == 0 for e in evidence)
        self.source.item = evidence_data(evidence[0])
        self.source.raw = Path(evidence[0].raw_path).read_bytes()
        self.source.package["evidence"] = [evidence_data(e) for e in evidence]
        self.source.publish()

    def publish_semantic_sources(self):
        self.checkpoint_request()
        contract, mapping = copy.deepcopy(self.contract), copy.deepcopy(self.mapping)
        host = yaml.safe_load(self.git("show", "HEAD:.ai/tasks/TASK-005.yaml"))
        host.pop("verification")
        host.pop("return_affinity", None)
        host["schema"] = "task-semantic-v1"
        host["proof_obligations"] = [{k: v for k, v in o.items() if k != "provenance"}
                                     for o in contract["obligations"]]
        semantic_bytes = yaml.safe_dump(host).encode()
        parse_semantic_task(semantic_bytes)
        contract["task"]["envelope_digest"] = admission._digest(semantic_bytes)
        parsed = validate_proof_contract(contract, expected_task=contract["task"])
        mapping["contract_digest"] = parsed.digest
        for entry in mapping["entries"]:
            entry["obligation_digest"] = next(o.digest for o in parsed.obligations if o.id == entry["obligation_id"])
        files = _tree_files(self.repo, self.tree(self.reviewed))
        # Use the fixture's actual contract paths rather than imposing another
        # semantic namespace on the source reader.
        review_proof = REVIEW_PROOF.replace("docs/aios-proof-contracts/fixture-contract.json", self.contract_path).replace(
            "docs/aios-proof-contracts/fixture-mapping.json", self.mapping_path)
        review_task = yaml.safe_load(files[".ai/tasks/TASK-006.yaml"])
        review_command = "python -m pytest tests/test_g3_mapping_review.py -n 12 --dist load --max-worker-restart 0"
        review_task["verification"] = dict(policy="minimum-sufficient-v1", required=[review_command])
        files.update({".ai/tasks/TASK-005.yaml": semantic_bytes,
                      ".ai/tasks/TASK-006.yaml": yaml.safe_dump(review_task).encode(),
                      "tests/test_g3_mapping_review.py": review_proof.encode()})
        # Contract/mapping and all execution manifests are actual reviewed delta.
        changed = review_task["scope"]["modify"]
        additions = {path: files[path] for path in changed}
        additions.update({self.contract_path: admission._bytes(contract), self.mapping_path: admission._bytes(mapping)})
        original_base = _tree_files(self.repo, self.tree(self.review_base))
        for path in changed:
            if path in original_base:
                files[path] = original_base[path]
            else:
                files.pop(path, None)
        self.review_base = self.commit_files(files, parents=(self.target.sha,))
        files.update(additions)
        self.reviewed = self.commit_files(files, parents=(self.review_base,))
        review_run = dict(run_id="RUN-006-001", task=dict(id="TASK-006", revision=1), executor="codex",
            base_sha=self.review_base, workspace=str(self.repo), status="ACTIVE", head_sha=None)
        with verification.materialize_verification_subject(self.repo, run_id=review_run["run_id"], subject_sha=self.reviewed) as subject:
            evidence = verification.execute_verification((review_command,), run_id=review_run["run_id"], subject_sha=self.reviewed,
                repository=subject, raw_directory=self.state.verification / "g3-original-mapping-review" / review_run["run_id"],
                environment={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": os.pathsep.join((str(subject / "src"), str(subject / "tests")))})
        assert evidence and all(e.result.exit_code == 0 for e in evidence)
        package = json.loads(_tree_files(self.repo, self.tree(self.artifacts))[".ai/transport/result.json"])
        package["result"].update(head_sha=self.reviewed, changed_files=changed)
        for claim in package["result"]["claims"]:
            claim["evidence"] = [e.evidence_id for e in evidence]
        package["evidence"] = [evidence_data(e) for e in evidence]
        run_bytes = public_run(admission._bytes(review_run))
        self.artifacts = self.commit_files({".ai/transport/run.json": run_bytes,
            ".ai/transport/result.json": public_result(admission._bytes(package))})
        self.decision = self.commit_files({**files, ".ai/reviews/REVIEW-006-001.yaml": yaml.safe_dump(dict(
            review_id="REVIEW-006-001", reviewed_sha=self.reviewed, mode="PRIMARY", verdict="PASS",
            acceptance=dict(AC1="PASS"), findings=[])).encode()}, parents=(self.reviewed,))
        selected = copy.deepcopy(self.selection_request)
        selected["expected_state"]["expected_main_sha"] = self.reviewed
        selected["payload"]["scope"].update(target_task=contract["task"],
            contract_pin=dict(id=parsed.id, revision=parsed.revision, digest=parsed.digest),
            mapping_pin=dict(id=mapping["id"], revision=mapping["revision"], digest=proof_mapping_digest(mapping)),
            review_task=dict(task_id="TASK-006", revision=1,
                envelope_digest=admission._digest(files[".ai/tasks/TASK-006.yaml"]), acceptance_ids=["AC1"]))
        for role, sha, path in (("task", self.review_base, ".ai/tasks/TASK-005.yaml"),
                ("contract", self.reviewed, self.contract_path), ("mapping", self.reviewed, self.mapping_path)):
            selected["payload"]["sources"][role] = self.locator(sha, path)
        selected["payload"]["review_task_source"] = self.locator(self.review_base, ".ai/tasks/TASK-006.yaml")
        selected["payload"]["run"].update(sha256=admission._digest(run_bytes), base_sha=self.review_base, candidate_sha=self.reviewed)
        self.issue_body = admission._bytes(selected).decode()
        self.provenance = {**self.provenance, "body_sha256": admission._digest(self.issue_body.encode())}
        self.record = dict(format="AIOS_L1_HUMAN_REVIEW_SELECTION", version=1, request=selected,
            provenance=self.provenance, policy=dict(main_sha=self.reviewed,
                blob_sha=self.locator(self.reviewed, ".ai/brain-ingress-carriers.yaml")["blob_sha"],
                sha256=self.record["policy"]["sha256"]))
        self.selection = self.commit_files({**files, L1_SELECTION_PATH: admission._bytes(self.record)}, parents=(self.reviewed,))
        _copy_loose_objects(self.objects, self.remote / "objects")
        for ref, sha in (("refs/heads/main", self.reviewed), (self.selection_ref, self.selection),
                ("refs/heads/aios/review/RUN-006-001", self.reviewed),
                ("refs/heads/aios/artifacts/RUN-006-001", self.artifacts),
                ("refs/heads/aios/review-decision/RUN-006-001", self.decision)):
            self.set_ref(ref, sha)
        self.semantic = admission.G3SemanticSources(self.reviewed, self.target.sha,
            *(GitSemanticSource(**selected["payload"]["sources"][role]) for role in ("task", "contract", "mapping")))
        # Corrections have their own original transport; no primary predecessor
        # admission artifact can authenticate this new current RUN.
        current_raw = self.owner.run_path.read_bytes()
        current_head = self.git("rev-parse", "HEAD").decode().strip()
        current_artifact = self.commit_files({".ai/transport/run.json": public_run(current_raw)})
        _copy_loose_objects(self.objects, self.remote / "objects")
        self.set_ref("refs/heads/aios/review/" + self.run.run_id, current_head)
        self.set_ref("refs/heads/aios/artifacts/" + self.run.run_id, current_artifact)

    def authorize_g3(self, overrides=()):
        pins = [self.issue(1000 + i, dict(format="AIOS_KA01_V13_OVERRIDE", version=1, ka01_version="1.3",
            task=asdict(self.run.task), operations=[self.operation], values=values)) for i, values in enumerate(overrides)]
        selection = dict(format="AIOS_KA01_V13_SELECTION", version=1, implementation=admission.G3_IMPLEMENTATION,
            ka01_version="1.3", profile=admission.PROFILE, policy=self.task.verification.policy,
            task=asdict(self.run.task), operations=[self.operation], remote_url=self.url,
            normative=dict(source_sha=self.run.base_sha, path=admission.NORMATIVE_PATH, sha256=admission.NORMATIVE_SHA256),
            overrides=pins, g3=dict(semantic=asdict(self.semantic), native=asdict(self.native)))
        selection_pin = self.issue(1100, selection)
        request = admission.G3AdmissionRequest(1, 1100, selection_pin["sha256"], 1101, "0" * 64, self.semantic, self.native)
        # Compute consent against actual native source readers; these values are
        # not mock validator returns and cannot bypass their later rereads.
        source = operator._ka01_operation_source(self.owner, self.operation, self.policy)
        if self.operation == "REMEDIATION":
            # G3 also freezes the independent DELTA/original finding roots.
            original_request = self.owner._ka01_g3_request
            self.owner._ka01_g3_request = request
            try:
                source = operator._ka01_operation_source(self.owner, self.operation, self.policy)
            finally:
                self.owner._ka01_g3_request = original_request
        _, semantic = admission._g3_semantic(self.owner, request)
        source["g3"] = dict(semantic=semantic, admission=admission._g3_run_admission(self.owner), native=asdict(self.native))
        consent = dict(format="AIOS_KA01_V13_RUN_CONSENT", version=1, mode="CONTROLLED_PREACTIVATION",
            implementation=admission.G3_IMPLEMENTATION, ka01_version="1.3", selection_sha256=selection_pin["sha256"],
            task=asdict(self.run.task), run_id=self.run.run_id, admitted_run_sha256=source["admitted_run_sha256"],
            operation=self.operation, executor=self.run.executor, execution_profile_sha256=admission._digest(self.profile.as_dict()),
            operation_source_sha256=admission._digest(source), effective_limits_sha256=admission._digest(admission.resolve_limits(overrides)))
        consent_pin = self.issue(1101, consent)
        self.admission_request = replace(request, consent_sha256=consent_pin["sha256"])
        carrier = self.commit_files({".ai/transport/ka01-v13.json": admission._bytes(asdict(self.admission_request))})
        _copy_loose_objects(self.objects, self.remote / "objects")
        self.set_ref("refs/heads/aios/ka01-v13-consent/" + self.run.run_id, carrier)
        return operator.preactivate_ka01_v13(runtime=self.owner, request=self.admission_request, operation=self.operation,
            execution_profile=self.profile, policy=self.policy, leases=self.leases, lease=self.lease)

    def dispatch(self):
        self.adapter = CodexAdapter(execution_profile=self.profile, g3_native_source=self.native,
            execution_policy=resolve_native_execution_policy(authorizes_mutation=True))
        self.dispatcher = Dispatcher(selected_executor=self.run.executor, operation=self.operation,
            execution_profile=self.profile, prospective_runtime=self.owner, adapter_factories={self.run.executor: lambda: self.adapter})
        if self.operation == "PRIMARY":
            return self.dispatcher.dispatch_primary(task=self.task, run=self.run, lease=self.lease, leases=self.leases)
        if self.operation == "REMEDIATION":
            return self.dispatcher.dispatch_remediation(execution=self.policy.remediation_execution)
        execution = json.loads(self.policy.lineage_path.read_bytes())
        execution["run"] = self.run
        return self.dispatcher.dispatch_repair(execution=execution)


@pytest.mark.parametrize("operation", ["PRIMARY", "REPAIR", "REMEDIATION"])
@pytest.mark.parametrize("multi", [False, True])
def test_real_native_k1_feedback_scoped_committed_k2_and_single_terminal(tmp_path, monkeypatch, operation, multi):
    f = G3Fixture(tmp_path / operation, monkeypatch, operation=operation, multi=multi)
    original_task, original_run = asdict(f.task), f.owner.run_path.read_bytes()
    authorization = f.authorize_g3([{"max_corrections": 1}, {"max_checkpoints": 2}])
    assert authorization["native_continuity"] == "PINNED_DUPLEX_NATIVE_PROCESS"
    original_admission = (f.directory.parent / "ka01-v13/admission.json").read_bytes()
    package = f.dispatch()
    first = parse_runtime_checkpoint((f.directory / "k001.json").read_bytes())
    second = parse_runtime_checkpoint((f.directory / "k002.json").read_bytes())
    feedback = parse_runtime_feedback((f.directory / "k001.feedback.json").read_bytes())
    receipt = parse_feedback_consumption((f.directory / "k001.consumption.json").read_bytes())
    assert first.data["outstanding"] and second.data["outstanding"] == []
    assert first.data["request"]["scheduler"]["subject_sha"] != second.data["request"]["scheduler"]["subject_sha"]
    assert second.data["request"]["predecessor_digest"] == first.digest
    assert first.data["population_identity"] == second.data["population_identity"]
    assert first.data["execution_identity"] == second.data["execution_identity"]
    assert feedback.data["binding"]["operation"] == receipt.data["binding"]["operation"] == operation
    assert receipt.data["binding"]["feedback_digest"] == feedback.digest
    assert f.adapter._g3_process.pid > 0 and f.adapter._g3_process.returncode == 0
    assert len(f.calls) == 2 * len(f.task.verification.required)
    assert len(feedback.data["proofs"]) == len(f.task.verification.required)
    if multi:
        units = first.data["plan"]["units"]
        workers = next(u for u in units if u["proof_id"] == "workers-proof")["proof"]["conditions"]
        assert workers["workers"] == 2 and workers["worker_mode"] == "parallel"
        assert workers["ordering_ref"] == "complete-worker-order"
        assert workers["repetition_ref"] == "separate-native-worker-proof"
        assert len({u["unit_id"] for u in units}) == 3
    result = json.loads((f.state.results / (f.run.run_id + ".json")).read_bytes())
    assert result["result"]["head_sha"] == package.result.head_sha
    assert {e["subject_sha"] for e in result["evidence"]} == {package.result.head_sha}
    assert asdict(f.task) == original_task and f.owner.run_path.read_bytes() == original_run
    assert (f.directory.parent / "ka01-v13/admission.json").read_bytes() == original_admission
    budget = f.owner._ka01_budget
    assert (budget.checkpoints_spent, budget.corrections_spent) == (2, 1)
    assert budget.verification_spent_ns > 0 and budget.verification_reserved_ns == budget.verification_settled_ns
    assert budget.stopped
    with pytest.raises(VerificationContractError):
        f.owner.seal_checkpoint(f.owner.g3_checkpoint_request())
    with pytest.raises((DispatcherError, VerificationContractError)):
        f.dispatch()
    assert len(f.calls) == 2 * len(f.task.verification.required)


@pytest.mark.parametrize("operation", ["PRIMARY", "REPAIR", "REMEDIATION"])
@pytest.mark.parametrize("defect", ["profile", "session", "digest", "ordinal", "exit-before-ack", "no-progress", "outside-scope"])
def test_native_peer_failures_stop_without_extra_correction_or_terminal(tmp_path, monkeypatch, operation, defect):
    f = G3Fixture(tmp_path / (operation + defect), monkeypatch, operation=operation, defect=defect)
    f.authorize_g3()
    with pytest.raises((VerificationContractError, OSError, RuntimeError)):
        f.dispatch()
    assert f.owner._ka01_budget.stopped
    assert not (f.state.results / (f.run.run_id + ".json")).exists()
    assert len(f.calls) <= (2 if defect == "no-progress" else 1)
    if f.adapter._g3_process is not None:
        assert f.adapter._g3_process.poll() is not None
    with pytest.raises(VerificationContractError):
        f.adapter.confirm_local_feedback(feedback=None, receipt_id="0" * 64)


@pytest.mark.parametrize("surface", ["consent", "mapping-selection", "mapping-decision", "native-source", "lease"])
def test_authenticated_sources_cannot_be_withdrawn_or_replaced_before_native_work(tmp_path, monkeypatch, surface):
    f = G3Fixture(tmp_path / surface, monkeypatch)
    f.authorize_g3()
    if surface == "consent":
        f.issues[1101]["state"] = "closed"
    elif surface == "mapping-selection":
        f.issue_body += " "
    elif surface == "mapping-decision":
        f.set_ref("refs/heads/aios/review-decision/RUN-006-001", f.reviewed)
    elif surface == "native-source":
        (f.repo / f.native.path).write_bytes(b"print('unapproved source')")
    else:
        f.leases.release(f.lease)
        f.leases.acquire(f.run)
    with pytest.raises((VerificationContractError, OSError, RuntimeError, ValueError)):
        f.dispatch()
    assert f.calls == [] and f.owner._ka01_budget.stopped


def test_v2_scheduler_without_its_original_live_admission_is_blocked(tmp_path, monkeypatch):
    f = G3Fixture(tmp_path / "unbound", monkeypatch)
    old = f.native_request if hasattr(f, "native_request") else f.checkpoint_request()
    request = replace(old.scheduler, version=2)
    plan = verification.plan_candidate_proofs(f.repo, expected=request)
    assert plan.authentication == "NOT_ESTABLISHED"
    assert scheduler_plan_data(plan)["status"] == "BLOCKED"
    assert f.calls == []


@pytest.mark.parametrize("operation", ["PRIMARY", "REPAIR", "REMEDIATION"])
@pytest.mark.parametrize("effect", ["withdrawal", "lease-generation", "parallel"])
def test_native_proof_guard_loss_cannot_settle_or_start_more_work(tmp_path, monkeypatch, operation, effect):
    f = G3Fixture(tmp_path / (operation + effect), monkeypatch, operation=operation)
    f.authorize_g3()
    original = f.owner.verification_runner
    def interrupt_after_real_proof(command, **kwargs):
        value = original(command, **kwargs)
        if effect == "withdrawal":
            f.issues[1101]["state"] = "closed"
        elif effect == "lease-generation":
            f.leases.release(f.lease)
            f.leases.acquire(f.run)
        else:
            def detached():
                with pytest.raises(VerificationContractError):
                    f.owner.g3_checkpoint_request()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(detached).result(timeout=10)
        return value
    f.owner.verification_runner = interrupt_after_real_proof
    with pytest.raises((VerificationContractError, RuntimeError, ValueError)):
        f.dispatch()
    budget = f.owner._ka01_budget
    assert budget.stopped and budget.pending is not None
    assert budget.checkpoints_reserved == 1 and budget.checkpoints_spent == 0
    assert len(f.calls) == 1 and f.adapter._g3_process.poll() is not None
    assert not (f.state.results / (f.run.run_id + ".json")).exists()


def test_native_seal_crash_keeps_original_intent_and_checkpoint(tmp_path, monkeypatch):
    from aios_renew import runtime
    f = G3Fixture(tmp_path / "seal-crash", monkeypatch)
    f.authorize_g3()
    original = runtime._checkpoint_write_once
    def crash_after_original_write(path, content):
        original(path, content)
        if path.name == "k001.json":
            raise OSError("unknown effect after durable native seal")
    monkeypatch.setattr(runtime, "_checkpoint_write_once", crash_after_original_write)
    with pytest.raises(OSError):
        f.dispatch()
    retained = (f.directory / "k001.json").read_bytes()
    assert parse_runtime_checkpoint(retained).data["outstanding"]
    assert (f.directory.parent / ".runtime-mutation/intent.json").is_file()
    assert f.owner._ka01_budget.pending is not None
    with pytest.raises(VerificationContractError):
        f.dispatch()
    assert len(f.calls) == 1 and (f.directory / "k001.json").read_bytes() == retained


def test_native_double_terminal_preserves_first_original_result(tmp_path, monkeypatch):
    f = G3Fixture(tmp_path / "double-terminal", monkeypatch, defect="double-terminal")
    f.authorize_g3()
    with pytest.raises(VerificationContractError, match="DOUBLE_TERMINAL"):
        f.dispatch()
    original = (f.state.results / (f.run.run_id + ".json")).read_bytes()
    assert len(f.calls) == 2
    assert f.owner._ka01_budget.stopped
    with pytest.raises(VerificationContractError):
        f.owner.g3_checkpoint_request()
    assert (f.state.results / (f.run.run_id + ".json")).read_bytes() == original
