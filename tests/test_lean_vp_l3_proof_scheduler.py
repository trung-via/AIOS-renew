"""L3-01 definitions: real Git and native authority, no affirmative auth mocks.

Only the inherited GitHub HTTP transport is replayed. Runtime owns canonical
verification and EVIDENCE. Fixtures never run a proof or launch an execution.
"""

import ast
import copy
import inspect
import json
from dataclasses import FrozenInstanceError, replace

import pytest
import yaml

from aios_renew import verification as runtime
from aios_renew.proof_coverage_contract import validate_proof_contract, proof_mapping_digest
from aios_renew.review_transport import L1_SELECTION_PATH, public_run
from aios_renew.verification_contract import (
    ApplicabilityDeclarationPin, ProofSchedulerRequest, VerificationContractError,
    MAX_ORIGINAL_PROOF_BYTES, MAX_SCHEDULER_SOURCES, SCHEDULER_INVARIANT,
    format_scheduler_request, parse_scheduler_request, scheduler_request_data,
    format_scheduler_plan, parse_scheduler_plan, scheduler_plan_data, verification_digest,
)
from tests.git_fixture_support import _copy_loose_objects, _tree_files
from tests.test_lean_vp_l2_applicability import POLICY, digest
from tests.test_lean_vp_l2_original_evidence import encoded, snapshot
from tests.test_lean_vp_l2_reuse_conformance import ReuseFixture, correction_locator


class SchedulerFixture(ReuseFixture):
    def scheduler_request(self, *, reuse=False):
        return ProofSchedulerRequest(1, self.target_request, self.reviewed, self.request.contract,
            self.request.mapping, "PRIMARY", self.target.sha, (self.reuse_request,) if reuse else ())

    def plan(self, request=None, *, reuse=False):
        return runtime.plan_candidate_proofs(self.repo, expected=request or self.scheduler_request(reuse=reuse))

    def erase_prior_proof(self):
        # A run-only target admission and no prior canonical original proof.
        # Immutable historical Git objects confer no source evidence authority.
        for subject in (self.source, self.target):
            for directory in ("results",):
                (subject.state / directory / (subject.run_id + ".json")).unlink()
            subject.raw_path.unlink()
        (self.source.state / "runs" / (self.source.run_id + ".json")).unlink()
        for kind in ("review", "artifacts"):
            (self.remote / "refs/heads/aios" / kind / self.source.run_id).unlink()
        artifacts = self.commit_files({".ai/transport/run.json": public_run(encoded(self.target.run))})
        _copy_loose_objects(self.objects, self.remote / "objects")
        self.set_ref("refs/heads/aios/artifacts/" + self.target.run_id, artifacts)
        self.target_request = replace(self.target_request, artifacts_sha=artifacts, condition_evidence=None)

    def review_population(self, change):
        """Publish a new complete synthetic semantic review, using real validators."""
        contract, mapping = copy.deepcopy(self.contract), copy.deepcopy(self.mapping)
        change(contract, mapping)
        parsed = validate_proof_contract(contract, expected_task=self.task_binding)
        mapping["contract_digest"] = parsed.digest
        obligations = {o.id: o for o in parsed.obligations}
        for entry in mapping["entries"]:
            obligation = obligations[entry["obligation_id"]]
            entry.update(obligation_revision=obligation.revision, obligation_digest=obligation.digest)
        contract_pin = dict(id=parsed.id, revision=parsed.revision, digest=parsed.digest)
        mapping_pin = dict(id=mapping["id"], revision=mapping["revision"], digest=proof_mapping_digest(mapping))
        files = _tree_files(self.repo, self.tree(self.reviewed))
        files.update({self.contract_path: encoded(contract), self.mapping_path: encoded(mapping)})
        reviewed = self.commit_files(files, parents=(self.review_base,))
        artifacts = _tree_files(self.repo, self.tree(self.artifacts))
        package = json.loads(artifacts[".ai/transport/result.json"])
        package["result"]["head_sha"] = reviewed
        for item in package["evidence"]:
            item["subject_sha"] = reviewed
        artifacts[".ai/transport/result.json"] = encoded(package)
        self.artifacts = self.commit_files(artifacts)
        decision_files = dict(files)
        decision_files[".ai/reviews/REVIEW-006-001.yaml"] = yaml.safe_dump(dict(
            review_id="REVIEW-006-001", reviewed_sha=reviewed, mode="PRIMARY", verdict="PASS",
            acceptance=dict(AC1="PASS"), findings=[])).encode()
        self.decision = self.commit_files(decision_files, parents=(reviewed,))
        selected = copy.deepcopy(self.selection_request)
        selected["expected_state"]["expected_main_sha"] = reviewed
        scope = selected["payload"]["scope"]
        scope.update(contract_pin=contract_pin, mapping_pin=mapping_pin)
        scope["acceptance"][0]["obligation_ids"] = sorted(obligations)
        for role, path in (("contract", self.contract_path), ("mapping", self.mapping_path)):
            selected["payload"]["sources"][role] = self.locator(reviewed, path)
        selected["payload"]["run"]["candidate_sha"] = reviewed
        self.issue_body = encoded(selected).decode()
        self.provenance = {**self.provenance, "body_sha256": digest(self.issue_body.encode())}
        self.record = dict(format="AIOS_L1_HUMAN_REVIEW_SELECTION", version=1, request=selected,
            provenance=self.provenance, policy=dict(main_sha=reviewed,
                blob_sha=self.locator(reviewed, ".ai/brain-ingress-carriers.yaml")["blob_sha"], sha256=digest(POLICY)))
        selection_files = {**files, L1_SELECTION_PATH: encoded(self.record)}
        self.selection = self.commit_files(selection_files, parents=(reviewed,))
        _copy_loose_objects(self.objects, self.remote / "objects")
        for ref, sha in (("refs/heads/main", reviewed), (self.selection_ref, self.selection),
                ("refs/heads/aios/review/RUN-006-001", reviewed),
                ("refs/heads/aios/artifacts/RUN-006-001", self.artifacts),
                ("refs/heads/aios/review-decision/RUN-006-001", self.decision)):
            self.set_ref(ref, sha)
        self.reviewed, self.contract, self.mapping, self.selection_request = reviewed, contract, mapping, selected
        original = obligations["fixture-obligation"]
        self.request = replace(self.request, expected_main_sha=reviewed,
            contract=ApplicabilityDeclarationPin(**contract_pin), mapping=ApplicabilityDeclarationPin(**mapping_pin),
            obligation_digest=original.digest)
        proof = next(p for p in mapping["proofs"] if p["id"] == "fixture-proof")
        self.reuse_request = replace(self.reuse_request, applicability=self.request, proof_sha256=verification_digest(proof))


