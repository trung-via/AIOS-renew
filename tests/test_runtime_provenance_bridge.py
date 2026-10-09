"""VP-03D offline deterministic content fixtures, never real issuer credentials.

No live Runtime execution, verification, transport, admission, network or source
writes. Share only upstream minimal fixture builders and assertions, not suites.
"""

import copy
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from aios_renew import proof_applicability as proof
from aios_renew import proof_authority_handoff as handoff
from aios_renew import runtime
from aios_renew import runtime_provenance_bridge as bridge
from aios_renew.proof_coverage_contract import proof_mapping_digest
from test_proof_authority_handoff import request_values, snapshot
from test_proof_checkpoint_lineage import GitFixture, build_case, mapping_for, pin


def terminal_case(repo, exit_code=1, published=True):
    fixture = GitFixture(repo)
    task = {"task_id": "TASK-333", "revision": 1, "goal": "Offline terminal content inspection.",
        "problem": "Inspect immutable fixture bytes without issuing provenance.",
        "assumptions": ["This is conditional fixture content."],
        "scope": {"inspect": ["app.txt"], "modify": ["app.txt"]},
        "non_goals": ["Authenticate fixture producer rights."],
        "constraints": {"hard": ["Never activate authority."]},
        "acceptance": [{"id": "AC1", "condition": "Observe content."}],
        "verification": {"required": ["offline-content-command"]}}
    base, pins = fixture.commit(None, {".ai/tasks/TASK-333.yaml": task, "app.txt": b"base"})
    task_pin = pins[".ai/tasks/TASK-333.yaml"]
    candidate, _ = fixture.commit(base, {"app.txt": b"candidate"})
    tree = fixture.git("rev-parse", candidate + "^{tree}").decode().strip()
    run_id = "RUN-333-FIXTURE-001"
    run = {"run_id": run_id, "task": {"id": "TASK-333", "revision": 1}, "executor": "codex",
        "base_sha": base, "workspace": "OFFLINE", "head_sha": candidate, "status": "ACTIVE"}
    raw_path = ".ai/transport/raw/E1.txt"
    evidence = {"evidence_id": "E1", "run_id": run_id, "subject_sha": candidate, "type": "test",
        "source": {"command": "offline-content-command"},
        "result": {"exit_code": exit_code, "summary": "Original fixture outcome."}, "raw": {"path": raw_path}}
    result = {"result": {"head_sha": candidate, "claims": [{"id": "C1", "satisfies": ["AC1"],
        "claim": "Existing fixture observation.", "evidence": ["E1"]}],
        "changed_files": ["app.txt"], "unresolved": []}, "evidence": [evidence]}
    # Real transport's isolated terminal/decision topology: no forged ancestry.
    artifact, pins = fixture.commit(None, {".ai/transport/run.json": run,
        ".ai/transport/result.json": result, raw_path: b"original raw FAIL or PASS\n"})
    review_id = "REVIEW-333-FIXTURE"
    review = {"review_id": review_id, "reviewed_sha": candidate, "mode": "PRIMARY",
        "verdict": "PASS" if exit_code == 0 else "BLOCKED",
        "acceptance": {"AC1": "PASS" if exit_code == 0 else "FAIL"}, "findings": []}
    decision, review_pins = fixture.commit(None, {f".ai/reviews/{review_id}.yaml": review})
    fixture.selection = {"schema": bridge.REQUEST_SCHEMA, "task": task_pin,
        "task_id": "TASK-333", "task_revision": 1, "run": pins[".ai/transport/run.json"],
        "run_id": run_id, "base_sha": base, "candidate_sha": candidate, "tree_sha": tree,
        "result": pins[".ai/transport/result.json"], "reviewed_source": pins[".ai/transport/result.json"],
        "review": review_pins[f".ai/reviews/{review_id}.yaml"], "evidence": [{"evidence_id": "E1",
        "evidence_digest": proof.canonical_digest(evidence), "raw": pins[raw_path]}]}
    fixture.main = candidate if published else base
    fixture.git("update-ref", "refs/heads/main", fixture.main)
    for name, sha in (("review", candidate), ("artifacts", artifact), ("review-decision", decision)):
        fixture.git("update-ref", f"refs/heads/aios/{name}/{run_id}", sha)
    return fixture


