"""L2-03 definitions using real immutable Git and canonical Runtime fixtures.

Only the GitHub HTTP transport is replayed by the inherited fixture. Production
source/target/selection/mapping/RESULT/REVIEW authenticators remain intact.
Runtime owns canonical execution and EVIDENCE; these subjects are synthetic.
"""

import ast
import copy
import inspect
import json
from dataclasses import FrozenInstanceError, replace

import pytest
import yaml

from aios_renew import verification as runtime
from aios_renew.verification_contract import (
    APPLICABILITY_DIMENSIONS, MAX_ORIGINAL_PROOF_BYTES,
    ProofReuseRequest, ReuseCorrectionLocator, VerificationContractError,
    applicability_source_snapshot, format_reuse_request, parse_reuse_request,
    format_reuse, parse_reuse, reuse_data, verification_digest,
)
from tests.test_lean_vp_l2_applicability import ApplicabilityFixture, DEPENDENCIES, digest
from tests.test_lean_vp_l2_original_evidence import encoded, snapshot


class ReuseFixture(ApplicabilityFixture):
    """Reviewed full phases and exact TASK scope, before source authentication."""

    def __init__(self, root, monkeypatch, *, report_mode="complete", distinct_base=False, outside_scope=False, **options):
        self.report_mode, self.distinct_base = report_mode, distinct_base
        self.outside_scope = outside_scope
        super().__init__(root, monkeypatch, **options)
        self.reuse_request = ProofReuseRequest(1, self.request, applicability_source_snapshot(self.source_proof),
            "fixture-proof", verification_digest(self.mapping["proofs"][0]), "PRIMARY")

    def worktree_commit(self, files, message):
        if message == "source dependency closure":
            self.earlier_source_sha = self.source.sha
            if not self.distinct_base:
                self.source.task["scope"]["modify"] = sorted(set(self.source.task["scope"]["modify"]) | {
                    "fixture-target.txt", "pyproject.toml", "tests/bp_v4_probe_plugin.py", *DEPENDENCIES.values()})
                files = {**files, ".ai/tasks/TASK-005.yaml": yaml.safe_dump(self.source.task).encode()}
        elif message == "exact admitted target" and self.outside_scope:
            files = {**files, "unrelated-outside-scope.txt": b"not an authorized target modification\n"}
        return super().worktree_commit(files, message)

    def bind_original(self, source, incomplete):
        if self.distinct_base:
            source.run["base_sha"] = self.earlier_source_sha if source.run_id == "RUN-005-001" else self.source.sha
        super().bind_original(source, incomplete)
        if not source.v2:
            return
        candidate = source.item["verification"]["candidate"]
        calls = [r for r in candidate["reports"] if r["phase"] == "call"]
        reports = []
        for call in calls:
            reports.extend([dict(nodeid=call["nodeid"], phase="setup", outcome="PASS"), copy.deepcopy(call),
                            dict(nodeid=call["nodeid"], phase="teardown", outcome="PASS")])
        if self.report_mode == "missing_setup":
            reports = [r for r in reports if r["phase"] != "setup"]
        elif self.report_mode == "skip":
            for report in reports:
                if report["phase"] == "call":
                    report["outcome"] = "SKIP"
        elif self.report_mode == "empty":
            reports = []
        elif self.report_mode == "diagnostics":
            candidate["errors"] = ["retained original observer uncertainty"]
        elif self.report_mode == "partial_population":
            nodes = [*candidate["collection"]["nodeids"], "tests/test_sample.py::test_missing"]
            from aios_renew.verification_contract import pytest_collection_identity
            candidate["collection"] = dict(nodeids=nodes, identity=pytest_collection_identity(nodes))
        candidate["reports"] = reports
        self.refresh_record(source)

    def change_condition(self, name):
        if name not in {"population", "collection"}:
            return super().change_condition(name)
        # L2-03 adds setup/teardown to the inherited call-only fixture. Rename
        # the whole executed node, retaining a complete, ordered phase record.
        candidate = self.target.item["verification"]["candidate"]
        for report in candidate["reports"]:
            report["nodeid"] = "tests/test_sample.py::test_other"
        from aios_renew.verification_contract import pytest_collection_identity
        nodes = sorted({r["nodeid"] for r in candidate["reports"]})
        candidate["collection"] = dict(nodeids=nodes, identity=pytest_collection_identity(nodes))
        self.refresh_record(self.target)

    def reuse(self, request=None):
        return runtime.observe_proof_reuse(self.repo, expected=request or self.reuse_request)