def add_obligation(contract, mapping, identity, *, shared=False, **conditions):
    obligation = copy.deepcopy(contract["obligations"][0])
    obligation.update(id=identity, claim=dict(id=identity + "-claim", text="Separate reviewed " + identity + " semantics"))
    obligation["conditions"].update(conditions)
    if obligation["conditions"]["base_sha"] is not None:
        obligation["kind"] = "comparison"
    contract["obligations"].append(obligation)
    proof_id = "fixture-proof" if shared else identity + "-proof"
    if shared:
        mapping["proofs"][0]["claims"].append(obligation["claim"])
    else:
        mapping["proofs"].append(dict(id=proof_id, kind=obligation["kind"], claims=[obligation["claim"]],
            conditions=obligation["conditions"], provenance=obligation["provenance"]))
    mapping["entries"].append(dict(obligation_id=identity, obligation_revision=1,
        obligation_digest="0" * 64, proof_id=proof_id))


@pytest.fixture
def scheduled(tmp_path, monkeypatch):
    return SchedulerFixture(tmp_path / "scheduler", monkeypatch)


def assert_no_effects(data):
    assert data["target_execution"] == data["target_acceptance"] == "NOT_ASSERTED"
    assert data["execution_permission"] == "NOT_AUTHORIZED"
    assert data["production_activation"] == "NOT_ACTIVATED"
    assert data["checkpoint"] == data["terminal"]
    assert data["checkpoint"]["checkpoint_sealed"] == "NOT_ASSERTED"
    assert data["invariant"] == SCHEDULER_INVARIANT
    assert all(u["execution_permission"] == u["base_replay"] == "NOT_AUTHORIZED" for u in data["units"])


