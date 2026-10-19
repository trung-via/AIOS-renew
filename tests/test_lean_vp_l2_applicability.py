"""L2-02 falsification with immutable Git and original Runtime-layout fixtures.

Only HTTP transport observations are replayed. Source, target, Human selection,
mapping, RESULT and REVIEW authentication are real production readers/validators;
no monkeypatch returns an authenticated proof or an affirmative judgment. All
identities here belong to isolated synthetic fixtures, never production proof.
Runtime owns running these tests and constructing canonical EVIDENCE.
"""

import ast
import copy
import hashlib
import inspect
import json
from dataclasses import FrozenInstanceError, replace

import pytest
import yaml

from aios_renew import github_issue_ingress as carrier
from aios_renew import verification as runtime
from aios_renew.proof_coverage_contract import validate_proof_contract, proof_mapping_digest
from aios_renew.review_transport import public_result, public_run, L1_SELECTION_PATH
from aios_renew.verification_contract import (
    APPLICABILITY_DIMENSIONS, APPLICABILITY_GIT_DIMENSIONS, APPLICABILITY_CONDITIONS_FORMAT,
    ApplicabilityDeclarationPin, ApplicabilityTargetRequest, ProofApplicabilityRequest,
    VerificationContractError, applicability_data, applicability_target_data,
    format_applicability, parse_applicability, format_applicability_request, parse_applicability_request,
    format_original_proof, original_proof_data, verification_digest, pytest_collection_identity,
)
from tests.git_fixture_support import (
    commit_fixture_state, _copy_loose_objects, _write_object, _write_tree, _read_object, _tree_files,
)
from tests.test_lean_vp_l2_original_evidence import SourceFixture, encoded, snapshot


DEPENDENCIES = {
    "implementation_dependencies": "src/fixture_subject.py",
    "test_dependencies": "tests/test_sample.py",
    "fixtures_setup_teardown": "tests/conftest.py",
    "helpers": "tests/fixture_helper.py",
    "shared_state": "fixture_state/shared.txt",
    "generated_state": "fixture_generated/snapshot.txt",
}
ORIGINAL_CONDITIONS = {
    "population", "interpreter", "toolchain", "observer", "environment_digest", "configuration",
    "verification_policy", "execution_profile", "worker_topology", "worker_state", "collection",
    "completeness", "stability", "phase_outcomes", "evidence_kind_schema", "condition_diagnostics",
}
POLICY = b"""format: AIOS_BRAIN_INGRESS_CARRIERS_POLICY
version: 1
github_issue:
  enabled: true
  repository: fixture-owner/fixture-repository
  authorized_actors: [fixture-human]
  title_marker: '[AIOS BRAIN INGRESS]'
  max_body_bytes: 131072
"""


def digest(content):
    return hashlib.sha256(content).hexdigest()