@pytest.fixture(scope="module")
def specimens(tmp_path_factory):
    return terminal_case(tmp_path_factory.mktemp("terminal") / "repo")


@pytest.fixture(scope="module")
def lineage_specimen(tmp_path_factory):
    return build_case(tmp_path_factory.mktemp("lineage") / "repo", distinct=True)


def inspect(fixture, values=None, main=None):
    return bridge.inspect_terminal_content(bridge.decode_selection(values or fixture.selection),
        fixture_repository=fixture.repo, fixture_main_sha=main or fixture.main)


def inactive(result):
    assert result.status in ("UNKNOWN", "BLOCK")
    assert result.preterminal_state == result.lease_state == result.cas_state == "UNKNOWN"
    for name in handoff.InactiveEffects.__dataclass_fields__:
        assert getattr(result, name) == getattr(handoff.InactiveEffects(), name), name


def test_existing_runtime_boundary_is_read_only_and_not_constructor_authentication(monkeypatch, specimens):
    def forbidden(*args, **kwargs):
        raise AssertionError("canonical availability must not execute or read caller Git")
    monkeypatch.setattr(proof._Git, "__init__", forbidden)
    monkeypatch.setattr(runtime.subprocess, "run", forbidden)
    availability = runtime.observe_canonical_provenance_availability()
    assert availability.schema == "runtime-provenance-availability-v1"
    assert availability.runtime_issuer == availability.reviewer_issuer == "UNAVAILABLE"
    selection = bridge.decode_selection(specimens.selection)
    result = bridge.observe_terminal_provenance(selection)
    assert result.schema == bridge.SCHEMA and result.terminal is result.currentness is None
    assert bridge.ISSUER_UNAVAILABLE in result.reasons
    assert result == bridge.observe_terminal_provenance(selection)
    inactive(result)
    with pytest.raises(TypeError):
        runtime.observe_canonical_provenance_availability(repo=specimens.repo)
    with pytest.raises(TypeError):
        runtime.CanonicalProvenanceAvailability(runtime_issuer="AUTHENTICATED")
    with pytest.raises(TypeError):
        bridge.observe_terminal_provenance(selection, runtime_instance=object())
    with pytest.raises(TypeError):
        bridge.observe_terminal_provenance(selection, fixture_authority=object())


@pytest.mark.parametrize("exit_code,published", [(0, True), (1, True), (1, False)])
def test_exact_terminal_content_preserves_original_fail_and_publication(tmp_path, monkeypatch, exit_code, published):
    fixture = terminal_case(tmp_path / "repo", exit_code, published)
    before = snapshot(fixture.repo)
    calls = []
    real_run = proof.subprocess.run
    def readonly(args, **kwargs):
        command = args[args.index("-C") + 2]
        assert command in ("cat-file", "rev-parse"), args
        assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
        calls.append(command)
        return real_run(args, **kwargs)
    monkeypatch.setattr(proof.subprocess, "run", readonly)
    result = inspect(fixture)
    assert result.content_state == "CONSISTENT", result.reasons
    assert result.source_authentication == "CONDITIONAL_FIXTURE_CONTENT"
    assert result.schema == bridge.CONTENT_SCHEMA
    facts = result.terminal
    assert facts.selection == bridge.decode_selection(fixture.selection)
    assert facts.terminal_kind == "RESULT" and facts.source_phase == "TERMINAL"
    assert facts.recorded_run_status == "ACTIVE"  # Encoding preserved, no live authority.
    assert facts.evidence[0].exit_code == exit_code
    assert facts.evidence[0].outcome == ("PASS" if exit_code == 0 else "FAIL")
    assert facts.evidence[0].subject_sha == fixture.selection["candidate_sha"]
    assert facts.review_verdict == ("PASS" if exit_code == 0 else "BLOCKED")
    assert facts.review_acceptance == (("AC1", "PASS" if exit_code == 0 else "FAIL"),)
    assert result.currentness.state == "CONSISTENT"
    assert result.currentness.publication_state == ("PUBLISHED" if published else "UNPUBLISHED")
    assert result == inspect(fixture) and calls
    assert snapshot(fixture.repo) == before
    inactive(result)
    with pytest.raises(FrozenInstanceError):
        facts.evidence[0].outcome = "PASS"
    with pytest.raises(ValueError):
        replace(result, authorization_granted=True)


