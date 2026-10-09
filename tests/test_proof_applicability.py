"""Offline VP-03A cases with disposable real Git provenance; no proof execution."""

import copy
import hashlib
import json
import os
import subprocess
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path

import pytest

import aios_renew.proof_applicability as module
from aios_renew.proof_applicability import (
    CATALOG_SCHEMA, CONTEXT_SCHEMA, DIMENSIONS, REVIEW_SCHEMA, SCHEMA, WITNESS_SCHEMA,
    ApplicabilityInputError, ReadOnlyAuthority, ReasonCode, State,
    canonical_digest, decode_record_identity, decode_request, evaluate_applicability,
)
from aios_renew.proof_coverage_contract import (
    CONTRACT_SCHEMA, EXECUTION_DEFAULT, MAPPING_SCHEMA, proof_mapping_digest,
    validate_proof_contract,
)
from aios_renew.verification_contract import MINIMUM_SUFFICIENT_V2


def _plain(value):
    return json.loads(json.dumps(value))


def _write(repo, path, value):
    destination = repo / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = value if isinstance(value, bytes) else json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    destination.write_bytes(raw)


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="Offline", GIT_AUTHOR_EMAIL="offline@example.invalid",
        GIT_COMMITTER_NAME="Offline", GIT_COMMITTER_EMAIL="offline@example.invalid",
        GIT_AUTHOR_DATE="2026-01-01T00:00:00+00:00", GIT_COMMITTER_DATE="2026-01-01T00:00:00+00:00",
        GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    return subprocess.run(["git", "--no-replace-objects", "-C", str(repo),
        "-c", "core.hooksPath=.git/offline-no-hooks", *args], env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=True, timeout=5).stdout