class ApplicabilityFixture:
    """One separately admitted target and an exactly selected mapping review."""

    def __init__(self, root, monkeypatch, *, code=0, v1=False, source_incomplete=False,
                 changed_git=None, change_condition=None, omitted=(), unavailable=(),
                 provider_override=None, no_target_conditions=False, target_incomplete=False, target_v1_shape=False,
                 large_diagnostics=False):
        self.source = SourceFixture(root, v2=not v1, code=code, incomplete=source_incomplete)
        self.repo, self.remote, self.objects = self.source.repo, self.source.remote, self.source.objects
        additions = {path: ("# reviewed static fixture closure: " + name + "\n").encode()
                     for name, path in DEPENDENCIES.items()}
        additions.update({".ai/brain-ingress-carriers.yaml": POLICY, "pyproject.toml": b"# fixture configuration\n"})
        self.source.sha = self.worktree_commit(additions, "source dependency closure")
        self.source.tree = self.tree(self.source.sha)
        self.source.run["base_sha"] = self.source.sha
        self.source.item["subject_sha"] = self.source.sha
        self.source.package["result"]["head_sha"] = self.source.sha
        self.bind_original(self.source, source_incomplete)
        if large_diagnostics:
            self.source.item["verification"]["candidate"]["errors"] = ["D" * 60000] * 2
            self.refresh_record(self.source)
        self.source.publish()
        self.target = copy.copy(self.source)
        delta = {"fixture-target.txt": b"a distinct committed target\n"}
        if changed_git is not None:
            delta[DEPENDENCIES[changed_git]] = b"# changed relevant target dependency\n"
        if change_condition == "observer":
            delta["tests/bp_v4_probe_plugin.py"] = b"# changed actual target observer\n"
        if change_condition == "configuration":
            delta["pyproject.toml"] = b"# changed actual target configuration\n"
        self.target.sha = self.worktree_commit(delta, "exact admitted target")
        self.target.tree = self.tree(self.target.sha)
        self.target.run_id, self.target.evidence_id = "RUN-005-002", "RUN-005-002-V001"
        self.target.run = copy.deepcopy(self.source.run)
        self.target.run.update(run_id=self.target.run_id)
        self.target.item = copy.deepcopy(self.source.item)
        self.target.item.update(run_id=self.target.run_id, evidence_id=self.target.evidence_id, subject_sha=self.target.sha)
        self.target.raw_path = self.target.state / "verification" / self.target.run_id
        if not v1:
            self.target.raw_path /= "candidate"
        self.target.raw_path /= self.target.evidence_id + ".raw"
        self.target.raw_path.parent.mkdir(parents=True)
        self.target.raw_path.write_bytes(self.target.raw)
        self.target.item["raw"]["path"] = str(self.target.raw_path)
        self.bind_original(self.target, target_incomplete)
        if change_condition is not None:
            self.change_condition(change_condition)
        if large_diagnostics:
            self.target.item["verification"]["candidate"]["errors"] = ["D" * 60000] * 2
            self.refresh_record(self.target)
        if target_v1_shape:
            self.target.item.pop("verification")
        self.target.package = dict(result=dict(head_sha=self.target.sha, claims=[], changed_files=[], unresolved=[]),
                                   evidence=[self.target.item])
        self.target.publish()
        self.target_request = ApplicabilityTargetRequest("TASK-005", 1, self.target.run_id, self.source.sha,
            self.target.sha, self.target.tree, self.target.artifacts_sha, digest(encoded(self.target.run)),
            None if no_target_conditions else self.target.expected)
        self.source_proof = self.source.observe().provenance
        assert self.source_proof is not None, self.source.observe().reason

        conditions = dict(candidate_sha=self.target.sha, base_sha=None, population_ref="fixture-population",
            population_size=1, fixture_refs=["static-fixture-closure"], shared_state_ref="static-shared-state",
            profile_ref="recorded-fixture-profile", worker_mode="serial", workers=1, concurrency_ref="no-concurrent-boundary",
            ordering_ref="independent-single-item", integration_ref="isolated-fixture-boundary",
            repetition_ref="single-purpose", toolchain_ref="recorded-toolchain", environment_ref="recorded-environment",
            evidence_kind="VERIFICATION", evidence_schema="V1" if v1 else "V2", collection_ref="recorded-collection")
        self.claim = dict(id="fixture-claim", text="Complete isolated fixture semantics under the exact reviewed conditions")
        self.manifest = dict(format=APPLICABILITY_CONDITIONS_FORMAT, version=1,
            source_proof_sha256=digest(format_original_proof(self.source_proof)), target=applicability_target_data(self.target_request),
            obligation_id="fixture-obligation", obligation_revision=1, acceptance_ids=["AC1"], claim=self.claim,
            conditions=conditions, review_acceptance_id="AC1", dimensions=[])
        for name in APPLICABILITY_DIMENSIONS:
            if name in omitted:
                continue
            provider = "GIT_FOOTPRINT" if name in DEPENDENCIES else "L2_ORIGINAL" if name in ORIGINAL_CONDITIONS else "INAPPLICABLE"
            if name in unavailable:
                provider = "UNAVAILABLE"
            if provider_override and name in provider_override:
                provider = provider_override[name]
            self.manifest["dimensions"].append(dict(name=name, provider=provider,
                reason=("Exact reviewed static dependency closure; no unlisted process state" if provider == "GIT_FOOTPRINT"
                        else "Independent review of this exact obligation declares the dimension irrelevant" if provider == "INAPPLICABLE"
                        else "Exact recorded original source and target conditions required"),
                paths=[DEPENDENCIES[name]] if provider == "GIT_FOOTPRINT" and name in DEPENDENCIES else []))
        manifest_bytes = encoded(self.manifest)
        self.manifest_path = "docs/aios-proof-contracts/l2-conditions/" + digest(manifest_bytes) + ".json"
        conditions = {**conditions, "test_ref": "l2c:" + digest(manifest_bytes)}
        self.contract_path, self.mapping_path = "docs/fixture-contract.json", "docs/fixture-mapping.json"
        review_task = copy.deepcopy(self.source.task)
        review_task.update(task_id="TASK-006", goal="Review the exact selected condition closure",
            acceptance=[dict(id="AC1", condition="Review every dimension, explicit inapplicability and complete source-to-target closure for this obligation.")],
            scope=dict(inspect=[".ai/tasks/TASK-005.yaml"], modify=[self.contract_path, self.mapping_path, self.manifest_path]),
            verification=dict(required=["pytest tests/test_review_fixture.py"]))
        review_task_bytes = yaml.safe_dump(review_task).encode()
        self.review_base = self.worktree_commit({".ai/tasks/TASK-006.yaml": review_task_bytes}, "admit independent mapping review")
        labels = dict(authority_ref="independent-human-selection", review_ref="independent-review", source_ref="immutable-fixture")
        self.task_binding = dict(task_id="TASK-005", revision=1,
            envelope_digest=digest(yaml.safe_dump(self.source.task).encode()), acceptance_ids=["AC1"])
        self.contract = dict(schema="proof-coverage-v1", id="fixture-contract", revision=1,
            execution_default="candidate-first-no-reproduction", task=self.task_binding, provenance=labels,
            obligations=[dict(id="fixture-obligation", revision=1, claim=self.claim, acceptance_ids=["AC1"],
                             kind="candidate", blocking=True, conditions=conditions, provenance=labels)])
        parsed = validate_proof_contract(self.contract, expected_task=self.task_binding)
        obligation = parsed.obligations[0]
        self.mapping = dict(schema="proof-mapping-v1", id="fixture-mapping", revision=1,
            contract_id=parsed.id, contract_revision=1, contract_digest=parsed.digest, provenance=labels,
            proofs=[dict(id="fixture-proof", kind="candidate", claims=[self.claim], conditions=conditions, provenance=labels)],
            entries=[dict(obligation_id=obligation.id, obligation_revision=1, obligation_digest=obligation.digest, proof_id="fixture-proof")])
        changed_paths = [self.contract_path, self.mapping_path, self.manifest_path]
        self.reviewed = self.worktree_commit(dict(zip(changed_paths, (encoded(self.contract), encoded(self.mapping), manifest_bytes))),
                                             "independently reviewed semantic mapping and closure")
        contract_pin = dict(id=parsed.id, revision=1, digest=parsed.digest)
        mapping_pin = dict(id=self.mapping["id"], revision=1, digest=proof_mapping_digest(self.mapping))
        review_run = dict(run_id="RUN-006-001", task=dict(id="TASK-006", revision=1), executor="codex",
                          base_sha=self.review_base, workspace=str(self.repo), head_sha=None, status="ACTIVE")
        review_raw = self.source.state / "verification/RUN-006-001/RUN-006-001-V001.raw"
        review_raw.parent.mkdir(parents=True)
        review_raw.write_bytes(b"STDOUT\nisolated review fixture capture\nSTDERR\n")
        review_package = dict(result=dict(head_sha=self.reviewed, claims=[dict(id="C1", satisfies=["AC1"],
            claim="Exact condition closure and mapping reviewed", evidence=["RUN-006-001-V001"])], changed_files=changed_paths, unresolved=[]),
            evidence=[dict(evidence_id="RUN-006-001-V001", run_id="RUN-006-001", subject_sha=self.reviewed, type="VERIFICATION",
                source=dict(command="pytest tests/test_review_fixture.py"), result=dict(exit_code=0, summary="fixture capture"), raw=dict(path=str(review_raw)))])
        for directory, data in (("runs", review_run), ("results", review_package)):
            (self.source.state / directory / "RUN-006-001.json").write_bytes(encoded(data))
        public_run_bytes = public_run(encoded(review_run))
        self.artifacts = self.commit_files({".ai/transport/run.json": public_run_bytes,
                                           ".ai/transport/result.json": public_result(encoded(review_package))})
        review = dict(review_id="REVIEW-006-001", reviewed_sha=self.reviewed, mode="PRIMARY", verdict="PASS",
                      acceptance=dict(AC1="PASS"), findings=[])
        decision_files = _tree_files(self.repo, self.tree(self.reviewed))
        decision_files[".ai/reviews/REVIEW-006-001.yaml"] = yaml.safe_dump(review).encode()
        self.decision = self.commit_files(decision_files, parents=(self.reviewed,))
        self.url = runtime._original_remote(self.repo)
        self.selection_request = dict(format="AIOS_INGRESS_ENVELOPE", version=1, operation="SELECT_MAPPING_REVIEW",
            identity=dict(target_task_id="TASK-005"), expected_state=dict(expected_main_sha=self.reviewed, expected_remote_url=self.url),
            payload=dict(scope=dict(version=1, review_task=dict(task_id="TASK-006", revision=1,
                envelope_digest=digest(review_task_bytes), acceptance_ids=["AC1"]), target_task=self.task_binding,
                candidate_sha=self.target.sha, contract_pin=contract_pin, mapping_pin=mapping_pin,
                acceptance=[dict(review_acceptance_id="AC1", target_acceptance_ids=["AC1"], obligation_ids=[obligation.id])]),
                sources={role: self.locator(sha, path) for role, sha, path in (
                    ("task", self.target.sha, ".ai/tasks/TASK-005.yaml"), ("contract", self.reviewed, self.contract_path),
                    ("mapping", self.reviewed, self.mapping_path))},
                review_task_source=self.locator(self.review_base, ".ai/tasks/TASK-006.yaml"),
                run=dict(id="RUN-006-001", sha256=digest(public_run_bytes), base_sha=self.review_base, candidate_sha=self.reviewed)))
        self.issue_body = encoded(self.selection_request).decode()
        self.provenance = dict(repository="fixture-owner/fixture-repository", repository_id=101, issue_number=201,
            issue_id=301, issue_node_id="fixture-issue-node", actor="fixture-human", actor_id=401, action="opened",
            created_at="2026-01-01T00:00:00Z", body_sha256=digest(self.issue_body.encode()))
        policy_blob = self.locator(self.reviewed, ".ai/brain-ingress-carriers.yaml")["blob_sha"]
        self.record = dict(format="AIOS_L1_HUMAN_REVIEW_SELECTION", version=1, request=self.selection_request,
            provenance=self.provenance, policy=dict(main_sha=self.reviewed, blob_sha=policy_blob, sha256=digest(POLICY)))
        selection_files = _tree_files(self.repo, self.tree(self.reviewed))
        selection_files[L1_SELECTION_PATH] = encoded(self.record)
        self.selection = self.commit_files(selection_files, parents=(self.reviewed,))
        self.selection_ref = "refs/heads/aios/l1-human-selection/TASK-005/" + self.target.sha
        _copy_loose_objects(self.objects, self.remote / "objects")
        for ref, sha in (("refs/heads/main", self.reviewed), (self.selection_ref, self.selection),
                         ("refs/heads/aios/review/RUN-006-001", self.reviewed),
                         ("refs/heads/aios/artifacts/RUN-006-001", self.artifacts),
                         ("refs/heads/aios/review-decision/RUN-006-001", self.decision)):
            self.set_ref(ref, sha)
        # Current local candidate is independent of published semantic sources.
        (self.repo / ".git/refs/heads/main").write_text(self.target.sha + "\n", encoding="ascii")
        self.request = ProofApplicabilityRequest(1, self.source.expected, self.target_request, self.reviewed,
            ApplicabilityDeclarationPin(**contract_pin), ApplicabilityDeclarationPin(**mapping_pin),
            obligation.id, 1, obligation.digest, ("AC1",))
        self.edited_at, self.http_count, self.http_hook = None, 0, None
        monkeypatch.setattr(carrier, "_github_selection_json", self.http)

    def tree(self, sha):
        return _read_object(self.repo, sha)[1].splitlines()[0][5:].decode()

    def locator(self, sha, path):
        tree = self.tree(sha)
        files = _tree_files(self.repo, tree)
        content = files[path]
        blob = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
        return dict(source_sha=sha, tree_sha=tree, path=path, blob_sha=blob)

    def worktree_commit(self, files, message):
        for name, content in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return commit_fixture_state(self.repo, paths=tuple(files), message=message,
                                    user_name="L2 applicability fixture", user_email="fixture@example.invalid")

    def commit_files(self, files, *, parents=()):
        root = {}
        for path, content in files.items():
            node = root
            parts = path.split("/")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = bytes.fromhex(_write_object(self.objects, "blob", content))
        tree = _write_tree(self.objects, root)
        content = (f"tree {tree}\n" + "".join(f"parent {p}\n" for p in parents)
            + "author Fixture <fixture@example.invalid> 946684800 +0000\n"
            + "committer Fixture <fixture@example.invalid> 946684800 +0000\n\nImmutable fixture\n").encode()
        return _write_object(self.objects, "commit", content)

    def set_ref(self, ref, sha):
        path = self.remote / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sha + "\n", encoding="ascii")

    def bind_original(self, source, incomplete):
        if not source.v2:
            return
        record = source.item["verification"]
        candidate = record["candidate"]
        profile = dict(profile="recorded-fixture-profile", workers=0, distribution="no", max_worker_restart=0,
            collect_only=False, environment_digest=verification_digest({"FIXTURE_ENV": "original"}),
            observer_blob=self.locator(source.sha, "tests/bp_v4_probe_plugin.py")["blob_sha"],
            repository_tool_blobs={"pyproject.toml": self.locator(source.sha, "pyproject.toml")["blob_sha"]})
        candidate.update(subject_sha=source.sha, profile=profile, complete=not incomplete, unstable=incomplete,
                         execution=dict(mode="serial", state="isolated-fixture-state"), errors=[])
        nodes = sorted({r["nodeid"] for r in candidate["reports"]})
        candidate["collection"] = dict(nodeids=nodes, identity=pytest_collection_identity(nodes))
        record["binding"].update(subject_sha=source.sha, base_sha=source.run["base_sha"], tree_sha=source.tree,
                                 profile=copy.deepcopy(profile), toolchain=copy.deepcopy(candidate["toolchain"]))
        record.update(evidence_id=source.evidence_id, run_id=source.run_id, raw_path=str(source.raw_path))
        self.refresh_record(source)

    def refresh_record(self, source):
        if not source.v2:
            return
        record, candidate = source.item["verification"], source.item["verification"]["candidate"]
        for report in candidate.get("reports", []):
            if report["outcome"] == "FAIL":
                report.update(profile=copy.deepcopy(candidate["profile"]), toolchain=copy.deepcopy(candidate["toolchain"]))
        record["binding"].update(profile=copy.deepcopy(candidate["profile"]), toolchain=copy.deepcopy(candidate["toolchain"]))
        record["candidate_digest"] = verification_digest(candidate)

    def change_condition(self, name):
        candidate = self.target.item["verification"]["candidate"]
        if name in {"interpreter", "toolchain"}:
            candidate["toolchain"]["python_version" if name == "interpreter" else "pytest_version"] = "fixture-changed"
        elif name == "environment_digest":
            candidate["profile"]["environment_digest"] = verification_digest({"FIXTURE_ENV": "changed"})
        elif name in {"worker_topology", "execution_profile"}:
            candidate["profile"].update(workers=2, distribution="load")
        elif name == "worker_state":
            candidate["execution"]["state"] = "changed-shared-fixture-state"
        elif name in {"population", "collection"}:
            candidate["reports"][0]["nodeid"] = "tests/test_sample.py::test_other"
            nodes = sorted({r["nodeid"] for r in candidate["reports"]})
            candidate["collection"] = dict(nodeids=nodes, identity=pytest_collection_identity(nodes))
        elif name in {"completeness", "stability"}:
            candidate.update(complete=False, unstable=True)
        elif name == "phase_outcomes":
            candidate["reports"][0]["outcome"] = "SKIP"
        elif name == "condition_diagnostics":
            candidate["errors"] = ["recorded changed observation behavior"]
        elif name not in {"observer", "configuration"}:
            raise AssertionError("unsupported fixture condition mutation")
        self.refresh_record(self.target)

    def http(self, repository, suffix):
        assert repository == "fixture-owner/fixture-repository"
        self.http_count += 1
        if self.http_hook is not None:
            self.http_hook(self.http_count, suffix)
        if suffix == "":
            return dict(full_name=repository, id=101)
        if suffix == "/issues/201":
            return dict(number=201, id=301, node_id="fixture-issue-node", title="[AIOS BRAIN INGRESS]",
                repository_url="https://api.github.com/repos/" + repository, created_at="2026-01-01T00:00:00Z",
                body=self.issue_body, user=dict(login="fixture-human", id=401, type="User"))
        if suffix == "/selection-edit-history/201":
            return dict(data=dict(repository=dict(nameWithOwner=repository, databaseId=101,
                issue=dict(id="fixture-issue-node", number=201, title="[AIOS BRAIN INGRESS]", body=self.issue_body,
                           createdAt="2026-01-01T00:00:00Z", lastEditedAt=self.edited_at))))
        raise AssertionError("unexpected fixture HTTP subject")

    def observe(self, request=None):
        return runtime.observe_proof_applicability(self.repo, expected=request or self.request)