@pytest.mark.parametrize("field", ["task_id", "task_revision", "run_id", "base_sha", "candidate_sha", "tree_sha",
    "task", "run", "result", "review", "reviewed_source", "evidence-id", "evidence-digest", "raw"])
def test_wrong_exact_source_identity_fails_closed(specimens, field):
    values = copy.deepcopy(specimens.selection)
    if field in ("task", "run", "result", "review", "reviewed_source"):
        values[field]["sha256"] = "0" * 64
    elif field == "raw":
        values["evidence"][0]["raw"]["sha256"] = "0" * 64
    elif field.startswith("evidence-"):
        key = "evidence_id" if field.endswith("id") else "evidence_digest"
        values["evidence"][0][key] = "E-SWAPPED" if key == "evidence_id" else "0" * 64
    else:
        values[field] = {"task_id": "TASK-WRONG", "task_revision": 2, "run_id": "RUN-WRONG"}.get(field, "0" * 40)
    result = inspect(specimens, values)
    assert result.content_state != "CONSISTENT" and result.terminal is None
    inactive(result)


@pytest.mark.parametrize("field,value", [("issuer", "Runtime"), ("repository", "trusted"),
    ("remote", "origin"), ("catalog", {}), ("fixture_authority", {}),
    ("expected_main_sha", "0" * 40), ("trusted", True), ("acceptance_pass", True)])
def test_selection_cannot_enroll_trust_or_override_currentness(specimens, field, value):
    with pytest.raises(bridge.ProvenanceInputError):
        bridge.decode_selection(dict(specimens.selection, **{field: value}))


@pytest.mark.parametrize("defect", ["version", "bool-revision", "missing", "traversal", "oversized", "hook", "typed-hook"])
def test_strict_bounded_decoding(specimens, defect):
    values = copy.deepcopy(specimens.selection)
    class Hook:
        def __getattr__(self, name):
            raise AssertionError("untrusted object hook")
    if defect == "version":
        values["schema"] = bridge.CONTENT_SCHEMA
    elif defect == "bool-revision":
        values["task_revision"] = True
    elif defect == "missing":
        del values["reviewed_source"]
    elif defect == "traversal":
        values["result"]["path"] = "../result.json"
    elif defect == "oversized":
        values["evidence"] *= bridge.MAX_EVIDENCE + 1
    elif defect == "hook":
        values["task"] = Hook()
    else:
        request = bridge.decode_selection(values)
        with pytest.raises(bridge.ProvenanceInputError):
            bridge.observe_terminal_provenance(replace(request, task=Hook()))
        return
    with pytest.raises(bridge.ProvenanceInputError):
        bridge.decode_selection(values)


@pytest.mark.parametrize("name", ["main", "review", "artifacts", "review-decision", "during-read"])
def test_stale_ambiguous_or_torn_publication_refs(specimens, monkeypatch, name):
    main_ref = "refs/heads/main"
    ref = main_ref if name in ("main", "during-read") else f"refs/heads/aios/{name}/{specimens.selection['run_id']}"
    original_sha = specimens.git("rev-parse", ref).decode().strip()
    try:
        if name == "during-read":
            original_read = proof._Git._read
            count = []
            def moved(reader, args, oid=None):
                value = original_read(reader, args, oid)
                if args == ["rev-parse", "--verify", main_ref]:
                    count.append(1)
                    if len(count) == 2:
                        return (specimens.selection["base_sha"] + "\n").encode()
                return value
            monkeypatch.setattr(proof._Git, "_read", moved)
        else:
            specimens.git("update-ref", ref, specimens.selection["base_sha"])
        result = inspect(specimens)
        assert result.status == "UNKNOWN" and result.terminal is None
        assert result.currentness.state == result.currentness.publication_state == "UNKNOWN"
        assert "SEPARATE_MAIN_PUBLICATION_CURRENTNESS_CONFLICT" in result.reasons
        inactive(result)
    finally:
        specimens.git("update-ref", ref, original_sha)


