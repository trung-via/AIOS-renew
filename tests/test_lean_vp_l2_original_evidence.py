"""L2-01 source authentication with real Git objects and isolated Runtime bytes.

Fixtures create source records; no mock can confer canonical source authority.
Runtime owns execution of these regressions and issuance of EVIDENCE.
"""

import copy
import hashlib
import json
import os
from dataclasses import FrozenInstanceError, replace

import pytest
import yaml

import aios_renew.verification as runtime
from aios_renew.artifacts import validate_evidence
from aios_renew.review_transport import public_result, public_run
from aios_renew.verification_contract import (
    ORIGINAL_PROOF_DIMENSIONS, OriginalEvidenceRequest, VerificationContractError,
    format_original_proof, original_proof_data, parse_original_proof, verification_digest,
    pytest_collection_identity,
)
from tests.git_fixture_support import (
    materialize_git_baseline, _copy_loose_objects, _write_object, _write_tree,
)
from tests.test_artifacts import TASK_SOURCE
from tests.test_verification_contract import v2_observation, v2_binding


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


class SourceFixture:
    def __init__(self, root, *, v2=False, code=0, incomplete=False):
        self.v2 = v2
        self.task = yaml.safe_load(TASK_SOURCE)
        self.task["verification"]["required"] = ["pytest tests/test_artifacts.py"]
        if v2:
            self.task["verification"]["policy"] = "minimum-sufficient-v2"
        observer_bytes = b"# isolated original observer fixture\n"
        self.observer_sha = hashlib.sha1(f"blob {len(observer_bytes)}\0".encode("ascii") + observer_bytes).hexdigest()
        self.repo, self.remote, self.sha = materialize_git_baseline(
            root, files={".ai/tasks/TASK-005.yaml": yaml.safe_dump(self.task),
                         "tests/bp_v4_probe_plugin.py": observer_bytes},
            user_name="L2 fixture", user_email="l2@example.invalid", commit_message="original source")
        self.objects = self.repo / ".git/objects"
        # Read the actual object tree, rather than substituting an invented SHA.
        import zlib
        commit = zlib.decompress((self.objects / self.sha[:2] / self.sha[2:]).read_bytes())
        self.tree = commit.split(b"\0", 1)[1].splitlines()[0][5:].decode("ascii")
        self.state = self.repo / ".git/aios"
        self.run_id = "RUN-005-001"
        self.evidence_id = self.run_id + "-V001"
        self.raw_path = self.state / "verification" / self.run_id
        if v2:
            self.raw_path /= "candidate"
        self.raw_path /= self.evidence_id + ".raw"
        self.raw_path.parent.mkdir(parents=True)
        self.raw = b"STDOUT\noriginal bounded capture\nSTDERR\n"
        self.raw_path.write_bytes(self.raw)
        self.run = dict(run_id=self.run_id, task=dict(id="TASK-005", revision=1), executor="codex",
                        base_sha=self.sha, workspace=str(self.repo), head_sha=None, status="ACTIVE")
        self.item = dict(evidence_id=self.evidence_id, run_id=self.run_id, subject_sha=self.sha,
                         type="VERIFICATION", source=dict(command="pytest tests/test_artifacts.py"),
                         result=dict(exit_code=code, summary="original command observation"),
                         raw=dict(path=str(self.raw_path)))
        if v2:
            candidate = v2_observation(self.sha, outcome="FAIL" if code == 1 else "PASS",
                                       command=self.item["source"]["command"])
            candidate.update(exit_code=code)
            if incomplete:
                candidate.update(complete=False, unstable=True)
            binding = v2_binding(self.sha)
            binding.update(base_sha=self.sha, tree_sha=self.tree, command=candidate["command"])
            self.item["verification"] = dict(policy="minimum-sufficient-v2", disposition="EXECUTED",
                binding=binding, candidate=candidate, candidate_digest=verification_digest(candidate),
                evidence_id=self.evidence_id, run_id=self.run_id, raw_path=str(self.raw_path),
                raw_digest=hashlib.sha256(self.raw).hexdigest())
        self.package = dict(result=dict(head_sha=self.sha, claims=[], changed_files=[], unresolved=[]),
                            evidence=[self.item])
        self.publish()

    def publish(self):
        for directory, value in (("runs", self.run), ("results", self.package)):
            path = self.state / directory / (self.run_id + ".json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encoded(value))
        files = {"run.json": public_run(encoded(self.run)), "result.json": public_result(encoded(self.package))}
        blobs = {name: bytes.fromhex(_write_object(self.objects, "blob", content)) for name, content in files.items()}
        tree = _write_tree(self.objects, {".ai": {"transport": blobs}})
        self.artifacts_sha = _write_object(self.objects, "commit",
            (f"tree {tree}\nauthor Fixture <f@example.invalid> 946684800 +0000\n"
             "committer Fixture <f@example.invalid> 946684800 +0000\n\nOriginal artifacts\n").encode("ascii"))
        _copy_loose_objects(self.objects, self.remote / "objects")
        self.ref("review", self.sha)
        self.ref("artifacts", self.artifacts_sha)
        self.expected = OriginalEvidenceRequest("TASK-005", 1, self.run_id, self.sha, self.tree,
            self.artifacts_sha, self.evidence_id, self.sha, self.item["source"]["command"], self.item["result"]["exit_code"])

    def ref(self, kind, sha):
        path = self.remote / "refs/heads/aios" / kind / self.run_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sha + "\n", encoding="ascii")

    def observe(self, expected=None):
        return runtime.observe_original_evidence(self.repo, expected=expected or self.expected)