@pytest.fixture
def applicable(tmp_path, monkeypatch):
    return ApplicabilityFixture(tmp_path / "applicability", monkeypatch)


def judgments(value):
    return {dimension.name: dimension for dimension in value.dimensions}


def assert_unknown(value):
    assert value.validity == "UNKNOWN" and value.disposition == "BLOCK"
    assert value.satisfactory is False
    assert tuple(d.name for d in value.dimensions) == APPLICABILITY_DIMENSIONS


def test_fully_authenticated_valid_is_immutable_read_only_and_not_a_target_pass(applicable):
    fixture = applicable
    before = snapshot(fixture.repo), snapshot(fixture.remote)
    witness = fixture.observe()
    assert witness.validity == "VALID", witness.reason
    assert witness.authentication == "AUTHENTICATED"
    assert witness.source == fixture.source_proof
    assert witness.source.identity.subject_sha == fixture.source.sha != fixture.target.sha
    assert witness.source.raw_sha256 == digest(fixture.source.raw)
    assert witness.target.identity == fixture.target_request
    assert witness.target.conditions.identity == fixture.target.expected
    assert witness.authority.selection_sha == fixture.selection
    assert witness.authority.decision_sha == fixture.decision
    assert witness.authority.artifacts_sha == fixture.artifacts
    assert witness.disposition == "APPLICABILITY_ONLY" and witness.satisfactory is False
    for name, judgment in judgments(witness).items():
        expected = "EQUIVALENT" if name in ORIGINAL_CONDITIONS or name in DEPENDENCIES else "INAPPLICABLE"
        assert judgment.status == expected, name
        assert judgment.authority_sha256 and judgment.source.identity_sha256 and judgment.target.identity_sha256
    assert parse_applicability(format_applicability(witness)) == witness
    assert parse_applicability_request(format_applicability_request(fixture.request)) == fixture.request
    assert fixture.observe() == witness
    detached = applicability_data(witness)
    detached["request"]["acceptance_ids"].clear()
    assert witness.request.acceptance_ids == ("AC1",)
    with pytest.raises(FrozenInstanceError):
        witness.source = None
    assert (snapshot(fixture.repo), snapshot(fixture.remote)) == before
    assert applicability_data(witness)["target_execution"] == "NOT_ASSERTED"
    assert applicability_data(witness)["reuse"] == "NOT_AUTHORIZED"