@pytest.mark.parametrize("defect", ["raw-absent", "duplicate-evidence", "duplicate-json", "review-candidate", "review-duplicate",
    "review-acceptance", "result-candidate", "admission-task", "raw-swapped", "torn"])
def test_coherent_reissued_attacker_content_is_never_producer_authentication(specimens, defect):
    values = copy.deepcopy(specimens.selection)
    run = specimens.value(values["run"])
    result = specimens.value(values["result"])
    review = specimens.value(values["review"])
    raw_path = values["evidence"][0]["raw"]["path"]
    updates = {values["run"]["path"]: run, values["result"]["path"]: result, raw_path: b"raw"}
    if defect == "raw-absent":
        del updates[raw_path]
    elif defect == "duplicate-evidence":
        result["evidence"].append(copy.deepcopy(result["evidence"][0]))
        values["evidence"].append(copy.deepcopy(values["evidence"][0]))
    elif defect == "duplicate-json":
        updates[values["result"]["path"]] = b'{"result":{},"result":{},"evidence":[]}'
    elif defect == "review-candidate":
        review["reviewed_sha"] = values["base_sha"]
    elif defect == "review-acceptance":
        review["acceptance"] = {"AC-WRONG": "FAIL"}
    elif defect == "result-candidate":
        result["result"]["head_sha"] = values["base_sha"]
    elif defect == "admission-task":
        run["task"]["revision"] = 2
    elif defect == "raw-swapped":
        result["evidence"][0]["raw"]["path"] = ".ai/transport/raw/other.txt"
        values["evidence"][0]["evidence_digest"] = proof.canonical_digest(result["evidence"][0])
    artifact, pins = specimens.commit(None, updates)
    values["run"] = pins[values["run"]["path"]]
    values["result"] = values["reviewed_source"] = pins[values["result"]["path"]]
    if raw_path in pins:
        values["evidence"][0]["raw"] = pins[raw_path]
    if defect == "torn":
        values["run"] = specimens.selection["run"]
    review_value = ((json.dumps(review)[:-1] + ', "reviewed_sha": "' + values["candidate_sha"] + '"}').encode()
                    if defect == "review-duplicate" else review)
    decision, pins = specimens.commit(None, {values["review"]["path"]: review_value})
    values["review"] = pins[values["review"]["path"]]
    run_id = values["run_id"]
    try:
        specimens.git("update-ref", f"refs/heads/aios/artifacts/{run_id}", artifact)
        specimens.git("update-ref", f"refs/heads/aios/review-decision/{run_id}", decision)
        before = snapshot(specimens.repo)
        result = inspect(specimens, values)
        assert result.content_state != "CONSISTENT" and result.terminal is None, defect
        canonical = bridge.observe_terminal_provenance(bridge.decode_selection(values))
        assert canonical.source_authentication == "UNAVAILABLE" and canonical.terminal is None
        assert snapshot(specimens.repo) == before
        inactive(result)
        inactive(canonical)
    finally:
        for name, identity in (("artifacts", specimens.selection["result"]), ("review-decision", specimens.selection["review"])):
            specimens.git("update-ref", f"refs/heads/aios/{name}/{run_id}", identity["commit_sha"])


@pytest.mark.parametrize("spoof", ["duck", "constructor", "forged-availability"])
def test_runtime_looking_producers_cannot_assert_authenticated_rights(specimens, monkeypatch, spoof):
    class RuntimeLooking:
        runtime_issuer = "AUTHENTICATED"
        terminal_source = "AUTHENTICATED"
        repo = specimens.repo
    if spoof == "duck":
        fake = RuntimeLooking()
    elif spoof == "constructor":
        fake = runtime.RuntimeCompletion(repo=specimens.repo, state=object(), task=object(), run=object(),
            run_path=specimens.repo / "run.json", verification_runner=lambda: None,
            observation_tracker=None, error_type=ValueError)
    else:
        fake = runtime.CanonicalProvenanceAvailability()
        object.__setattr__(fake, "runtime_issuer", "AUTHENTICATED")
    monkeypatch.setattr(runtime, "observe_canonical_provenance_availability", lambda: fake)
    result = bridge.observe_terminal_provenance(bridge.decode_selection(specimens.selection))
    assert result.status == "BLOCK" and "UNTRUSTED_RUNTIME_SOURCE_OBSERVATION" in result.reasons
    inactive(result)