@pytest.fixture
def source(tmp_path):
    return SourceFixture(tmp_path / "original")


def dimensions(provenance):
    return {d["name"]: d for d in original_proof_data(provenance)["dimensions"]}


def blocked(observation):
    assert observation.status == "NOT_ESTABLISHED"
    assert observation.disposition == "BLOCK"
    assert observation.provenance is None
    assert observation.reason


def snapshot(path):
    return {p.relative_to(path).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in path.rglob("*") if p.is_file()}


def test_original_v1_is_exact_immutable_and_read_only(source):
    before = snapshot(source.repo), snapshot(source.remote)
    observation = source.observe()
    assert observation.status == "AUTHENTICATED", observation.reason
    proof = observation.provenance
    assert proof.identity == source.expected
    assert proof.raw_sha256 == hashlib.sha256(source.raw).hexdigest()
    assert proof.raw_size == len(source.raw) and proof.outcome == "PASS"
    assert proof.task_sha256 == hashlib.sha256(yaml.safe_dump(source.task).encode()).hexdigest()
    assert proof.local_result_sha256 == hashlib.sha256(encoded(source.package)).hexdigest()
    assert proof.upstream_sha256 == hashlib.sha256(runtime._original_remote(source.repo).encode("utf-8")).hexdigest()
    assert proof.evidence_sha256 == hashlib.sha256(encoded(source.item)).hexdigest()
    assert parse_original_proof(format_original_proof(proof)) == proof
    assert source.observe().provenance == proof
    assert tuple(d.name for d in proof.dimensions) == ORIGINAL_PROOF_DIMENSIONS
    for name, dimension in dimensions(proof).items():
        if name != "evidence_kind_schema":
            assert dimension["status"] == "UNKNOWN", name
            assert dimension["value"] is None and dimension["reason"] == "NOT_OBSERVED"
    with pytest.raises(FrozenInstanceError):
        proof.raw_sha256 = "0" * 64
    data = original_proof_data(proof)
    data["identity"]["command"] = "forged"
    assert proof.identity.command == source.expected.command
    assert (snapshot(source.repo), snapshot(source.remote)) == before
    assert not any(key in original_proof_data(proof) for key in
                   ("target", "applicability", "valid", "permission", "semantic_adequacy", "production_eligibility"))


def test_v2_retains_exact_recorded_conditions_and_unknown_footprints(tmp_path):
    source = SourceFixture(tmp_path / "v2", v2=True)
    proof = source.observe().provenance
    assert proof is not None
    facts = dimensions(proof)
    candidate = source.item["verification"]["candidate"]
    for dimension, field in (("toolchain", "toolchain"), ("execution_profile", "profile"),
                             ("phase_outcomes", "reports"), ("completeness", "complete"), ("stability", "unstable")):
        assert facts[dimension]["value"] == candidate[field]
    assert facts["prior_raw_digest"]["value"] == proof.raw_sha256
    for name in ("environment_digest", "environment_content", "worker_topology", "worker_state", "collection",
                 "configuration", "fixtures_helpers_shared_state", "ordering", "repetition", "test_dependencies",
                 "concurrency_interference", "integration_external_boundaries"):
        assert facts[name]["status"] == "UNKNOWN", name
    assert parse_original_proof(format_original_proof(proof)) == proof