@pytest.mark.parametrize("dimension", tuple(DEPENDENCIES))
def test_every_reviewed_git_dependency_change_invalidates(tmp_path, monkeypatch, dimension):
    fixture = ApplicabilityFixture(tmp_path / dimension, monkeypatch, changed_git=dimension)
    witness = fixture.observe()
    assert witness.validity == "INVALIDATED", witness.reason
    assert judgments(witness)[dimension].status == "CHANGED"
    assert witness.source.identity == fixture.source.expected


@pytest.mark.parametrize("dimension", sorted(ORIGINAL_CONDITIONS - {"verification_policy", "evidence_kind_schema"}))
def test_changed_authenticated_runtime_conditions_never_grant_valid(tmp_path, monkeypatch, dimension):
    fixture = ApplicabilityFixture(tmp_path / dimension, monkeypatch, change_condition=dimension)
    witness = fixture.observe()
    assert witness.validity == "INVALIDATED", witness.reason
    assert judgments(witness)[dimension].status == "CHANGED"


@pytest.mark.parametrize("dimension", APPLICABILITY_DIMENSIONS)
def test_omission_in_every_condition_family_remains_unknown(tmp_path, monkeypatch, dimension):
    fixture = ApplicabilityFixture(tmp_path / dimension, monkeypatch, omitted=(dimension,))
    witness = fixture.observe()
    assert_unknown(witness)
    assert witness.authentication == "AUTHENTICATED"
    assert judgments(witness)[dimension].status == "UNKNOWN"