def test_canonical_handoff_retains_missing_preterminal_and_rejects_fixture_observations(lineage_specimen, specimens, monkeypatch):
    fixture = lineage_specimen
    request = handoff.decode_request(request_values(fixture))
    before = snapshot(fixture.repo)
    canonical = handoff.evaluate_handoff(request)
    assert canonical.runtime_provenance.schema == bridge.SCHEMA
    assert canonical.runtime_provenance.terminal is None
    assert "LIVE_PRETERMINAL_SOURCE_UNAVAILABLE" in canonical.reasons
    assert "HUMAN_LEASE_UNAVAILABLE" in canonical.reasons and "ATOMIC_CAS_ABA_UNAVAILABLE" in canonical.reasons
    diagnostic = handoff.inspect_content_handoff(request, fixture_authority=fixture.authority)
    assert diagnostic.content_lineage.history_authenticated
    assert len(diagnostic.targets) == 5
    assert {v.original.outcome for v in diagnostic.targets} == {"PASS", "FAIL"}
    assert next(v.original for v in diagnostic.targets if v.selection.source_proof_id == "proof-unit").outcome == "FAIL"
    assert all(v.content_applicability.witness is not None for v in diagnostic.targets)
    assert diagnostic.content_lineage.seals[-1].checkpoint.resource_usage.corrections == 1
    assert diagnostic.runtime_provenance is None
    fixture_terminal = inspect(specimens)
    monkeypatch.setattr(bridge, "observe_handoff_provenance", lambda request: fixture_terminal)
    rejected = handoff.evaluate_handoff(request)
    assert rejected.status == "BLOCK" and rejected.runtime_provenance is None
    assert "UNTRUSTED_RUNTIME_SOURCE_OBSERVATION" in rejected.reasons
    assert snapshot(fixture.repo) == before
    for result in (canonical, diagnostic, rejected):
        assert result.activation == "NOT_ACTIVATED" and not result.issuer_authenticated
        assert all(v.state == handoff.State.UNKNOWN for v in result.targets)


def test_duplicate_replay_delivery_is_observation_not_consumption(lineage_specimen):
    fixture = lineage_specimen
    values = request_values(fixture, replay=fixture.catalog["checkpoints"][-1]["checkpoint"])
    request = handoff.decode_request(values)
    before = snapshot(fixture.repo)
    first = handoff.evaluate_handoff(request)
    assert first == handoff.evaluate_handoff(request)
    content = handoff.inspect_content_handoff(request, fixture_authority=fixture.authority)
    assert content.observation == "CONTENT_SAME_FACT"
    assert content.content_lineage.seals[-1].checkpoint.resource_usage.corrections == 1
    for key in ("originals", "obligations"):
        duplicate = copy.deepcopy(values)
        duplicate[key].append(copy.deepcopy(duplicate[key][0]))
        assert handoff.evaluate_handoff(handoff.decode_request(duplicate)).status == "BLOCK"
    missing = copy.deepcopy(values)
    missing["replay"]["blob_sha"] = "0" * 40
    assert handoff.inspect_content_handoff(handoff.decode_request(missing),
        fixture_authority=fixture.authority).status == "UNKNOWN"
    conflict = handoff.decode_request(request_values(fixture, replay=fixture.catalog["current"]))
    blocked = handoff.inspect_content_handoff(conflict, fixture_authority=fixture.authority)
    assert blocked.status == "BLOCK" and "CONFLICTING_OR_UNCATALOGED_REPLAY" in blocked.reasons
    assert snapshot(fixture.repo) == before