@pytest.fixture
def reusable(tmp_path, monkeypatch):
    return ReuseFixture(tmp_path / "reuse", monkeypatch)


def assert_blocked(witness):
    data = reuse_data(witness)
    assert witness.outcome == "BLOCKED"
    assert data["target_execution"] == data["target_acceptance"] == "NOT_ASSERTED"
    assert data["execution_permission"] == "NOT_AUTHORIZED"
    assert data["affected_proofs"] == []


def test_authenticated_primary_eligibility_preserves_original_and_is_read_only(reusable):
    fixture = reusable
    before = snapshot(fixture.repo), snapshot(fixture.remote)
    witness = fixture.reuse()
    data = reuse_data(witness)
    assert witness.outcome == "REUSE_ELIGIBLE", witness.reason
    assert witness.authentication == "AUTHENTICATED"
    assert data["source_satisfaction"] == "SATISFACTORY" and data["satisfactory"] is True
    assert data["source_complete"] is True and data["source_unstable"] is False
    assert data["applicability_state"] == "VALID" and data["operation_authorization"] == "AUTHORIZED_PRIMARY"
    assert witness.applicability.source == fixture.source_proof
    assert witness.source_snapshot.identity.subject_sha == fixture.source.sha != fixture.target.sha
    assert witness.source_snapshot.raw_sha256 == digest(fixture.source.raw)
    assert data["target_execution"] == data["target_acceptance"] == "NOT_ASSERTED"
    assert data["execution_permission"] == "NOT_AUTHORIZED"
    assert data["consumer_gate"] == "L3_REAUTHENTICATION_REQUIRED"
    assert data["outstanding_obligations"] == data["affected_proofs"] == []
    assert json.loads(witness.reviewed_proof_json)["entry"]["proof_id"] == "fixture-proof"
    assert parse_reuse(format_reuse(witness)) == witness
    assert parse_reuse_request(format_reuse_request(fixture.reuse_request)) == fixture.reuse_request
    assert fixture.reuse() == witness
    with pytest.raises(FrozenInstanceError):
        witness.authentication = "NOT_ESTABLISHED"
    data["request"]["applicability"]["acceptance_ids"].clear()
    assert fixture.reuse_request.applicability.acceptance_ids == ("AC1",)
    assert (snapshot(fixture.repo), snapshot(fixture.remote)) == before


@pytest.mark.parametrize("code,outcome", [(1, "FAIL"), (2, "ERROR")])
def test_valid_original_failure_or_error_never_becomes_eligible(tmp_path, monkeypatch, code, outcome):
    fixture = ReuseFixture(tmp_path / "negative-outcome", monkeypatch, code=code)
    witness = fixture.reuse()
    assert witness.applicability.validity == "VALID", witness.reason
    assert witness.source_snapshot.outcome == outcome
    assert witness.source_satisfaction[0] == "UNSATISFACTORY"
    assert_blocked(witness)


@pytest.mark.parametrize("mode", ["missing_setup", "skip", "partial_population", "diagnostics"])
def test_valid_exit_zero_without_required_actual_semantics_is_blocked(tmp_path, monkeypatch, mode):
    fixture = ReuseFixture(tmp_path / "exit-zero", monkeypatch, report_mode=mode)
    witness = fixture.reuse()
    assert witness.applicability.validity == "VALID", witness.reason
    assert witness.source_snapshot.outcome == "PASS" and witness.source_snapshot.identity.exit_code == 0
    assert witness.source_satisfaction[0] != "SATISFACTORY"
    assert_blocked(witness)


@pytest.mark.parametrize("options", [dict(v1=True), dict(source_incomplete=True), dict(report_mode="empty"),
    dict(provider_override={name: "INAPPLICABLE" for name in ("phase_outcomes", "collection", "population")})])
def test_historical_missing_incomplete_empty_or_waived_phase_proof_is_not_satisfaction(tmp_path, monkeypatch, options):
    fixture = ReuseFixture(tmp_path / "missing-proof", monkeypatch, **options)
    witness = fixture.reuse()
    if options.get("source_incomplete"):
        assert witness.authentication == "AUTHENTICATED", witness.reason
        assert witness.source_snapshot.outcome == "UNKNOWN"
        assert witness.applicability.validity == "INVALIDATED"
        assert witness.source_satisfaction[0] == "NOT_ESTABLISHED"
        assert parse_reuse(format_reuse(witness)) == witness
    assert_blocked(witness)