@pytest.mark.parametrize("dimension", ("environment_content", "concurrency_interference", "ordering", "repetition", "integration_external_boundaries"))
def test_unsupported_actual_condition_provider_is_unknown_not_optimistic(tmp_path, monkeypatch, dimension):
    fixture = ApplicabilityFixture(tmp_path / dimension, monkeypatch, provider_override={dimension: "L2_ORIGINAL"})
    witness = fixture.observe()
    assert_unknown(witness)
    assert judgments(witness)[dimension].status == "UNKNOWN"


def test_material_divergence_dominates_unrelated_missingness_but_not_disputed_authority(tmp_path, monkeypatch):
    fixture = ApplicabilityFixture(tmp_path / "precedence", monkeypatch, changed_git="helpers", omitted=("environment_digest",))
    assert fixture.observe().validity == "INVALIDATED"
    fixture.edited_at = "2026-01-02T00:00:00Z"
    assert_unknown(fixture.observe())


@pytest.mark.parametrize("code,outcome", [(1, "FAIL"), (2, "ERROR")])
def test_valid_original_failure_or_error_cannot_become_satisfactory_pass(tmp_path, monkeypatch, code, outcome):
    fixture = ApplicabilityFixture(tmp_path / outcome, monkeypatch, code=code)
    witness = fixture.observe()
    assert witness.validity == "VALID", witness.reason
    assert witness.source_outcome == outcome
    assert witness.source.identity.exit_code == code and witness.satisfactory is False
    assert witness.source.identity.subject_sha == fixture.source.sha
    data = applicability_data(witness)
    data["source_outcome"] = "PASS"
    with pytest.raises(VerificationContractError):
        parse_applicability(encoded(data))