@pytest.mark.parametrize("dimension", [None, "integration_ref", "repetition_ref", "environment_ref"])
def test_mapping_and_condition_drift_cannot_become_canonical_authority(lineage_specimen, dimension):
    fixture = lineage_specimen
    entry = fixture.catalog["checkpoints"][-1]
    contract = fixture.value(entry["contract"])
    if dimension is None:
        contract["obligations"][0]["claim"]["text"] = "Changed meaning."
    else:
        contract["obligations"][0]["conditions"][dimension] = "changed-condition-v2"
    parsed, mapping = mapping_for(contract)
    seal = fixture.value(entry["checkpoint"])
    seal["binding"]["proof_contract"] = pin(parsed)
    seal["binding"]["proof_mapping"] = {"id": mapping["id"], "revision": mapping["revision"],
        "digest": proof_mapping_digest(mapping)}
    authority, _ = fixture.variant([(entry["contract"], contract), (entry["mapping"], mapping),
        (entry["checkpoint"], seal)], reissue_seals=True)
    request = handoff.decode_request(request_values(fixture, authority))
    before = snapshot(fixture.repo)
    content = handoff.inspect_content_handoff(request, fixture_authority=authority)
    assert content.status == "BLOCK" and "SEMANTIC_OR_MAPPING_CHANGED" in content.reasons
    canonical = handoff.evaluate_handoff(request)
    assert canonical.status == "UNKNOWN" and canonical.content_lineage is None
    assert canonical.activation == "NOT_ACTIVATED" and not canonical.evidence_reuse_authorized
    assert snapshot(fixture.repo) == before


@pytest.mark.parametrize("defect", ["policy", "lease", "torn", "duplicate-feedback"])
def test_preterminal_policy_lease_and_torn_effects_remain_unavailable(lineage_specimen, defect):
    fixture = lineage_specimen
    if defect == "policy":
        original_pin = fixture.catalog["transitions"][0]["catalogs"][0]
        catalog = fixture.value(original_pin)
        review_pin = catalog["review"]
        review = fixture.value(review_pin)
        policy = next(v for v in review["dimensions"] if v["dimension"] == "policy")
        identity = policy["target_context"]
        context = fixture.value(identity)
        context["facts"]["state"] = "changed-policy-v2"
        parent, pins = fixture.commit(fixture.catalog_pin["commit_sha"], {identity["path"]: context})
        policy["target_context"] = pins[identity["path"]]
        parent, pins = fixture.commit(parent, {review_pin["path"]: review})
        catalog["review"] = pins[review_pin["path"]]
        witness_pin = catalog["witness"]
        witness = fixture.value(witness_pin)
        witness["review"] = catalog["review"]
        comparison = next(v for v in witness["dimensions"] if v["dimension"] == "policy")
        objects = [[p, ["100644", fixture.trees[catalog["target_candidate_sha"]][p]]] for p in sorted(policy["paths"])]
        comparison["target_digest"] = proof.canonical_digest({"conditions": context["conditions"],
            "facts": context["facts"], "objects": objects})
        parent, pins = fixture.commit(parent, {witness_pin["path"]: witness})
        catalog["witness"] = pins[witness_pin["path"]]
        parent, pins = fixture.commit(parent, {original_pin["path"]: catalog})
        outer = copy.deepcopy(fixture.catalog)
        outer["transitions"][0]["catalogs"][0] = pins[original_pin["path"]]
        parent, pins = fixture.commit(parent, {"lineage/policy-catalog.json": outer})
        authority = replace(fixture.authority, catalog=proof.decode_record_identity(pins["lineage/policy-catalog.json"]))
    else:
        identity = fixture.catalog["current"]
        current = fixture.value(identity)
        if defect == "lease":
            current["delegation"]["lease_generation"] += 1
        elif defect == "torn":
            current["tail_state"] = "TORN"
        else:
            current["consumed_feedback"].append(current["consumed_feedback"][0])
        authority, _ = fixture.variant([(identity, current)])
    request = handoff.decode_request(request_values(fixture, authority))
    before = snapshot(fixture.repo)
    content = handoff.inspect_content_handoff(request, fixture_authority=authority)
    if defect == "policy":
        assert any(v.content_applicability is None or v.content_applicability.state != handoff.State.VALID
                   for v in content.targets)
    else:
        assert content.observation != "CONTENT_CONSISTENT"
    canonical = handoff.evaluate_handoff(request)
    assert canonical.runtime_provenance.preterminal_state == "UNKNOWN"
    assert not canonical.checkpoint_consumption_authorized and not canonical.feedback_consumption_authorized
    assert snapshot(fixture.repo) == before