def test_available_recorded_v2_profile_collection_and_environment_are_exact(tmp_path):
    source = SourceFixture(tmp_path / "conditions", v2=True)
    record = source.item["verification"]
    candidate = record["candidate"]
    profile = candidate["profile"]
    # These are source fixture records, not values read from the current host.
    profile.update(distribution="load", max_worker_restart=0, collect_only=False,
                   environment_digest=verification_digest({"SOURCE_SETTING": "original"}),
                   observer_blob=source.observer_sha, repository_tool_blobs={"pyproject.toml": None})
    record["binding"]["profile"] = copy.deepcopy(profile)
    nodes = [r["nodeid"] for r in candidate["reports"]]
    candidate["collection"] = dict(nodeids=nodes, identity=pytest_collection_identity(nodes))
    record["candidate_digest"] = verification_digest(candidate)
    source.publish()
    observation = source.observe()
    assert observation.status == "AUTHENTICATED", observation.reason
    facts = dimensions(observation.provenance)
    for name, field in (("environment_digest", "environment_digest"), ("observer", "observer_blob"),
                        ("configuration", "repository_tool_blobs")):
        assert facts[name]["value"] == profile[field]
    assert facts["collection"]["value"] == candidate["collection"]
    assert facts["worker_topology"]["value"]["workers"] == 12
    assert facts["environment_content"]["status"] == "UNKNOWN"


def test_incomplete_observed_toolchain_conflict_is_retained_without_planned_backfill(tmp_path):
    source = SourceFixture(tmp_path / "conflict", v2=True, incomplete=True)
    record = source.item["verification"]
    actual = {"python_implementation": "recorded", "python_version": "old",
              "python_executable": "source-interpreter", "conflict": "original observation"}
    record["candidate"].update(observed_toolchain=actual, errors=["toolchain conflict"])
    record["candidate_digest"] = verification_digest(record["candidate"])
    source.publish()
    observation = source.observe()
    assert observation.status == "AUTHENTICATED", observation.reason
    facts = dimensions(observation.provenance)
    assert observation.provenance.outcome == "UNKNOWN"
    assert facts["toolchain"]["value"] == actual
    assert facts["toolchain"]["source"].endswith(".observed_toolchain")
    assert facts["condition_diagnostics"]["value"]["errors"] == ["toolchain conflict"]
    assert facts["execution_profile"]["status"] == "UNKNOWN"


@pytest.mark.parametrize("key", ["observer_blob", "selected_policy_blob", "repository_tool_blobs", "environment_digest"])
def test_matching_looking_context_labels_do_not_replace_exact_source_facts(tmp_path, key):
    source = SourceFixture(tmp_path / "labels", v2=True)
    record = source.item["verification"]
    profile = record["candidate"]["profile"]
    profile[key] = {"pyproject.toml": source.sha} if key == "repository_tool_blobs" else "0" * 40
    record["binding"]["profile"] = copy.deepcopy(profile)
    record["candidate_digest"] = verification_digest(record["candidate"])
    source.publish()
    blocked(source.observe())


@pytest.mark.parametrize("code,incomplete,outcome", [(1, False, "FAIL"), (2, False, "ERROR"),
                                                    (0, True, "UNKNOWN"), (1, True, "FAIL")])
def test_outcome_never_promoted_by_good_provenance(tmp_path, code, incomplete, outcome):
    # This exercises recorded observation truth, not canonical RESULT eligibility.
    # L2-01 does not run the target proof/pass or package completion gate.
    source = SourceFixture(tmp_path / "outcome", v2=True, code=code, incomplete=incomplete)
    observation = source.observe()
    assert observation.status == "AUTHENTICATED", observation.reason
    proof = observation.provenance
    assert proof.outcome == outcome and proof.identity.exit_code == code
    with pytest.raises(VerificationContractError):
        replace(proof, outcome="PASS")
    if incomplete:
        assert dimensions(proof)["toolchain"]["status"] == "UNKNOWN"
        assert dimensions(proof)["execution_profile"]["status"] == "UNKNOWN"