def test_projection_bound_preserves_known_original_failure_and_raw_identity(tmp_path, monkeypatch):
    fixture = ApplicabilityFixture(tmp_path / "projection-bound", monkeypatch, code=1, large_diagnostics=True)
    witness = fixture.observe()
    assert_unknown(witness)
    assert witness.reason == "APPLICABILITY_PROJECTION_BOUND"
    assert witness.source is None and witness.source_outcome == "FAIL"
    assert witness.source_snapshot.identity == fixture.source.expected
    assert witness.source_snapshot.provenance_sha256 == digest(format_original_proof(fixture.source_proof))
    assert witness.source_snapshot.raw_sha256 == digest(fixture.source.raw)
    assert parse_applicability(format_applicability(witness)) == witness


def test_minimal_v1_and_unstable_v2_are_not_backfilled(tmp_path, monkeypatch):
    historical = ApplicabilityFixture(tmp_path / "v1", monkeypatch, v1=True)
    assert_unknown(historical.observe())
    assert original_proof_data(historical.source_proof)["dimensions"][0]["status"] == "UNKNOWN"
    unstable = ApplicabilityFixture(tmp_path / "unstable", monkeypatch, source_incomplete=True, target_incomplete=True)
    witness = unstable.observe()
    assert_unknown(witness)
    assert witness.source_outcome == "UNKNOWN" and witness.satisfactory is False


def test_no_target_condition_record_does_not_invent_actual_target_conditions(tmp_path, monkeypatch):
    fixture = ApplicabilityFixture(tmp_path / "no-target-conditions", monkeypatch, no_target_conditions=True)
    witness = fixture.observe()
    assert_unknown(witness)
    assert witness.target is not None and witness.target.conditions is None
    assert judgments(witness)["toolchain"].status == "UNKNOWN"


def test_changed_evidence_schema_is_explicitly_invalidated(tmp_path, monkeypatch):
    fixture = ApplicabilityFixture(tmp_path / "schema", monkeypatch, target_v1_shape=True)
    witness = fixture.observe()
    assert witness.validity == "INVALIDATED", witness.reason
    assert judgments(witness)["evidence_kind_schema"].status == "CHANGED"


def test_changed_task_policy_cannot_inherit_the_old_admission_or_mapping(applicable):
    fixture = applicable
    files = _tree_files(fixture.repo, fixture.target.tree)
    task = yaml.safe_load(files[".ai/tasks/TASK-005.yaml"])
    task["verification"]["policy"] = "minimum-sufficient-v1"
    files[".ai/tasks/TASK-005.yaml"] = yaml.safe_dump(task).encode()
    changed = fixture.commit_files(files, parents=(fixture.target.sha,))
    (fixture.repo / ".git/refs/heads/main").write_text(changed + "\n", encoding="ascii")
    target = replace(fixture.target_request, candidate_sha=changed, tree_sha=fixture.tree(changed), condition_evidence=None)
    witness = fixture.observe(replace(fixture.request, target=target))
    assert_unknown(witness)
    assert witness.reason == "TARGET_TASK_CHANGED_AFTER_ADMISSION"