def test_first_candidate_without_any_source_preserves_every_outstanding_obligation(scheduled):
    scheduled.review_population(lambda c, m: add_obligation(c, m, "integration", integration_ref="full-boundary"))
    scheduled.erase_prior_proof()
    before = snapshot(scheduled.repo), snapshot(scheduled.remote)
    plan = scheduled.plan()
    data = scheduler_plan_data(plan)
    assert plan.authentication == "AUTHENTICATED", plan.reason
    assert data["status"] == "PLANNED" and data["sources"] == []
    assert len(data["obligations"]) == len(data["units"]) == 2
    assert {r["obligation"]["id"] for r in data["obligations"]} == {"fixture-obligation", "integration"}
    assert {r["state"] for r in data["obligations"]} == {"OUTSTANDING_PLANNED"}
    assert {a for u in data["units"] for a in u["acceptance_ids"]} == {"AC1"}
    assert parse_scheduler_plan(format_scheduler_plan(plan)) == plan
    assert scheduled.plan() == plan
    assert (snapshot(scheduled.repo), snapshot(scheduled.remote)) == before
    with pytest.raises(FrozenInstanceError):
        plan.authentication = "NOT_ESTABLISHED"
    assert_no_effects(data)


def test_later_candidate_uses_genuine_l2_03_and_preserves_original_proof(scheduled):
    before = snapshot(scheduled.repo), snapshot(scheduled.remote)
    plan = scheduled.plan(reuse=True)
    data = scheduler_plan_data(plan)
    assert data["status"] == "PLANNED", plan.reason
    assert data["units"][0]["state"] == "REUSE_ELIGIBLE"
    original = plan.sources[0].original
    assert original.identity == scheduled.source.expected
    assert original.identity.subject_sha == scheduled.source.sha != scheduled.target.sha
    assert original.identity.evidence_id == scheduled.source.evidence_id
    assert original.raw_sha256 == digest(scheduled.source.raw)
    assert original.provenance_sha256 == scheduled.reuse_request.original.provenance_sha256
    assert data["checkpoint"]["units"] == data["terminal"]["units"] == data["units"]
    assert len({u["unit_id"] for u in data["units"]}) == 1
    assert parse_scheduler_plan(format_scheduler_plan(plan)) == plan
    assert (snapshot(scheduled.repo), snapshot(scheduled.remote)) == before
    assert_no_effects(data)


def test_explicit_shared_mapping_consolidates_execution_without_erasing_claims(scheduled):
    scheduled.review_population(lambda c, m: add_obligation(c, m, "overlap", shared=True))
    plan = scheduled.plan()
    data = scheduler_plan_data(plan)
    assert plan.authentication == "AUTHENTICATED", plan.reason
    assert len(data["units"]) == 1 and len(data["obligations"]) == 2
    assert data["units"][0]["obligation_ids"] == ["fixture-obligation", "overlap"]
    assert len(data["units"][0]["proof"]["claims"]) == 2
    assert {r["state"] for r in data["obligations"]} == {"OUTSTANDING_PLANNED"}
    assert len({r["unit_id"] for r in data["obligations"]}) == 1


@pytest.mark.parametrize("condition,value", [
    ("integration_ref", "full-integration"), ("concurrency_ref", "shared-worker-interference"),
    ("ordering_ref", "reverse-stateful-order"), ("repetition_ref", "repeated-proof"),
    ("fixture_refs", ["different-fixture"]), ("profile_ref", "another-execution-profile"),
    ("test_ref", "a-distinct-reviewed-full-test-closure"), ("collection_ref", "another-collection"),
])
def test_distinct_full_conditions_never_collapse_to_common_test_identity(scheduled, condition, value):
    scheduled.review_population(lambda c, m: add_obligation(c, m, "distinct", **{condition: value}))
    plan = scheduled.plan()
    data = scheduler_plan_data(plan)
    assert plan.authentication == "AUTHENTICATED", plan.reason
    assert len(data["units"]) == 2
    assert len({u["unit_id"] for u in data["units"]}) == 2
    assert {u["state"] for u in data["units"]} == {"OUTSTANDING_PLANNED"}
    assert all(len(u["obligation_ids"]) == 1 for u in data["units"])