@pytest.mark.parametrize("field,value", [("candidate_sha", "1" * 40), ("tree_sha", "2" * 40),
    ("artifacts_sha", "3" * 40), ("task_revision", 2), ("run_id", "RUN-005-002"),
    ("evidence_id", "RUN-005-001-V002"), ("command", "forged command"), ("exit_code", 1)])
def test_forged_wrong_and_replayed_expected_identities_block(source, field, value):
    changes = {field: value}
    if field == "candidate_sha":
        changes["subject_sha"] = value
    if field == "run_id":
        changes["evidence_id"] = value + "-V001"
    blocked(source.observe(replace(source.expected, **changes)))


@pytest.mark.parametrize("kind", ["failure", "failure-artifacts", "repair"])
def test_competing_source_refs_are_not_ignored(source, kind):
    source.ref(kind, source.sha)
    blocked(source.observe())


def test_stale_source_artifacts_and_unpublished_local_conflicts_block(source):
    original = source.expected
    source.item["result"]["summary"] = "different original record"
    source.publish()
    blocked(source.observe(original))
    source.item["result"]["summary"] = "unpublished local substitution"
    (source.state / "results" / (source.run_id + ".json")).write_bytes(encoded(source.package))
    blocked(source.observe())


@pytest.mark.parametrize("mutation", ["run", "task", "subject", "command", "outcome", "evidence_id", "extra"])
def test_local_canonical_record_conflicts_block(source, mutation):
    original = source.expected
    if mutation == "task":
        source.run["task"]["revision"] = 2
    elif mutation == "run":
        source.item["run_id"] = "RUN-005-002"
    elif mutation == "subject":
        source.item["subject_sha"] = "4" * 40
    elif mutation == "command":
        source.item["source"]["command"] = "other command"
    elif mutation == "outcome":
        source.item["result"]["exit_code"] = 1
    elif mutation == "extra":
        source.item["claimed_context"] = {"environment": "preserved"}
    else:
        source.item["evidence_id"] = "RUN-005-002-V001"
    # Even republishing copied labels as actual Git objects cannot bypass the
    # independently supplied exact original identities/schema.
    source.publish()
    blocked(source.observe(replace(original, artifacts_sha=source.artifacts_sha)))


@pytest.mark.parametrize("mutation", ["raw_bytes", "raw_digest", "record_run", "record_evidence", "binding_tree", "reuse"])
def test_v2_original_raw_and_record_bindings_block(tmp_path, mutation):
    source = SourceFixture(tmp_path / "v2", v2=True)
    record = source.item["verification"]
    if mutation == "raw_bytes":
        source.raw_path.write_bytes(source.raw + b"substituted")
    elif mutation == "raw_digest":
        record["raw_digest"] = "0" * 64
    elif mutation == "record_run":
        record["run_id"] = "RUN-005-002"
    elif mutation == "record_evidence":
        record["evidence_id"] = "RUN-005-001-V002"
    elif mutation == "binding_tree":
        record["binding"]["tree_sha"] = "0" * 40
    else:
        record["reuse"] = dict(binding=copy.deepcopy(record["binding"]), source_evidence_id=source.evidence_id)
    source.publish()
    blocked(source.observe())


@pytest.mark.parametrize("mutation", ["copy", "traversal", "protected", "missing", "symlink", "oversize", "failure", "partial"])
def test_local_paths_missingness_and_size_fail_closed(source, tmp_path, mutation):
    if mutation == "copy":
        path = tmp_path / "matching.raw"
        path.write_bytes(source.raw)
        source.item["raw"]["path"] = str(path)
        source.publish()
    elif mutation == "traversal":
        source.item["raw"]["path"] = str(source.raw_path.parent / ".." / source.raw_path.name)
        source.publish()
    elif mutation == "protected":
        source.item["raw"]["path"] = "protected-runtime-raw/" + source.evidence_id
        source.publish()
    elif mutation == "symlink":
        target = tmp_path / "outside.raw"
        target.write_bytes(source.raw)
        source.raw_path.unlink()
        try:
            source.raw_path.symlink_to(target)
        except OSError:
            pytest.skip("OS does not permit symlink fixture creation")
    elif mutation == "oversize":
        with source.raw_path.open("wb") as stream:
            stream.truncate(runtime.MAX_ORIGINAL_RAW_BYTES + 1)
    elif mutation == "failure":
        path = source.state / "failures" / (source.run_id + ".json")
        path.parent.mkdir()
        path.write_bytes(b"{}")
    elif mutation == "partial":
        path = source.state / "results" / (source.run_id + ".json")
        path.write_bytes(b'{"result":')
    else:
        source.raw_path.unlink()
    blocked(source.observe())