@pytest.mark.parametrize("kind", ("contract", "mapping", "obligation", "acceptance", "main", "target-run", "target-sha", "source-evidence", "source-run"))
def test_wrong_or_replayed_exact_pins_block(applicable, kind):
    request = applicable.request
    if kind in {"contract", "mapping"}:
        request = replace(request, **{kind: replace(getattr(request, kind), digest="0" * 64)})
    elif kind == "obligation":
        request = replace(request, obligation_digest="0" * 64)
    elif kind == "acceptance":
        request = replace(request, acceptance_ids=("AC99",))
    elif kind == "main":
        request = replace(request, expected_main_sha=applicable.review_base)
    elif kind == "target-run":
        request = replace(request, target=replace(request.target, run_sha256="0" * 64))
    elif kind == "target-sha":
        request = replace(request, target=replace(request.target, candidate_sha=applicable.source.sha, condition_evidence=None))
    elif kind == "source-evidence":
        request = replace(request, source=replace(request.source, evidence_id="RUN-005-001-V999"))
    else:
        request = replace(request, source=replace(request.source, run_id="RUN-005-003", evidence_id="RUN-005-003-V001"))
    assert_unknown(applicable.observe(request))


def test_unreviewed_and_cross_task_selection_cannot_authenticate(applicable):
    fixture = applicable
    # A real, different immutable selection object with a substituted TASK.
    data = copy.deepcopy(fixture.record)
    data["request"]["identity"]["target_task_id"] = "TASK-999"
    files = _tree_files(fixture.repo, fixture.tree(fixture.reviewed))
    files[L1_SELECTION_PATH] = encoded(data)
    forged = fixture.commit_files(files, parents=(fixture.reviewed,))
    _copy_loose_objects(fixture.objects, fixture.remote / "objects")
    fixture.set_ref(fixture.selection_ref, forged)
    assert_unknown(fixture.observe())


@pytest.mark.parametrize("surface", ("selection", "review", "manifest", "mapping"))
def test_spoofed_inapplicability_requires_the_exact_reviewed_source(applicable, surface):
    fixture = applicable
    files = _tree_files(fixture.repo, fixture.tree(fixture.reviewed))
    if surface == "selection":
        (fixture.remote / fixture.selection_ref).unlink()
    elif surface == "review":
        files[".ai/reviews/REVIEW-006-001.yaml"] = yaml.safe_dump(dict(review_id="REVIEW-006-001", reviewed_sha=fixture.reviewed,
            mode="PRIMARY", verdict="CHANGES_REQUIRED", acceptance=dict(AC1="FAIL"), findings=[])).encode()
        sha = fixture.commit_files(files, parents=(fixture.reviewed,))
        _copy_loose_objects(fixture.objects, fixture.remote / "objects")
        fixture.set_ref("refs/heads/aios/review-decision/RUN-006-001", sha)
    else:
        if surface == "manifest":
            fraudulent = copy.deepcopy(fixture.manifest)
            for dimension in fraudulent["dimensions"]:
                dimension.update(provider="INAPPLICABLE", paths=[])
            files[fixture.manifest_path] = encoded(fraudulent)
        else:
            mapping = copy.deepcopy(fixture.mapping)
            mapping["proofs"][0]["conditions"]["environment_ref"] = "caller-claims-reviewed"
            files[fixture.mapping_path] = encoded(mapping)
        sha = fixture.commit_files(files, parents=(fixture.reviewed,))
        _copy_loose_objects(fixture.objects, fixture.remote / "objects")
        fixture.set_ref("refs/heads/main", sha)
        fixture.request = replace(fixture.request, expected_main_sha=sha)
    assert_unknown(fixture.observe())


def test_same_tree_without_target_base_lineage_is_not_current_target_authority(applicable):
    fixture = applicable
    unrelated = fixture.commit_files(_tree_files(fixture.repo, fixture.target.tree))
    target = replace(fixture.target_request, candidate_sha=unrelated, condition_evidence=None)
    (fixture.repo / ".git/refs/heads/main").write_text(unrelated + "\n", encoding="ascii")
    assert_unknown(fixture.observe(replace(fixture.request, target=target)))


@pytest.mark.parametrize("kind,suffix", (("repair", ""), ("failure", ""), ("remediation", "-001"),
                                        ("repair-supersession", "/replayed"), ("publication-recovery", "")))
def test_competing_target_lifecycle_namespace_cannot_claim_currentness(applicable, kind, suffix):
    applicable.set_ref("refs/heads/aios/" + kind + "/" + applicable.target.run_id + suffix, applicable.target.sha)
    witness = applicable.observe()
    assert_unknown(witness)
    assert witness.reason == "TARGET_CANONICAL_REFS_MISSING_STALE_OR_COMPETING"