def _commit(repo, message):
    _git(repo, "add", "--all")
    _git(repo, "commit", "--allow-empty", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").decode().strip()


def _record(repo, commit, path):
    # Fixtures call this immediately after committing these exact local bytes;
    # the evaluator independently rereads and authenticates the committed blob.
    raw = (repo / path).read_bytes()
    return {"commit_sha": commit, "path": path,
        "blob_sha": _git(repo, "rev-parse", f"{commit}:{path}").decode().strip(),
        "sha256": hashlib.sha256(raw).hexdigest()}


def _pin(value):
    return {"id": value.id, "revision": value.revision, "digest": value.digest}


def _mapping(contract):
    parsed = validate_proof_contract(contract, expected_task=contract["task"])
    proofs, entries = [], []
    for obligation in parsed.obligations:
        identity = "proof-" + obligation.id
        proofs.append({"id": identity, "kind": obligation.kind,
            "claims": [_plain(asdict(obligation.claim))],
            "conditions": _plain(asdict(obligation.conditions)),
            "provenance": _plain(asdict(obligation.provenance))})
        entries.append({"obligation_id": obligation.id, "obligation_revision": obligation.revision,
            "obligation_digest": obligation.digest, "proof_id": identity})
    mapping = {"schema": MAPPING_SCHEMA, "id": "mapping-v1", "revision": 1,
        "contract_id": parsed.id, "contract_revision": parsed.revision,
        "contract_digest": parsed.digest, "provenance": contract["provenance"],
        "proofs": proofs, "entries": entries}
    return parsed, mapping


def _tree_entries(repo, subject):
    entries = {}
    for entry in _git(repo, "ls-tree", "-r", "-z", subject, "--", "dep").split(b"\0"):
        if entry:
            metadata, path = entry.split(b"\t", 1)
            mode, kind, sha = metadata.split()
            assert kind == b"blob"
            entries[path.decode()] = [mode.decode(), sha.decode()]
    return entries


def _fingerprint(entries, context, paths):
    # Independent Git plumbing supplies the reviewed dependency objects. Paths
    # are fixture-defined complete semantic closures, not filename heuristics.
    objects = []
    for path in sorted(paths):
        objects.append([path, entries.get(path)])
    return canonical_digest({"conditions": context["conditions"], "facts": context["facts"], "objects": objects})


def _case(tmp_path, *, change=None, fact_change=None, outcome="PASS", selected="unit",
          witness_defect=None, review_defect=None, evidence_defect=None, mapping_defect=None,
          sibling=False, source_worker_mismatch=False, inapplicable=None):
    repo = tmp_path / "repository"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    task = {"task_id": "TASK-OFFLINE", "revision": 1,
        "goal": "Preserve the explicitly reviewed proof behavior.",
        "problem": "An offline fixture exercises immutable applicability.",
        "assumptions": ["Offline disposable Git data only."],
        "scope": {"inspect": ["dep/test_code.dat"], "modify": ["app.dat"]},
        "non_goals": ["Execute production verification."],
        "constraints": {"hard": ["Keep all provenance immutable."]},
        "acceptance": [{"id": "AC1", "condition": "The required behavior holds."}],
        "verification": {"policy": MINIMUM_SUFFICIENT_V2,
                         "required": ["python -m pytest -q tests/offline.py"]},
        "return_affinity": {"kind": "LEGACY_REPOSITORY_DEFAULT_ROUTE"}}
    _write(repo, "records/task.json", task)
    for dimension in DIMENSIONS:
        _write(repo, f"dep/{dimension}.dat", dimension.encode())
    _write(repo, "app.dat", b"base")
    base = _commit(repo, "task and base")
    task_pin = _record(repo, base, "records/task.json")
    _git(repo, "checkout", "--detach", "-q", base)
    _write(repo, "app.dat", b"observed source")
    original = _commit(repo, "original candidate")
    if change:
        _write(repo, f"dep/{change}.dat", b"authenticated relevant change")
    else:
        _write(repo, "unrelated.dat", b"outside independently reviewed closure")
    target = _commit(repo, "target candidate")
    if sibling:
        tree = _git(repo, "rev-parse", target + "^{tree}").decode().strip()
        target = _git(repo, "commit-tree", tree, "-p", base, "-m", "conflicting sibling").decode().strip()
        _git(repo, "checkout", "--detach", "-q", target)
    tree = _git(repo, "rev-parse", original + "^{tree}").decode().strip()
    target_tree = _git(repo, "rev-parse", target + "^{tree}").decode().strip()
    conditions = {"candidate_sha": original, "base_sha": None,
        "population_ref": "population-v1", "population_size": 1, "test_ref": "test-v1",
        "fixture_refs": ["fixtures-v1"], "shared_state_ref": "helpers-state-v1",
        "profile_ref": "profile-v1", "worker_mode": "serial", "workers": 1,
        "concurrency_ref": "isolated-v1", "ordering_ref": "ordered-v1",
        "integration_ref": "narrow-v1", "repetition_ref": "once-v1", "toolchain_ref": "tools-v1",
        "environment_ref": "env-v1", "evidence_kind": "VERIFICATION",
        "evidence_schema": "evidence-v1", "collection_ref": "collection-v1"}
    provenance = {"authority_ref": "authority-v1", "review_ref": "review-v1", "source_ref": "source-v1"}
    task_binding = {"task_id": task["task_id"], "revision": 1,
        "envelope_digest": canonical_digest(task), "acceptance_ids": ["AC1"]}
    obligation = {"id": "unit", "revision": 1,
        "claim": {"id": "behavior-v1", "text": "The required behavior holds under declared conditions."},
        "acceptance_ids": ["AC1"], "kind": "candidate", "blocking": True,
        "conditions": conditions, "provenance": provenance}
    contract = {"schema": CONTRACT_SCHEMA, "id": "contract-v1", "revision": 1,
        "execution_default": EXECUTION_DEFAULT, "task": task_binding,
        "provenance": provenance, "obligations": [obligation]}
    left, left_map = _mapping(contract)
    target_contract = copy.deepcopy(contract)
    target_contract["obligations"][0]["conditions"]["candidate_sha"] = target
    # All remain separate mandatory obligations, even though they share AC1.
    for identity, changes in (
        ("integration", {"integration_ref": "whole-suite-v1"}),
        ("concurrency", {"worker_mode": "parallel", "workers": 2, "concurrency_ref": "shared-v1"}),
        ("ordering", {"ordering_ref": "opposite-v1"}),
        ("comparison", {"base_sha": base}),
    ):
        extra = copy.deepcopy(target_contract["obligations"][0])
        extra["id"] = identity
        extra["conditions"].update(changes)
        if identity == "comparison":
            extra["kind"] = "comparison"
        target_contract["obligations"].append(extra)
    right, right_map = _mapping(target_contract)
    right_map_pin = {"id": right_map["id"], "revision": 1, "digest": proof_mapping_digest(right_map)}
    target_obligation = next(o for o in right.obligations if o.id == selected)
    target_proof = next(p for p in right_map["proofs"] if p["id"] == "proof-" + selected)
    if mapping_defect == "missing":
        right_map["entries"].pop()
    elif mapping_defect == "conflict":
        right_map["entries"].append(copy.deepcopy(right_map["entries"][0]))
    profile = {"worker_mode": "serial", "workers": 1, "policy": MINIMUM_SUFFICIENT_V2}
    if source_worker_mismatch:
        profile["workers"] = 2
    tools = {"python_implementation": "CPython", "python_version": "offline-v1",
        "python_executable": "offline-python", "platform_system": "offline-system",
        "platform_machine": "offline-machine", "pytest_version": "offline-v1",
        "pytest_xdist_version": "offline-v1"}
    reports = [{"nodeid": "tests/offline.py::test_behavior", "phase": phase,
                "outcome": outcome if phase == "call" else "PASS"}
               for phase in ("setup", "call", "teardown")]
    if outcome == "FAIL":
        reports[1].update(detail="Original observed assertion failed.",
            fingerprint=canonical_digest("Original observed assertion failed."), profile=profile, toolchain=tools)
    observation = {"subject_sha": original, "command": "python -m pytest -q tests/offline.py",
        "profile": profile, "toolchain": tools, "exit_code": 0 if outcome == "PASS" else 1,
        "complete": True, "unstable": False, "failure_count": 0 if outcome == "PASS" else 1,
        "reports": reports}
    evidence = {"evidence_id": "EVIDENCE-OFFLINE", "run_id": "RUN-OFFLINE", "subject_sha": original,
        "type": "VERIFICATION", "source": {"command": observation["command"]},
        "result": {"exit_code": observation["exit_code"], "summary": "Original offline observation."},
        "raw": {"path": "records/raw.log"}, "verification": {"policy": MINIMUM_SUFFICIENT_V2,
            "evidence_id": "EVIDENCE-OFFLINE", "candidate": observation,
            "candidate_digest": canonical_digest(observation), "binding": {
                "subject_sha": original, "base_sha": base, "tree_sha": tree,
                "envelope_digest": canonical_digest(task), "changed_files_digest": canonical_digest(["app.dat"]),
                "failure_set_digest": canonical_digest([]), "command": observation["command"],
                "profile": profile, "toolchain": tools}}}
    if evidence_defect == "digest":
        evidence["verification"]["candidate_digest"] = "0" * 64
    elif evidence_defect == "subject":
        evidence["subject_sha"] = target
    elif evidence_defect == "incomplete":
        observation["complete"] = False
        evidence["verification"]["candidate_digest"] = canonical_digest(observation)
    elif evidence_defect == "unstable":
        observation["unstable"] = True
        evidence["verification"]["candidate_digest"] = canonical_digest(observation)
    elif evidence_defect == "reuse":
        evidence["verification"]["reuse"] = {"binding": copy.deepcopy(evidence["verification"]["binding"]),
                                               "source_evidence_id": "EVIDENCE-OFFLINE"}
    run = {"run_id": "RUN-OFFLINE", "task": {"id": task["task_id"], "revision": 1},
        "executor": "codex", "base_sha": base, "workspace": "OFFLINE-DISPOSABLE",
        "head_sha": original, "status": "ACTIVE", "return_affinity": task["return_affinity"]}
    result = {"head_sha": original, "claims": [{"id": "claim-v1", "satisfies": ["AC1"],
        "claim": "An original observation was recorded.", "evidence": ["EVIDENCE-OFFLINE"]}],
        "changed_files": ["app.dat"], "unresolved": []}
    values = {"run": run, "result": result, "evidence": evidence,
        "source_contract": contract, "source_mapping": left_map,
        "target_contract": target_contract, "target_mapping": right_map}
    for name, data in values.items():
        _write(repo, f"records/{name}.json", data)
    _write(repo, "records/raw.log", b"Immutable original subject/outcome log.\n")
    artifact_commit = _commit(repo, "canonical source snapshots and reviewed coverage")
    records = {name: _record(repo, artifact_commit, f"records/{name}.json") for name in values}
    records.update(task=task_pin, raw=_record(repo, artifact_commit, "records/raw.log"))
    source_descriptor = left_map["proofs"][0]
    bindings = {"task": task_pin, "source_contract": _pin(left),
        "source_mapping": {"id": left_map["id"], "revision": 1, "digest": proof_mapping_digest(left_map)},
        "target_contract": _pin(right),
        "target_mapping": right_map_pin,
        "source_proof_id": "proof-unit", "source_proof_digest": canonical_digest(source_descriptor),
        "target_obligation": _pin(target_obligation), "target_proof_id": target_proof["id"]}
    plans, comparisons, contexts = [], [], {}
    source_entries, target_entries = _tree_entries(repo, original), _tree_entries(repo, target)
    for dimension in DIMENSIONS:
        paths = [f"dep/{dimension}.dat"]
        if dimension == inapplicable:
            paths = []
        if review_defect == "footprint" and dimension == "helpers":
            paths = ["dep/unobserved.dat"]
        role_contexts = []
        for role, subject, descriptor in (("source", original, source_descriptor), ("target", target, target_proof)):
            facts = {"state": dimension + "-v1"}
            if dimension == inapplicable:
                facts["inapplicable"] = True
            if dimension == "profile":
                facts["profile"] = copy.deepcopy(profile)
            elif dimension == "worker_mode":
                facts["profile"] = copy.deepcopy(profile)
            elif dimension == "toolchain":
                facts["toolchain"] = copy.deepcopy(tools)
            if role == "target" and dimension == fact_change:
                facts["state"] = "authenticated-v2"
            context = {"schema": CONTEXT_SCHEMA, "dimension": dimension, "subject_sha": subject,
                "proof_id": descriptor["id"], "evidence_digest": records["evidence"]["sha256"] if role == "source" else None,
                "conditions": {key: descriptor["conditions"][key] for key in module._FIELDS[dimension]}, "facts": facts}
            contexts[f"contexts/{role}-{dimension}.json"] = context
            role_contexts.append(context)
        plans.append({"dimension": dimension, "coverage": "INAPPLICABLE" if dimension == inapplicable else "COMPLETE",
                      "basis": "reviewed-complete-closure-v1",
                      "paths": paths, "source_context": None, "target_context": None})
        comparisons.append({"dimension": dimension,
            "source_digest": _fingerprint(source_entries, role_contexts[0], paths),
            "target_digest": _fingerprint(target_entries, role_contexts[1], paths)})
    for path, context in contexts.items():
        _write(repo, path, context)
    context_commit = _commit(repo, "independent immutable dependency facts")
    for plan in plans:
        for role in ("source", "target"):
            plan[role + "_context"] = _record(repo, context_commit, f"contexts/{role}-{plan['dimension']}.json")
    if review_defect == "missing":
        plans.pop()
    elif review_defect == "unknown":
        plans[0]["coverage"] = "UNKNOWN"
    review = {"schema": REVIEW_SCHEMA, **bindings, "dimensions": plans,
        "source_observation": {"evidence_digest": records["evidence"]["sha256"], "complete": True,
            "stable": True, "conflicting": review_defect == "conflicting", "observed_items": 1, "outcome": outcome}}
    _write(repo, "records/review.json", review)
    review_commit = _commit(repo, "independently reviewed mapping and footprint")
    records["review"] = _record(repo, review_commit, "records/review.json")
    witness = {"schema": WITNESS_SCHEMA, "repository_id": "offline-repository-v1", **bindings,
        **{name: records[name] for name in ("run", "result", "evidence", "raw", "review")},
        "source_candidate_sha": original, "source_tree_sha": tree, "target_candidate_sha": target,
        "target_tree_sha": target_tree, "base_sha": base, "dimensions": comparisons}
    if witness_defect == "digest":
        witness["dimensions"][0]["source_digest"] = "0" * 64
    elif witness_defect == "missing":
        witness["dimensions"].pop()
    elif witness_defect == "mapping":
        witness["target_mapping"]["digest"] = "0" * 64
    elif witness_defect == "subject":
        witness["source_candidate_sha"] = target
    elif witness_defect == "task":
        witness["task"] = dict(task_pin, sha256="0" * 64)
    _write(repo, "records/witness.json", witness)
    witness_commit = _commit(repo, "separate applicability witness")
    records["witness"] = _record(repo, witness_commit, "records/witness.json")
    catalog = {"schema": CATALOG_SCHEMA, "repository_id": "offline-repository-v1", "task_binding": task_binding,
        "expected_main_sha": base, "base_sha": base, "source_candidate_sha": original,
        "target_candidate_sha": target, "source_proof_id": "proof-unit",
        "target_obligation": _pin(target_obligation), **records}
    _write(repo, "records/catalog.json", catalog)
    catalog_commit = _commit(repo, "out-of-band canonical authority catalog")
    authority = ReadOnlyAuthority(repo, decode_record_identity(_record(repo, catalog_commit, "records/catalog.json")))
    request = decode_request({"schema": SCHEMA, "repository_id": catalog["repository_id"],
        "evidence": records["evidence"], "witness": records["witness"], "source_proof_id": "proof-unit",
        "target_candidate_sha": target, "target_obligation": _pin(target_obligation)})
    return authority, request, catalog


def _codes(result):
    return {reason.code for reason in result.reasons}


def test_valid_is_immutable_idempotent_and_retains_actual_original_subject(tmp_path):
    authority, request, catalog = _case(tmp_path)
    before = _git(authority.repository, "status", "--porcelain")
    result = evaluate_applicability(request, authority=authority)
    assert result.state == State.VALID and result.reasons == ()
    assert result == evaluate_applicability(request, authority=authority)
    assert result.source.subject_sha == catalog["source_candidate_sha"] != request.target_candidate_sha
    assert result.source.evidence == request.evidence and result.source.outcome == "PASS"
    assert result.source.run_id == "RUN-OFFLINE" and result.source.evidence_id == "EVIDENCE-OFFLINE"
    assert result.source.result != result.witness.record
    assert result.witness.review != result.witness.record
    assert result.applicable_obligation == request.target_obligation
    assert len(result.witness.dependency_digests) == len(DIMENSIONS)
    assert result.activation == "NOT_ACTIVATED" and result.authority_authenticated
    assert not any((result.acceptance_discharge_authorized, result.evidence_reuse_authorized,
        result.verification_execution_authorized, result.target_execution_evidence_created,
        result.canonical_checkpoint_created))
    assert before == _git(authority.repository, "status", "--porcelain") == b""
    with pytest.raises(FrozenInstanceError):
        result.source.subject_sha = request.target_candidate_sha


@pytest.mark.parametrize("dimension", DIMENSIONS)
def test_real_relevant_git_dependency_changes_invalidate_without_filename_inference(tmp_path, dimension):
    authority, request, _ = _case(tmp_path, change=dimension)
    result = evaluate_applicability(request, authority=authority)
    assert result.state == State.INVALIDATED and result.applicable_obligation is None
    assert result.reasons == (module.Reason(ReasonCode.DEPENDENCY_CHANGED, dimension),)
    assert result.source.outcome == "PASS" and not result.acceptance_discharge_authorized


@pytest.mark.parametrize("dimension", ("environment", "profile", "toolchain", "worker_mode", "policy", "population"))
def test_authenticated_external_facts_are_independent_invalidation_dimensions(tmp_path, dimension):
    authority, request, _ = _case(tmp_path, fact_change=dimension)
    result = evaluate_applicability(request, authority=authority)
    assert result.state == State.INVALIDATED
    assert result.reasons == (module.Reason(ReasonCode.DEPENDENCY_CHANGED, dimension),)


@pytest.mark.parametrize("defect", ("digest", "missing", "mapping", "subject", "task"))
def test_even_catalog_pinned_forged_or_incomplete_witness_cannot_make_valid(tmp_path, defect):
    authority, request, _ = _case(tmp_path, witness_defect=defect)
    result = evaluate_applicability(request, authority=authority)
    assert result.state == State.UNKNOWN and result.applicable_obligation is None
    assert _codes(result) <= {ReasonCode.WITNESS_CONFLICT, ReasonCode.DEPENDENCY_INCOMPLETE}
    assert not result.acceptance_discharge_authorized


@pytest.mark.parametrize("defect", ("missing", "unknown", "footprint", "conflicting"))
def test_incomplete_review_unknown_footprint_and_conflicting_observation_fail_closed(tmp_path, defect):
    authority, request, _ = _case(tmp_path, review_defect=defect)
    result = evaluate_applicability(request, authority=authority)
    assert result.state == State.UNKNOWN and result.applicable_obligation is None
    assert _codes(result) & {ReasonCode.DEPENDENCY_INCOMPLETE, ReasonCode.FOOTPRINT_UNKNOWN,
                             ReasonCode.SOURCE_PROOF_UNKNOWN}


@pytest.mark.parametrize("defect", ("digest", "subject", "incomplete", "unstable", "reuse"))
def test_corrupt_incomplete_relabelled_or_reused_source_is_not_original_proof(tmp_path, defect):
    authority, request, _ = _case(tmp_path, evidence_defect=defect)
    result = evaluate_applicability(request, authority=authority)
    assert result.state == State.UNKNOWN and result.applicable_obligation is None
    assert not result.target_execution_evidence_created


@pytest.mark.parametrize("defect", ("missing", "conflict"))
def test_actual_missing_and_conflicting_proof_mappings_are_unknown(tmp_path, defect):
    authority, request, _ = _case(tmp_path, mapping_defect=defect)
    result = evaluate_applicability(request, authority=authority)
    assert result.state == State.UNKNOWN and ReasonCode.MAPPING_CONFLICT in _codes(result)


def test_valid_original_failure_remains_failure(tmp_path):
    authority, request, _ = _case(tmp_path, outcome="FAIL")
    result = evaluate_applicability(request, authority=authority)
    assert result.state == State.VALID and result.source.outcome == "FAIL"
    assert result.source.exit_code == 1 and result.applicable_obligation == request.target_obligation
    assert not result.acceptance_discharge_authorized


def test_serial_proof_cannot_use_an_actual_different_worker_profile(tmp_path):
    authority, request, _ = _case(tmp_path, source_worker_mismatch=True)
    result = evaluate_applicability(request, authority=authority)
    assert result.state == State.UNKNOWN
    assert module.Reason(ReasonCode.SOURCE_BINDING_CONFLICT, "worker_mode") in result.reasons


@pytest.mark.parametrize("dimension, expected", (("helpers", State.VALID),
    ("fixtures", State.UNKNOWN), ("integration", State.UNKNOWN)))
def test_reviewed_inapplicability_cannot_erase_real_fixture_or_integration_conditions(tmp_path, dimension, expected):
    authority, request, _ = _case(tmp_path, inapplicable=dimension)
    result = evaluate_applicability(request, authority=authority)
    assert result.state == expected


@pytest.mark.parametrize("obligation", ("integration", "concurrency", "ordering", "comparison"))
def test_narrow_pass_never_collapses_independent_mandatory_obligations(tmp_path, obligation):
    authority, request, _ = _case(tmp_path, selected=obligation)
    result = evaluate_applicability(request, authority=authority)
    assert result.state in (State.INVALIDATED, State.UNKNOWN)
    assert result.applicable_obligation is None and not result.acceptance_discharge_authorized


def test_wrong_source_and_forgeable_digest_are_rejected_against_independent_catalog(tmp_path):
    authority, request, catalog = _case(tmp_path)
    wrong = replace(request, evidence=decode_record_identity(catalog["result"]))
    spoofed = replace(request, evidence=replace(request.evidence, sha256="0" * 64))
    fabricated = replace(request, witness=replace(request.witness, commit_sha="a" * 40))
    for bad in (wrong, spoofed, fabricated):
        result = evaluate_applicability(bad, authority=authority)
        assert result.state == State.UNKNOWN and ReasonCode.SOURCE_BINDING_CONFLICT in _codes(result)


def test_main_movement_and_conflicting_real_parent_lineage_fail_closed(tmp_path):
    authority, request, _ = _case(tmp_path)
    _git(authority.repository, "update-ref", "refs/heads/main", request.target_candidate_sha)
    result = evaluate_applicability(request, authority=authority)
    assert result.state == State.UNKNOWN and ReasonCode.AUTHORITY_NOT_CURRENT in _codes(result)
    other = tmp_path / "sibling"
    other.mkdir()
    authority, request, _ = _case(other, sibling=True)
    result = evaluate_applicability(request, authority=authority)
    assert result.state == State.UNKNOWN and ReasonCode.LINEAGE_CONFLICT in _codes(result)


def test_currentness_is_rechecked_before_returning_applicability(tmp_path, monkeypatch):
    authority, request, _ = _case(tmp_path)
    original = module._Git.current_main
    calls = []
    def moved(git, expected):
        calls.append(expected)
        if len(calls) == 2:
            _git(authority.repository, "update-ref", "refs/heads/main", request.target_candidate_sha)
        return original(git, expected)
    monkeypatch.setattr(module._Git, "current_main", moved)
    result = evaluate_applicability(request, authority=authority)
    assert result.state == State.UNKNOWN and ReasonCode.AUTHORITY_NOT_CURRENT in _codes(result)


def test_missing_objects_untrusted_source_and_root_content_spoofing_are_unknown(tmp_path):
    authority, request, _ = _case(tmp_path)
    assert evaluate_applicability(request, authority=None).state == State.UNKNOWN
    for catalog in (replace(authority.catalog, commit_sha="a" * 40),
                    replace(authority.catalog, sha256="0" * 64),
                    replace(authority.catalog, blob_sha=request.evidence.blob_sha)):
        result = evaluate_applicability(request, authority=replace(authority, catalog=catalog))
        assert result.state == State.UNKNOWN and result.applicable_obligation is None


def test_replace_refs_cannot_rewrite_authenticated_raw_parent_or_object_identity(tmp_path):
    authority, request, catalog = _case(tmp_path)
    _git(authority.repository, "replace", catalog["source_candidate_sha"], catalog["base_sha"])
    result = evaluate_applicability(request, authority=authority)
    assert result.state == State.VALID


def test_bounded_objects_and_git_only_reads_never_invoke_a_scheduler(tmp_path, monkeypatch):
    authority, request, _ = _case(tmp_path)
    calls = []
    original = module._Git._read
    def record(git, args, oid=None):
        calls.append(tuple(args))
        assert args[0] in ("rev-parse", "cat-file")
        return original(git, args, oid)
    monkeypatch.setattr(module._Git, "_read", record)
    assert evaluate_applicability(request, authority=authority).state == State.VALID
    assert len(calls) <= module.MAX_GIT_CALLS
    monkeypatch.setattr(module, "MAX_OBJECTS", 1)
    result = evaluate_applicability(request, authority=authority)
    assert result.state == State.UNKNOWN and ReasonCode.RESOURCE_BOUND in _codes(result)


@pytest.mark.parametrize("defect", ("schema", "ref", "type", "extra", "revision", "path", "digest"))
def test_decoded_identity_boundary_rejects_labels_refs_and_inexact_types(defect):
    record = {"commit_sha": "a" * 40, "path": "records/offline.json", "blob_sha": "b" * 40, "sha256": "c" * 64}
    data = {"schema": SCHEMA, "repository_id": "offline", "evidence": record,
        "witness": record, "source_proof_id": "proof-v1", "target_candidate_sha": "d" * 40,
        "target_obligation": {"id": "unit", "revision": 1, "digest": "e" * 64}}
    if defect == "schema":
        data["schema"] = "proof-applicability-v2"
    elif defect == "ref":
        data["target_candidate_sha"] = "main"
    elif defect == "type":
        data["source_proof_id"] = Path("proof-v1")
    elif defect == "extra":
        data["review"] = "PASS"
    elif defect == "revision":
        data["target_obligation"]["revision"] = True
    elif defect == "path":
        record["path"] = "../outside"
    elif defect == "digest":
        record["sha256"] = "caller-approved"
    with pytest.raises(ApplicabilityInputError):
        decode_request(data)


def test_json_bounds_duplicate_keys_and_absent_independent_authority(tmp_path):
    with pytest.raises(ApplicabilityInputError):
        canonical_digest({"a": [0] * (module.MAX_NODES + 1)})
    value = []
    for _ in range(module.MAX_DEPTH + 2):
        value = [value]
    with pytest.raises(ApplicabilityInputError):
        canonical_digest(value)
    with pytest.raises(ApplicabilityInputError):
        canonical_digest({"tuple": ("asserted",)})
    authority, request, _ = _case(tmp_path)
    _write(authority.repository, "records/duplicate.json", b'{"schema":"v1","schema":"v1"}')
    commit = _commit(authority.repository, "duplicate-key authority")
    bad = replace(authority, catalog=decode_record_identity(_record(authority.repository, commit, "records/duplicate.json")))
    result = evaluate_applicability(request, authority=bad)
    assert result.state == State.UNKNOWN and ReasonCode.RECORD_CORRUPT in _codes(result)


def test_forged_typed_constructor_never_calls_caller_deepcopy_hooks():
    class CallerObject:
        def __deepcopy__(self, memo):
            pytest.fail("untrusted constructor hook executed")
    record = module.RecordIdentity("a" * 40, "records/a.json", "b" * 40, "c" * 64)
    request = module.ApplicabilityRequest(SCHEMA, "offline", record, record, CallerObject(), "d" * 40,
                                         module.ProofPin("unit", 1, "e" * 64))
    with pytest.raises(ApplicabilityInputError):
        evaluate_applicability(request, authority=None)