def test_narrow_reuse_cannot_erase_distinct_parallel_or_comparison_proof(scheduled):
    def population(c, m):
        add_obligation(c, m, "parallel", worker_mode="parallel", workers=2, concurrency_ref="interference")
        add_obligation(c, m, "baseline", base_sha=scheduled.source.sha)
    scheduled.review_population(population)
    data = scheduler_plan_data(scheduled.plan(reuse=True))
    states = {r["obligation"]["id"]: r["state"] for r in data["obligations"]}
    assert states == {"fixture-obligation": "REUSE_ELIGIBLE", "parallel": "OUTSTANDING_PLANNED", "baseline": "BLOCKED"}
    assert data["units"][-1]["proof"]["kind"] == "comparison"
    assert "BR_1_THROUGH_BR_6" in data["units"][-1]["reason"]
    assert_no_effects(data)


@pytest.mark.parametrize("options,state", [
    ({"changed_git": "helpers"}, "OUTSTANDING_PLANNED"),
    ({"unavailable": ("ordering",)}, "BLOCKED"), ({"source_incomplete": True}, "BLOCKED"),
    ({"code": 1}, "BLOCKED"), ({"code": 2}, "BLOCKED"),
    ({"report_mode": "missing_setup"}, "BLOCKED"), ({"report_mode": "skip"}, "BLOCKED"),
])
def test_changed_unknown_failed_and_incomplete_proofs_cannot_reuse(tmp_path, monkeypatch, options, state):
    f = SchedulerFixture(tmp_path / "source-state", monkeypatch, **options)
    data = scheduler_plan_data(f.plan(reuse=True))
    assert data["units"][0]["state"] == state
    assert data["sources"][0]["outcome"] != "REUSE_ELIGIBLE"
    assert_no_effects(data)


@pytest.mark.parametrize("surface", ["raw", "main", "selection", "review", "target-ref", "target-run",
    "newer-run", "supersession", "human-edit", "outside-scope", "primary-correction-wrapper"])
def test_stale_swapped_superseded_or_correction_authority_blocks(scheduled, surface):
    f = scheduled
    if surface == "raw":
        f.source.raw_path.write_bytes(b"conflicting original capture")
    elif surface == "main":
        f.set_ref("refs/heads/main", f.source.sha)
    elif surface == "selection":
        f.set_ref(f.selection_ref, f.reviewed)
    elif surface == "review":
        f.set_ref("refs/heads/aios/review-decision/RUN-006-001", f.reviewed)
    elif surface == "target-ref":
        f.set_ref("refs/heads/aios/review/" + f.target.run_id, f.source.sha)
    elif surface == "target-run":
        path = f.target.state / "runs" / (f.target.run_id + ".json")
        path.write_bytes(encoded({**f.target.run, "status": "COMPLETED"}))
    elif surface == "newer-run":
        f.set_ref("refs/heads/aios/review/RUN-005-003", f.target.sha)
    elif surface == "supersession":
        f.set_ref("refs/heads/aios/repair-supersession/" + f.target.run_id + "/2", f.selection)
    elif surface == "human-edit":
        f.edited_at = "2026-01-02T00:00:00Z"
    elif surface == "outside-scope":
        # Replace committed HEAD without a corresponding current admitted ref.
        f.worktree_commit({"outside.txt": b"unapproved delta"}, "outside admitted candidate")
    else:
        files = _tree_files(f.repo, f.tree(f.target.artifacts_sha))
        files[".ai/transport/repair.json"] = encoded(dict(operation="REPAIR"))
        artifacts = f.commit_files(files)
        _copy_loose_objects(f.objects, f.remote / "objects")
        f.set_ref("refs/heads/aios/artifacts/" + f.target.run_id, artifacts)
        f.target_request = replace(f.target_request, artifacts_sha=artifacts, condition_evidence=None)
    plan = f.plan(reuse=surface != "primary-correction-wrapper")
    data = scheduler_plan_data(plan)
    assert data["status"] == "BLOCKED"
    assert not any(u["state"] == "REUSE_ELIGIBLE" for u in data["units"])
    assert_no_effects(data)