def test_missing_private_raw_and_unsupported_provider_do_not_trigger_resolution(applicable, tmp_path, monkeypatch):
    applicable.source.raw_path.unlink()
    assert_unknown(applicable.observe())
    unsupported = ApplicabilityFixture(tmp_path / "unsupported-provider", monkeypatch,
        provider_override={"ordering": "CALLER_ATTESTED"})
    witness = unsupported.observe()
    assert_unknown(witness)
    assert witness.reason == "UNSUPPORTED_REVIEWED_CONDITION_PROVIDER"


@pytest.mark.parametrize("surface", ("source-raw", "source-result", "source-refs", "target-run", "target-head", "target-refs", "review-refs", "human-edit"))
def test_concurrent_mutable_root_changes_block_even_after_affirmative_judgments(applicable, surface):
    fixture = applicable
    def mutate(count, suffix):
        if count != 4:  # Second independent Human reauthentication, after reduction inputs.
            return
        if surface == "source-raw":
            fixture.source.raw_path.write_bytes(b"STDOUT\nracy changed source\nSTDERR\n")
        elif surface == "source-result":
            path = fixture.source.state / "results" / (fixture.source.run_id + ".json")
            data = json.loads(path.read_bytes())
            data["evidence"][0]["result"]["summary"] = "changed private original observation"
            path.write_bytes(encoded(data))
        elif surface == "source-refs":
            fixture.source.ref("artifacts", fixture.target.artifacts_sha)
        elif surface == "target-run":
            path = fixture.source.state / "runs" / (fixture.target.run_id + ".json")
            data = json.loads(path.read_bytes())
            data["base_sha"] = fixture.target.sha
            path.write_bytes(encoded(data))
        elif surface == "target-head":
            (fixture.repo / ".git/refs/heads/main").write_text(fixture.reviewed + "\n", encoding="ascii")
        elif surface == "target-refs":
            fixture.target.ref("artifacts", fixture.source.artifacts_sha)
        elif surface == "review-refs":
            fixture.set_ref("refs/heads/aios/review-decision/RUN-006-001", fixture.reviewed)
        else:
            fixture.edited_at = "2026-01-02T00:00:00Z"
    fixture.http_hook = mutate
    witness = fixture.observe()
    assert_unknown(witness)
    assert witness.authentication == "NOT_ESTABLISHED"


def test_copied_raw_path_parsed_source_and_structural_validity_never_authenticate(applicable):
    fixture = applicable
    parsed = original_proof_data(fixture.source_proof)
    with pytest.raises(VerificationContractError):
        runtime.observe_proof_applicability(fixture.repo, expected=parsed)
    value = fixture.observe()
    assert parse_applicability(format_applicability(value)).validity == "VALID"
    copied = fixture.source.raw_path.with_name("copied.raw")
    copied.write_bytes(fixture.source.raw)
    fixture.source.item["raw"]["path"] = str(copied)
    fixture.source.publish()
    assert_unknown(fixture.observe(replace(fixture.request, source=fixture.source.expected)))


@pytest.mark.parametrize("defect", ("extra", "duplicate", "bool-version", "missing-dimension", "reordered", "satisfactory", "target-execution", "reuse", "outcome", "bad-fact", "unknown-affirmative"))
def test_closed_schema_and_reduction_reject_promotions(applicable, defect):
    data = applicability_data(applicable.observe())
    if defect == "duplicate":
        content = encoded(data).replace(b'"version":1', b'"version":1,"version":1', 1)
    else:
        if defect == "extra":
            data["reviewed"] = True
        elif defect == "bool-version":
            data["version"] = True
        elif defect == "missing-dimension":
            data["dimensions"].pop()
        elif defect == "reordered":
            data["dimensions"].reverse()
        elif defect == "satisfactory":
            data["satisfactory"] = True
        elif defect == "target-execution":
            data["target_execution"] = "EXECUTED"
        elif defect == "reuse":
            data["reuse"] = "AUTHORIZED"
        elif defect == "outcome":
            data["source_outcome"] = "FAIL"
        elif defect == "bad-fact":
            data["dimensions"][0]["source"]["value_json"] = "{}"
        else:
            data["authentication"] = "NOT_ESTABLISHED"
        content = encoded(data)
    with pytest.raises(VerificationContractError):
        parse_applicability(content)


def test_read_surface_has_no_execution_fetch_state_or_dispatch_calls():
    tree = ast.parse(inspect.getsource(runtime))
    functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    selected = [node for name, node in functions.items() if name.startswith("_app_") or name in {"observe_proof_applicability", "_ApplicabilityReader"}]
    forbidden = {"execute_verification", "execute_minimum_verification", "materialize_verification_subject",
                 "derive_minimum_verification", "subprocess.run", "Popen", "write_text", "write_bytes", "fetch",
                 "select", "dispatch", "admit", "publish", "acquire", "retry"}
    calls = {node.func.id if isinstance(node.func, ast.Name) else node.func.attr
             for function in selected for node in ast.walk(function) if isinstance(node, ast.Call)
             and isinstance(node.func, (ast.Name, ast.Attribute))}
    assert not calls.intersection(forbidden)
    assert set(inspect.signature(runtime.observe_proof_applicability).parameters) == {"repository", "expected"}