@pytest.mark.parametrize("dimension", tuple(DEPENDENCIES))
def test_changed_reviewed_dependency_returns_only_affected_outstanding_proof(tmp_path, monkeypatch, dimension):
    fixture = ReuseFixture(tmp_path / "changed", monkeypatch, changed_git=dimension)
    before = snapshot(fixture.repo), snapshot(fixture.remote)
    witness = fixture.reuse()
    data = reuse_data(witness)
    assert witness.outcome == "INVALIDATED", witness.reason
    assert data["affected_dimensions"] == [dimension]
    assert data["affected_proofs"] == ["fixture-proof"] and data["outstanding_obligations"] == ["fixture-obligation"]
    assert data["execution_permission"] == "NOT_AUTHORIZED"
    assert witness.source_snapshot == fixture.reuse_request.original
    assert (snapshot(fixture.repo), snapshot(fixture.remote)) == before


@pytest.mark.parametrize("dimension", ["population", "collection", "worker_state", "worker_topology", "execution_profile",
    "interpreter", "toolchain", "observer", "environment_digest", "configuration", "completeness", "stability",
    "phase_outcomes", "condition_diagnostics"])
def test_changed_actual_runtime_condition_cannot_reuse(tmp_path, monkeypatch, dimension):
    fixture = ReuseFixture(tmp_path / "changed-runtime", monkeypatch, change_condition=dimension)
    witness = fixture.reuse()
    assert witness.authentication == "AUTHENTICATED", witness.reason
    assert witness.outcome == "INVALIDATED", witness.reason
    data = reuse_data(witness)
    assert dimension in data["affected_dimensions"]
    assert data["affected_proofs"] == ["fixture-proof"]
    assert data["outstanding_obligations"] == ["fixture-obligation"]
    assert data["execution_permission"] == "NOT_AUTHORIZED"
    assert witness.source_snapshot == fixture.reuse_request.original


@pytest.mark.parametrize("dimension", ["population", "collection"])
def test_split_target_phase_population_remains_unauthenticated(tmp_path, monkeypatch, dimension):
    class SplitPhaseFixture(ReuseFixture):
        def change_condition(self, name):
            # Reproduce the call-only fixture's mutation on the full-phase
            # source: the target now splits setup from call/teardown.
            return ApplicabilityFixture.change_condition(self, name)

    fixture = SplitPhaseFixture(tmp_path / "split-phase", monkeypatch, change_condition=dimension)
    witness = fixture.reuse()
    assert witness.authentication == "NOT_ESTABLISHED"
    assert witness.applicability.reason == "TARGET_CONDITION_SOURCE_NOT_ESTABLISHED"
    assert_blocked(witness)


@pytest.mark.parametrize("dimension", APPLICABILITY_DIMENSIONS)
def test_every_missing_condition_family_blocks(tmp_path, monkeypatch, dimension):
    fixture = ReuseFixture(tmp_path / "missing-condition", monkeypatch, omitted=(dimension,))
    assert_blocked(fixture.reuse())


@pytest.mark.parametrize("pin", ["proof_id", "proof_sha256", "provenance_sha256", "evidence_sha256", "raw_sha256"])
def test_replayed_or_substituted_proof_and_raw_pins_block(reusable, pin):
    request = reusable.reuse_request
    if pin in {"proof_id", "proof_sha256"}:
        request = replace(request, **{pin: "unreviewed-proof" if pin == "proof_id" else "0" * 64})
    else:
        request = replace(request, original=replace(request.original, **{pin: "0" * 64}))
    witness = reusable.reuse(request)
    assert witness.authentication == "NOT_ESTABLISHED"
    assert_blocked(witness)


@pytest.mark.parametrize("surface", ["raw", "source-result", "source-ref", "main", "selection", "review", "target-ref",
    "target-run", "competing-failure", "competing-remediation", "human-actor", "edited-human"])