def test_final_core_rechecks_after_its_own_source_reads(scheduled, monkeypatch):
    original = runtime._scheduler_source_assignments
    def move_after_native_read(*args):
        original(*args)
        scheduled.set_ref("refs/heads/main", scheduled.source.sha)
    monkeypatch.setattr(runtime, "_scheduler_source_assignments", move_after_native_read)
    plan = scheduled.plan()
    assert plan.authentication == "NOT_ESTABLISHED"
    assert scheduler_plan_data(plan)["status"] == "BLOCKED"


def test_source_move_during_final_population_read_cannot_retain_reuse(scheduled, monkeypatch):
    original, reads = runtime._scheduler_read_population, 0
    def move_after_real_read(*args):
        nonlocal reads
        value = original(*args)
        reads += 1
        if reads == 2:
            scheduled.source.raw_path.write_bytes(b"source changed during final native population read")
        return value
    monkeypatch.setattr(runtime, "_scheduler_read_population", move_after_real_read)
    plan = scheduled.plan(reuse=True)
    assert plan.authentication == "NOT_ESTABLISHED"
    assert scheduler_plan_data(plan)["status"] == "BLOCKED"
    assert all(u["state"] == "BLOCKED" for u in scheduler_plan_data(plan)["units"])


@pytest.mark.parametrize("operation", ["REPAIR", "REMEDIATION", "OTHER"])
@pytest.mark.parametrize("pins", [False, True])
def test_corrections_and_unknown_operations_have_no_primary_authority(scheduled, operation, pins):
    correction = correction_locator(scheduled, "REPAIR" if operation == "OTHER" else operation) if pins else None
    request = replace(scheduled.scheduler_request(), operation=operation, correction=correction)
    plan = scheduled.plan(request)
    assert plan.authentication == "NOT_ESTABLISHED"
    assert ("NATIVE_CORRECTION_LINEAGE_NOT_ESTABLISHED" in plan.reason if operation != "OTHER"
            else plan.reason == "SCHEDULER_UNSUPPORTED_OPERATION")
    assert scheduler_plan_data(plan)["status"] == "BLOCKED"
    assert parse_scheduler_request(format_scheduler_request(request)) == request


@pytest.mark.parametrize("defect", ["extra", "missing", "duplicate", "float-version", "oversized", "noncanonical"])
def test_closed_bounded_canonical_request(scheduled, defect):
    request = scheduled.scheduler_request()
    data = scheduler_request_data(request)
    if defect == "extra":
        data["reviewed"] = True
    elif defect == "missing":
        del data["mapping"]
    elif defect == "float-version":
        data["version"] = 1.0
    elif defect == "duplicate":
        content = format_scheduler_request(request).replace(b'"version":1', b'"version":1,"version":1', 1)
    elif defect == "oversized":
        content = b" " * (MAX_ORIGINAL_PROOF_BYTES + 1)
    else:
        content = encoded(data) + b"\n"
    if defect in {"extra", "missing", "float-version"}:
        content = encoded(data)
    with pytest.raises(VerificationContractError):
        parse_scheduler_request(content)
    with pytest.raises(VerificationContractError):
        replace(request, sources=(scheduled.reuse_request,) * (MAX_SCHEDULER_SOURCES + 1))


def test_swapped_replayed_or_caller_added_proof_never_authenticates(scheduled):
    request = scheduled.scheduler_request(reuse=True)
    with pytest.raises(VerificationContractError):
        replace(request, subject_sha=scheduled.source.sha)
    with pytest.raises(VerificationContractError):
        replace(request, sources=(replace(scheduled.reuse_request, applicability=replace(
            scheduled.request, target=replace(scheduled.target_request, run_sha256="1" * 64))),))
    unreviewed = replace(scheduled.reuse_request, proof_id="caller-proof")
    data = scheduler_plan_data(scheduled.plan(replace(request, sources=(unreviewed,))))
    assert data["status"] == "BLOCKED"
    replay = replace(request, mapping=replace(request.mapping, digest="1" * 64), sources=())
    assert scheduler_plan_data(scheduled.plan(replay))["status"] == "BLOCKED"