@pytest.mark.parametrize("mutation", ["version", "extra", "missing", "duplicate", "unknown_value", "source", "identity", "bool", "size", "digest"])
def test_projection_strict_schema_and_missingness(source, mutation):
    proof = source.observe().provenance
    data = original_proof_data(proof)
    if mutation == "version":
        data["version"] = 2
    elif mutation == "extra":
        data["valid"] = True
    elif mutation == "missing":
        data["dimensions"].pop()
    elif mutation == "duplicate":
        content = format_original_proof(proof).replace(b'"version":1', b'"version":1,"version":1')
    elif mutation == "unknown_value":
        data["dimensions"][0]["value"] = "preserved"
    elif mutation == "source":
        next(d for d in data["dimensions"] if d["name"] == "evidence_kind_schema")["source"] = "caller assertion"
    elif mutation == "identity":
        data["identity"]["subject_sha"] = "4" * 40
    elif mutation == "bool":
        data["identity"]["exit_code"] = True
    elif mutation == "digest":
        data["raw_sha256"] = "not a digest"
    else:
        data["raw_size"] = runtime.MAX_ORIGINAL_RAW_BYTES + 1
    if mutation != "duplicate":
        content = encoded(data)
    with pytest.raises(VerificationContractError):
        parse_original_proof(content)


def test_no_caller_authority_or_raw_path_override(source):
    for argument in ("raw_path", "state_root", "environment", "runner", "resolver", "remote", "claimed_context", "target_sha"):
        with pytest.raises(TypeError):
            runtime.observe_original_evidence(source.repo, expected=source.expected, **{argument: "forged"})


def test_inaccessible_original_raw_never_falls_back_to_public_reference(source, monkeypatch):
    original = runtime._original_local
    def denied(path, limit):
        if path == source.raw_path:
            raise PermissionError("fixture denies exact raw read")
        return original(path, limit)
    monkeypatch.setattr(runtime, "_original_local", denied)
    blocked(source.observe())


def test_git_symlink_blob_mode_and_local_hardlink_are_rejected(source, tmp_path):
    blob = _write_object(source.objects, "blob", b"../../outside")
    tree = _write_object(source.objects, "tree", b"120000 run.json\0" + bytes.fromhex(blob))
    with pytest.raises(VerificationContractError):
        runtime._original_blob(source.repo, tree, "run.json")
    alias = tmp_path / "hardlinked.raw"
    os.link(source.raw_path, alias)
    blocked(source.observe())


def test_read_race_cannot_return_stale_snapshot(source, monkeypatch):
    original = runtime._original_refs
    calls = []
    def observe_refs(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            source.ref("review", source.artifacts_sha)
        return original(*args, **kwargs)  # Actual refs, no fabricated authority.
    monkeypatch.setattr(runtime, "_original_refs", observe_refs)
    blocked(source.observe())


def test_real_historical_minimal_v1_shape_remains_unknown():
    # Exact public EVIDENCE from published TASK-383 r2 RUN-383-004; a redacted
    # public reference is deliberately unavailable as original local raw bytes.
    historical = {"evidence_id": "RUN-383-004-V001", "run_id": "RUN-383-004",
        "subject_sha": "606476c84a05b2d852553d1638e178430e53ce56", "type": "VERIFICATION",
        "source": {"command": "python -m pytest -q tests/test_lean_vp_l1_integrated_readiness.py tests/test_lean_vp_l1_real_mapping.py tests/test_lean_vp_l1_human_selection.py"},
        "result": {"exit_code": 0, "summary": "Runtime verification exit code 0"},
        "raw": {"path": "protected-runtime-raw/RUN-383-004-V001"}}
    assert validate_evidence(historical).verification is None
    facts = runtime._original_dimensions({}, historical)
    assert all(d.status == "UNKNOWN" for d in facts if d.name != "evidence_kind_schema")