def test_native_authority_and_currentness_fail_closed(reusable, monkeypatch, surface):
    fixture = reusable
    if surface == "raw":
        fixture.source.raw_path.unlink()
    elif surface == "source-result":
        path = fixture.source.state / "results" / (fixture.source.run_id + ".json")
        value = json.loads(path.read_bytes())
        value["evidence"][0]["result"]["summary"] = "disputed independent local record"
        path.write_bytes(encoded(value))
    elif surface == "source-ref":
        fixture.source.ref("review", fixture.target.sha)
    elif surface == "main":
        fixture.set_ref("refs/heads/main", fixture.source.sha)
    elif surface == "selection":
        fixture.set_ref(fixture.selection_ref, fixture.reviewed)
    elif surface == "review":
        fixture.set_ref("refs/heads/aios/review-decision/RUN-006-001", fixture.reviewed)
    elif surface == "target-ref":
        fixture.set_ref("refs/heads/aios/review/" + fixture.target.run_id, fixture.source.sha)
    elif surface == "target-run":
        path = fixture.target.state / "runs" / (fixture.target.run_id + ".json")
        value = json.loads(path.read_bytes())
        value["executor"] = "antigravity"
        path.write_bytes(encoded(value))
    elif surface.startswith("competing-"):
        name = "failure/" + fixture.target.run_id if surface == "competing-failure" else "remediation/" + fixture.target.run_id + "-F1"
        fixture.set_ref("refs/heads/aios/" + name, fixture.source.sha)
    elif surface == "human-actor":
        from aios_renew import github_issue_ingress as carrier
        original_http = fixture.http
        def wrong_actor(repository, suffix):
            value = original_http(repository, suffix)
            if suffix == "/issues/201":
                value["user"]["login"] = "unauthorized-actor"
            return value
        monkeypatch.setattr(carrier, "_github_selection_json", wrong_actor)
    else:
        fixture.edited_at = "2026-01-02T00:00:00Z"
    witness = fixture.reuse()
    assert witness.authentication == "NOT_ESTABLISHED"
    assert_blocked(witness)


def test_valid_source_with_different_original_base_cannot_inherit_primary_authority(tmp_path, monkeypatch):
    fixture = ReuseFixture(tmp_path / "other-base", monkeypatch, distinct_base=True)
    witness = fixture.reuse()
    assert witness.applicability.validity == "VALID", witness.reason
    assert witness.reason == "REUSE_PRIMARY_TASK_REVISION_BASE_OR_IMMUTABLE_SCOPE_MISMATCH"
    assert_blocked(witness)


def test_scope_guard_is_independent_of_reviewed_applicability(tmp_path, monkeypatch):
    fixture = ReuseFixture(tmp_path / "outside-scope", monkeypatch, outside_scope=True)
    witness = fixture.reuse()
    assert witness.applicability.validity == "VALID", witness.reason
    assert witness.reason == "REUSE_CANDIDATE_OUTSIDE_IMMUTABLE_TASK_SCOPE"
    assert_blocked(witness)


@pytest.mark.parametrize("namespace", ["repair", "repair-supersession", "remediation", "failure-artifacts", "publication-recovery"])
def test_real_correction_or_failure_refs_cannot_be_hidden_by_primary_source_pins(reusable, namespace):
    suffix = "/2" if namespace == "repair-supersession" else "-F1" if namespace == "remediation" else ""
    reusable.set_ref("refs/heads/aios/" + namespace + "/" + reusable.source.run_id + suffix, reusable.decision)
    assert_blocked(reusable.reuse())


def correction_locator(fixture, operation):
    return ReuseCorrectionLocator(fixture.source.run_id, fixture.source.sha, fixture.source.artifacts_sha,
        fixture.decision, fixture.selection, "AUTHOR_REPAIR" if operation == "REPAIR" else "AUTHOR_REMEDIATION",
        "1" * 64, "2" * 64, "3" * 64, () if operation == "REPAIR" else ("F1",),
        None if operation == "REPAIR" else fixture.decision)


@pytest.mark.parametrize("operation", ["REPAIR", "REMEDIATION"])
@pytest.mark.parametrize("defect", ["missing", "exact-looking", "stale-authorization", "other-predecessor", "action-switch",
    "unrelated-finding", "closed-finding", "unproven-group", "missing-delta", "changed-verification"])