@pytest.mark.parametrize("defect", ["missing-entry", "narrow-proof", "unmapped-proof", "changed-claim"])
def test_selected_review_cannot_make_inconsistent_or_incomplete_mapping_valid(scheduled, defect):
    def population(c, m):
        add_obligation(c, m, "integration", integration_ref="full-integration")
        if defect == "missing-entry":
            m["entries"].pop()
        elif defect == "narrow-proof":
            m["proofs"][0]["conditions"] = {**m["proofs"][0]["conditions"], "population_size": 2}
        elif defect == "unmapped-proof":
            proof = copy.deepcopy(m["proofs"][0])
            proof.update(id="unmapped")
            proof["conditions"]["ordering_ref"] = "different-order"
            m["proofs"].append(proof)
        else:
            m["proofs"][0]["claims"] = [dict(id="fixture-claim", text="Altered reviewed requirement")]
    scheduled.review_population(population)
    data = scheduler_plan_data(scheduled.plan())
    assert data["status"] == "BLOCKED"
    assert not any(u["state"] == "REUSE_ELIGIBLE" for u in data["units"])


@pytest.mark.parametrize("defect", ["unknown-acceptance", "cross-source", "raw-pin", "source-correction-ref"])
def test_source_identity_and_coverage_cannot_be_borrowed(scheduled, defect):
    request = scheduled.scheduler_request(reuse=True)
    source = scheduled.reuse_request
    if defect == "unknown-acceptance":
        source = replace(source, applicability=replace(source.applicability, acceptance_ids=("AC999",)))
    elif defect == "cross-source":
        identity = replace(source.original.identity, evidence_id="RUN-005-001-V002")
        source = replace(source, applicability=replace(source.applicability, source=identity),
                         original=replace(source.original, identity=identity))
    elif defect == "raw-pin":
        source = replace(source, original=replace(source.original, raw_sha256="1" * 64))
    else:
        scheduled.set_ref("refs/heads/aios/repair/" + scheduled.source.run_id, scheduled.selection)
    data = scheduler_plan_data(scheduled.plan(replace(request, sources=(source,))))
    assert data["status"] == "BLOCKED"
    assert not any(u["state"] == "REUSE_ELIGIBLE" for u in data["units"])
    assert_no_effects(data)


def test_raw_fail_never_removes_comparison_or_authorizes_base(tmp_path, monkeypatch):
    f = SchedulerFixture(tmp_path / "failed-candidate", monkeypatch, code=1)
    f.review_population(lambda c, m: add_obligation(c, m, "baseline", base_sha=f.source.sha))
    data = scheduler_plan_data(f.plan(reuse=True))
    assert len(data["obligations"]) == 2
    assert data["units"][-1]["proof"]["kind"] == "comparison"
    assert data["units"][-1]["state"] == "BLOCKED"
    assert_no_effects(data)


@pytest.mark.parametrize("field", ["units", "obligations", "checkpoint", "terminal", "execution_permission", "extra"])
def test_serialized_plan_cannot_substitute_projections_or_authenticate_itself(scheduled, field):
    plan = scheduled.plan()
    data = scheduler_plan_data(plan)
    data[field] = [] if field in {"units", "obligations"} else "caller-assertion"
    with pytest.raises(VerificationContractError):
        parse_scheduler_plan(encoded(data))
    parsed = parse_scheduler_plan(format_scheduler_plan(plan))
    with pytest.raises(VerificationContractError):
        runtime.plan_candidate_proofs(scheduled.repo, expected=parsed)
    with pytest.raises(VerificationContractError):
        runtime.plan_candidate_proofs(scheduled.repo, expected=scheduled.reuse())


def test_scheduler_surface_has_no_execution_lifecycle_or_dispatch_calls():
    functions = (runtime.plan_candidate_proofs, runtime._scheduler_read_population,
                 runtime._scheduler_namespace, runtime._scheduler_source_assignments, runtime._scheduler_final_roots,
                 runtime._app_reviewed_population)
    forbidden = {"execute_verification", "execute_minimum_verification", "materialize_verification_subject",
        "execute_publication_verification", "attach_verification_evidence", "write_bytes", "write_text",
        "mkdir", "unlink", "fetch", "acquire", "dispatch", "run_executor", "transport_post_pass"}
    for function in functions:
        tree = ast.parse(inspect.getsource(function))
        calls = {n.func.id if isinstance(n.func, ast.Name) else n.func.attr
                 for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))}
        assert not calls & forbidden