def test_correction_pins_and_caller_actions_never_replace_native_lineage(reusable, operation, defect):
    locator = correction_locator(reusable, operation)
    if defect == "missing":
        locator = None
    elif defect == "stale-authorization":
        locator = replace(locator, authorization_sha=reusable.source.sha)
    elif defect == "other-predecessor":
        locator = replace(locator, predecessor_run_id="RUN-999-001")
    elif defect == "action-switch":
        locator = replace(locator, action="AUTHOR_REMEDIATION" if operation == "REPAIR" else "AUTHOR_REPAIR")
    elif defect in {"unrelated-finding", "closed-finding", "unproven-group"}:
        locator = replace(locator, finding_ids=("F1", "F2") if defect == "unproven-group" else (defect,))
    elif defect == "missing-delta":
        locator = replace(locator, delta_review_sha=None)
    elif defect == "changed-verification":
        locator = replace(locator, affected_verification_sha256="0" * 64)
    request = replace(reusable.reuse_request, operation=operation, correction=locator)
    witness = reusable.reuse(request)
    assert witness.reason == operation + "_NATIVE_CORRECTION_LINEAGE_NOT_ESTABLISHED"
    assert witness.operation_authorization == "NOT_ESTABLISHED"
    assert_blocked(witness)
    assert parse_reuse_request(format_reuse_request(request)) == request


def test_l2_03_refreshes_authority_after_its_own_binding_reads(reusable, monkeypatch):
    original = runtime._reuse_binding
    def move_after_read(*args):
        value = original(*args)
        reusable.set_ref("refs/heads/main", reusable.source.sha)
        return value
    # Calls the actual validator, then injects only an adversarial external move.
    monkeypatch.setattr(runtime, "_reuse_binding", move_after_read)
    witness = reusable.reuse()
    assert witness.reason == "REUSE_SOURCE_TARGET_OR_REVIEW_CHANGED_AT_BOUNDARY"
    assert_blocked(witness)


@pytest.mark.parametrize("defect", ["extra", "duplicate", "whitespace", "float", "boolean-version", "wrong-format",
    "unknown-operation", "nested-extra", "wrong-source", "too-big", "malformed", "not-object"])
def test_closed_bounded_request_encoding(reusable, defect):
    wire = format_reuse_request(reusable.reuse_request)
    data = json.loads(wire)
    if defect == "extra":
        data["reviewed"] = True
    elif defect == "duplicate":
        wire = b'{"version":1,' + wire[1:]
    elif defect == "whitespace":
        wire += b"\n"
    elif defect == "float":
        data["version"] = 1.0
    elif defect == "boolean-version":
        data["version"] = True
    elif defect == "wrong-format":
        data["format"] = "AIOS_L2_APPLICABILITY"
    elif defect == "unknown-operation":
        data["operation"] = "RETRY"
    elif defect == "nested-extra":
        data["applicability"]["target"]["valid"] = True
    elif defect == "wrong-source":
        data["original"]["identity"]["candidate_sha"] = "0" * 40
    elif defect == "too-big":
        wire = b" " * (MAX_ORIGINAL_PROOF_BYTES + 1)
    elif defect == "malformed":
        wire = b"\xff"
    else:
        wire = b"[]"
    if defect not in {"duplicate", "whitespace", "too-big", "malformed", "not-object"}:
        wire = encoded(data)
    with pytest.raises(VerificationContractError):
        parse_reuse_request(wire)


@pytest.mark.parametrize("field,value", [("target_execution", "PASS"), ("target_acceptance", "PASS"),
    ("execution_permission", "AUTHORIZED"), ("extra", True), ("source_outcome", "FAIL"), ("outstanding_obligations", ["unrelated"])])
def test_serialized_result_cannot_promote_or_substitute_computed_projections(reusable, field, value):
    data = reuse_data(reusable.reuse())
    data[field] = value
    with pytest.raises(VerificationContractError):
        parse_reuse(encoded(data))


def test_parsed_valid_or_eligible_witness_is_not_an_observer_input(reusable):
    witness = parse_reuse(format_reuse(reusable.reuse()))
    for value in (witness, witness.applicability, reuse_data(witness)):
        with pytest.raises(VerificationContractError, match="EXACT_TYPED_REUSE_REQUEST_REQUIRED"):
            runtime.observe_proof_reuse(reusable.repo, expected=value)


def test_l2_03_read_surface_contains_no_execution_or_lifecycle_mutation_calls():
    for function in (runtime.observe_proof_reuse, runtime._reuse_binding, runtime._reuse_scope):
        tree = ast.parse(inspect.getsource(function))
        calls = {node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                 for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))}
        assert not calls.intersection({"execute_verification", "execute_minimum_verification", "execute_publication_verification",
            "write_bytes", "write_text", "mkdir", "unlink", "fetch", "attach_verification_evidence", "dispatch", "run"})
