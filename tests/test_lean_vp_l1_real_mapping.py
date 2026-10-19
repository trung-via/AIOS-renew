"""Actual committed TASK-381 overlay; no selection, review or evidence writer.

Local Git fixtures carry the production source bytes under isolated source
commits. They exercise the real readers without issuing synthetic PASS or Human
selection. Target-code proof execution remains a separate Runtime obligation.
"""

import copy
import hashlib
import json
import subprocess
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from aios_renew import review_transport as transport
from aios_renew.proof_coverage_contract import (
    DeclarationPin,
    MappingReviewAcceptance,
    MappingReviewScope,
    ProofConditions,
    ProofCoverageError,
    TaskBinding,
    proof_conditions_equivalent,
    proof_mapping_digest,
    validate_mapping_review_scope,
    validate_proof_contract,
    validate_proof_mapping,
)
from aios_renew.task import parse_task, validate_semantic_proof_intake
from tests.git_fixture_support import commit_fixture_state, materialize_git_baseline


REPO = Path(__file__).resolve().parents[1]
TARGET = "bf1b3fa121fc7daebaa8257aa81d6546611e06e7"
PUBLISHED = "0ff5a56674c5f56012e0df551a069d6c461fd8fa"
RUN_BASE = "e381e921bbf82dd131c7178f35c4aad9ccac22dd"
TARGET_PATH = ".ai/tasks/TASK-381.yaml"
REVIEW_TASK_PATH = ".ai/tasks/TASK-382.yaml"
PATHS = dict(task=TARGET_PATH,
             contract="docs/aios-proof-contracts/TASK-381-contract.json",
             mapping="docs/aios-proof-contracts/TASK-381-mapping.json")
MODIFIED = {PATHS["contract"], PATHS["mapping"],
    "docs/AIOS-LEAN-VP-L1-REAL-MAPPING-REVIEW.md",
    "tests/test_lean_vp_l1_real_mapping.py",
}
MANDATORY = {
    "AC1": {"D01", "D02"}, "AC2": {"N01", "N02", "N03"},
    "AC3": {"B01", "B02", "B03"}, "AC4": {"S01", "S02", "S03"},
    "AC5": {"A01", "A02", "A03"}, "AC6": {"R01", "R02"},
    "AC7": {"L01", "L02"}, "AC8": {"T01", "T02"},
    "AC9": {"DOC01"}, "AC10": {"SC01", "SC02"},
}


def git_bytes(repo, *args):
    return subprocess.run(("git", "-C", str(repo), *args), check=True,
                          capture_output=True).stdout


def git(repo, *args):
    return git_bytes(repo, *args).decode("utf-8").strip()


def committed(path, sha="HEAD"):
    return git_bytes(REPO, "show", f"{sha}:{path}")


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("ascii")


def task_binding(raw):
    task = parse_task(raw.decode("utf-8"))
    return dict(task_id=task.task_id, revision=task.revision,
                envelope_digest=hashlib.sha256(raw).hexdigest(),
                acceptance_ids=sorted(item.id for item in task.acceptance))


@pytest.fixture
def documents():
    # Independent pin: original canonical Git bytes, never incoming contract.task.
    raw = committed(TARGET_PATH, TARGET)
    return (json.loads(committed(PATHS["contract"])),
            json.loads(committed(PATHS["mapping"])), task_binding(raw))


def pin(mapping):
    # Test-only structural pin preparation, never approval or a trusted reader.
    return dict(id=mapping["id"], revision=mapping["revision"],
                digest=proof_mapping_digest(mapping))


def validate(documents, *, mapping_pin=None):
    contract, mapping, expected_task = documents
    return validate_proof_mapping(contract, mapping, expected_task=expected_task,
                                  expected_mapping=pin(mapping) if mapping_pin is None else mapping_pin)


def repin_contract(contract, mapping, expected_task):
    """Reach semantic negative checks past digest gates, without claiming trust."""
    parsed = validate_proof_contract(contract, expected_task=expected_task)
    mapping.update(contract_id=parsed.id, contract_revision=parsed.revision,
                   contract_digest=parsed.digest)
    obligations = {item.id: item for item in parsed.obligations}
    for entry in mapping["entries"]:
        if entry["obligation_id"] in obligations:
            entry["obligation_digest"] = obligations[entry["obligation_id"]].digest


def test_committed_pair_binds_raw_target_and_all_mandatory_properties(documents):
    contract, mapping, expected = documents
    declaration = validate(documents)
    assert expected["task_id"] == "TASK-381" and expected["revision"] == 1
    assert set(expected["acceptance_ids"]) == set(MANDATORY)
    assert declaration.contract.task.envelope_digest == hashlib.sha256(
        committed(TARGET_PATH, TARGET)).hexdigest()
    assert committed(TARGET_PATH) == committed(TARGET_PATH, TARGET)
    assert git(REPO, "rev-parse", f"{TARGET}:{TARGET_PATH}") == "3db2f20f9a42560d0d1b55db522bafb0cca87683"
    assert mapping["contract_digest"] == declaration.contract.digest
    assert len(mapping["entries"]) == len(mapping["proofs"]) == 23
    for acceptance, suffixes in MANDATORY.items():
        assert {item.id for item in declaration.contract.obligations
                if acceptance in item.acceptance_ids} == {f"T381-{suffix}" for suffix in suffixes}
    for item in contract["obligations"] + mapping["proofs"]:
        assert set(item["conditions"]) == set(ProofConditions.__dataclass_fields__)
        assert item["conditions"]["candidate_sha"] == TARGET
        assert item["conditions"]["base_sha"] is None and item["kind"] == "candidate"
    # Requirements do not contain observations, outcomes or authority assertions.
    assert not {"outcome", "passed", "evidence", "human_selection"} & contract.keys()


@pytest.mark.parametrize("fault", ["task-id", "revision", "digest", "acceptance"])
def test_independent_task_pin_rejects_forged_or_stale_subject(documents, fault):
    contract, _, _ = documents
    changes = dict(task_id="TASK-OTHER", revision=2, envelope_digest="0" * 64,
                   acceptance_ids=["AC1"])
    key = {"task-id": "task_id", "revision": "revision", "digest": "envelope_digest",
           "acceptance": "acceptance_ids"}[fault]
    contract["task"][key] = changes[key]
    with pytest.raises(ProofCoverageError):
        validate(documents)


def test_normalized_yaml_digest_is_not_the_raw_envelope_pin(documents):
    import yaml
    contract, _, expected = documents
    reconstructed = yaml.safe_dump(yaml.safe_load(committed(TARGET_PATH, TARGET)))
    contract["task"]["envelope_digest"] = hashlib.sha256(reconstructed.encode()).hexdigest()
    assert contract["task"]["envelope_digest"] != expected["envelope_digest"]
    with pytest.raises(ProofCoverageError):
        validate(documents)


@pytest.mark.parametrize("fault", ["missing-acceptance", "missing-obligation", "missing-entry",
    "duplicate-entry", "unused-proof", "wrong-claim", "changed-claim-text", "extra-claim", "stale-obligation",
    "wrong-contract", "wrong-contract-revision", "wrong-contract-digest"])
def test_incomplete_forged_or_ambiguous_coverage_is_rejected(documents, fault):
    contract, mapping, _ = documents
    if fault == "missing-acceptance":
        contract["obligations"] = [item for item in contract["obligations"]
                                   if item["acceptance_ids"] != ["AC10"]]
    elif fault == "missing-obligation":
        contract["obligations"].pop(0)  # AC1 still exists: stale contract/entry must fail.
    elif fault == "missing-entry":
        mapping["entries"].pop()
    elif fault == "duplicate-entry":
        mapping["entries"].append(copy.deepcopy(mapping["entries"][0]))
    elif fault == "unused-proof":
        proof = copy.deepcopy(mapping["proofs"][0])
        proof["id"] = "UNUSED-PROOF"
        proof["conditions"]["population_ref"] = "unused-population-r1"
        mapping["proofs"].append(proof)
    elif fault == "wrong-claim":
        mapping["proofs"][0]["claims"] = [dict(id="OTHER-CLAIM", text="An unrelated property.")]
    elif fault == "changed-claim-text":
        mapping["proofs"][0]["claims"][0]["text"] = "A different property under the copied claim identity."
    elif fault == "extra-claim":
        mapping["proofs"][0]["claims"].append(dict(id="EXTRA-CLAIM", text="An unsupported property."))
    elif fault == "stale-obligation":
        mapping["entries"][0]["obligation_digest"] = "0" * 64
    elif fault == "wrong-contract":
        mapping["contract_id"] = "OTHER-CONTRACT"
    elif fault == "wrong-contract-revision":
        mapping["contract_revision"] += 1
    else:
        mapping["contract_digest"] = "0" * 64
    with pytest.raises(ProofCoverageError):
        validate(documents)


def test_removing_same_acceptance_obligation_cannot_reuse_committed_mapping_pin(documents):
    contract, mapping, expected = documents
    original_pin = pin(mapping)
    removed = contract["obligations"].pop(0)
    mapping["entries"] = [item for item in mapping["entries"] if item["obligation_id"] != removed["id"]]
    mapping["proofs"] = [item for item in mapping["proofs"] if item["id"] != "T381-P-D01"]
    repin_contract(contract, mapping, expected)
    # The residual AC1 label is insufficient; the original complete mapping is pinned.
    with pytest.raises(ProofCoverageError, match="mapping pin"):
        validate(documents, mapping_pin=original_pin)


@pytest.mark.parametrize("dimension", sorted(ProofConditions.__dataclass_fields__))
def test_each_distinguishing_condition_prevents_false_proof_equivalence(documents, dimension):
    contract, mapping, _ = documents
    original = contract["obligations"][0]["conditions"]
    changed = mapping["proofs"][0]["conditions"]
    if dimension == "candidate_sha":
        changed[dimension] = RUN_BASE
    elif dimension == "base_sha":
        changed[dimension] = RUN_BASE
        mapping["proofs"][0]["kind"] = "comparison"
    elif dimension == "population_size":
        changed[dimension] += 1
    elif dimension == "fixture_refs":
        changed[dimension] = ["different-fixture-r1"]
    elif dimension == "workers":
        changed.update(workers=2, worker_mode="parallel")
    elif dimension == "worker_mode":
        changed[dimension] = "parallel"  # Parallel-one-worker also differs from serial.
    else:
        changed[dimension] = "different-condition-r1"
    assert not proof_conditions_equivalent(original, changed)
    with pytest.raises(ProofCoverageError):
        validate(documents)


def test_integration_claim_cannot_reuse_an_isolated_proof_despite_copied_claim(documents):
    _, mapping, _ = documents
    isolated = next(item for item in mapping["proofs"] if item["id"] == "T381-P-D01")
    integrated = next(item for item in mapping["proofs"] if item["id"] == "T381-P-S01")
    isolated["claims"].extend(integrated["claims"])
    for entry in mapping["entries"]:
        if entry["proof_id"] == integrated["id"]:
            entry["proof_id"] = isolated["id"]
    mapping["proofs"].remove(integrated)
    with pytest.raises(ProofCoverageError, match="conditions"):
        validate(documents)


def locator(repo, sha, path):
    return transport.GitSemanticSource(sha, git(repo, "rev-parse", f"{sha}^{{tree}}"),
                                       path, git(repo, "rev-parse", f"{sha}:{path}"))


@pytest.fixture
def source_package(tmp_path, documents):
    contract, mapping, expected = documents
    repo, remote, base = materialize_git_baseline(
        tmp_path / "real-pair-isolated-source", files={
            TARGET_PATH: committed(TARGET_PATH, TARGET),
            REVIEW_TASK_PATH: committed(REVIEW_TASK_PATH, RUN_BASE)},
        user_name="Source Fixture", user_email="source@example.invalid",
        commit_message="Actual TASK bytes; no Human choice or REVIEW")
    for role in ("contract", "mapping"):
        target = repo / PATHS[role]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(committed(PATHS[role]))
    candidate = commit_fixture_state(repo, paths=[PATHS["contract"], PATHS["mapping"]],
        message="Actual source pair in isolated Git", user_name="Source Fixture",
        user_email="source@example.invalid", remote=remote, remote_ref="refs/heads/main")
    sources = {role: locator(repo, base if role == "task" else candidate, path)
               for role, path in PATHS.items()}
    declaration = dict(schema="task-semantic-proof-intake-v1", task=expected,
        candidate_sha=TARGET, sources={role: dict(source_sha=item.source_sha, blob_sha=item.blob_sha)
            for role, item in sources.items()}, contract=contract, mapping=mapping)
    return dict(repo=repo, remote=remote, base=base, main=candidate, sources=sources,
                declaration=declaration)


def bind(package, *, reader=transport.bind_canonical_semantic_review_authority, **overrides):
    arguments = dict(expected_remote_url=package["remote"].as_posix(),
                     expected_main_sha=package["main"], expected_candidate_sha=TARGET,
                     sources=package["sources"])
    arguments.update(overrides)
    return reader(package["repo"], encoded(package["declaration"]), **arguments)


def test_actual_pair_uses_real_authenticated_reader_without_elevating_labels(source_package):
    value = bind(source_package)
    assert value.source_status == "SOURCE_CONSISTENT"
    assert value.human_intent == value.reviewer_authority == "NOT_ESTABLISHED"
    assert value.reason == "HUMAN_MAPPING_SELECTION_UNAVAILABLE" and value.lineage is None
    assert value.canonical_verification == value.evidence_applicability == "UNESTABLISHED"
    assert value.future_version_admission == "NOT_ADMITTED"
    assert value.ka01_g3 == value.ka01_g6 == value.production_opt_in == "NOT_ESTABLISHED"
    assert value.production_activation == "NOT_ACTIVATED"
    assert not value.production_eligible and not value.execution_authorized
    for source in value.source_binding.sources:
        assert source.content_sha256 == hashlib.sha256(committed(PATHS[source.role],
            TARGET if source.role == "task" else "HEAD")).hexdigest()


def test_proposed_complete_correspondence_is_consistent_but_grants_no_authority(source_package):
    package = source_package
    value = bind(package)
    intake = value.source_binding.intake
    review = task_binding(committed(REVIEW_TASK_PATH, RUN_BASE))
    review["acceptance_ids"] = tuple(review["acceptance_ids"])
    partition = {
        "AC1": ("B02",), "AC2": ("D01", "D02", "N01", "N02", "N03", "B01", "B03"),
        "AC3": ("S01", "S02"), "AC4": ("A01",), "AC5": ("A02", "A03"),
        "AC6": ("SC01",), "AC7": ("DOC01",),
        "AC8": ("R01", "R02", "L01", "L02", "T01", "T02"),
        "AC9": ("S03", "SC02"),
    }
    obligations = {item.id: item for item in intake.coverage.contract.obligations}
    correspondence = []
    for acceptance, suffixes in partition.items():
        ids = tuple(f"T381-{suffix}" for suffix in suffixes)
        targets = tuple(sorted({ac for identity in ids for ac in obligations[identity].acceptance_ids}))
        correspondence.append(MappingReviewAcceptance(acceptance, targets, ids))
    scope = MappingReviewScope(1, TaskBinding(**review), intake.coverage.contract.task,
        TARGET, DeclarationPin(**asdict(intake.contract_pin)),
        DeclarationPin(**asdict(intake.mapping_pin)), tuple(correspondence))
    normalized = validate_mapping_review_scope(scope, intake=intake, expected_review_task=TaskBinding(**review))
    assert {item.review_acceptance_id for item in normalized.acceptance} == set(review["acceptance_ids"])
    assert value.human_intent == value.reviewer_authority == "NOT_ESTABLISHED"
    with pytest.raises(ProofCoverageError):
        validate_mapping_review_scope(replace(scope, candidate_sha=RUN_BASE),
                                      intake=intake, expected_review_task=TaskBinding(**review))
    with pytest.raises(ProofCoverageError):
        validate_mapping_review_scope(replace(scope, acceptance=scope.acceptance[:-1]),
                                      intake=intake, expected_review_task=TaskBinding(**review))


def test_committed_ordinary_review_labels_never_supply_human_or_mapping_authority(source_package):
    package = source_package
    incoming = package["declaration"]
    # Adversarial labels name the supplied historical ordinary review. This does
    # not create a decision, an approval carrier, or a selected review lineage.
    for document in (incoming["contract"], incoming["mapping"]):
        document["provenance"].update(authority_ref="copied-Human-approval-label",
                                      review_ref="REVIEW-381-001")
    repin_contract(incoming["contract"], incoming["mapping"], incoming["task"])
    for role in ("contract", "mapping"):
        (package["repo"] / PATHS[role]).write_bytes(encoded(incoming[role]))
    package["main"] = commit_fixture_state(package["repo"], paths=[PATHS["contract"], PATHS["mapping"]],
        message="Adversarial provenance labels only", user_name="Source Fixture", user_email="source@example.invalid",
        remote=package["remote"], remote_ref="refs/heads/main")
    for role in ("contract", "mapping"):
        source = locator(package["repo"], package["main"], PATHS[role])
        package["sources"][role] = source
        incoming["sources"][role] = dict(source_sha=source.source_sha, blob_sha=source.blob_sha)
    value = bind(package)
    assert value.source_status == "SOURCE_CONSISTENT"
    assert value.human_intent == value.reviewer_authority == "NOT_ESTABLISHED"
    assert value.lineage is None and not value.production_eligible and not value.execution_authorized


@pytest.mark.parametrize("fault", ["task", "revision", "digest", "acceptance", "candidate",
    "conditions", "copied-labels", "contract-swap", "source-role-swap"])
def test_actual_sources_reject_coherent_incoming_substitutions(source_package, fault):
    package = source_package
    incoming = package["declaration"]
    contract, mapping = incoming["contract"], incoming["mapping"]
    if fault == "task":
        incoming["task"]["task_id"] = contract["task"]["task_id"] = "TASK-OTHER"
    elif fault == "revision":
        incoming["task"]["revision"] = contract["task"]["revision"] = 2
    elif fault == "digest":
        incoming["task"]["envelope_digest"] = contract["task"]["envelope_digest"] = "0" * 64
    elif fault == "acceptance":
        incoming["task"]["acceptance_ids"] = contract["task"]["acceptance_ids"] = ["AC1"]
        contract["obligations"] = [item for item in contract["obligations"] if item["acceptance_ids"] == ["AC1"]]
        ids = {item["id"] for item in contract["obligations"]}
        mapping["entries"] = [item for item in mapping["entries"] if item["obligation_id"] in ids]
        used = {item["proof_id"] for item in mapping["entries"]}
        mapping["proofs"] = [item for item in mapping["proofs"] if item["id"] in used]
    elif fault == "candidate":
        incoming["candidate_sha"] = RUN_BASE
        for item in contract["obligations"] + mapping["proofs"]:
            item["conditions"]["candidate_sha"] = RUN_BASE
    elif fault == "conditions":
        contract["obligations"][0]["conditions"]["integration_ref"] = "forged-integration-r1"
        mapping["proofs"][0]["conditions"]["integration_ref"] = "forged-integration-r1"
    elif fault == "copied-labels":
        incoming["sources"]["mapping"] = dict(incoming["sources"]["contract"])
    elif fault == "contract-swap":
        contract["id"] = "SWAPPED-CONTRACT"
    else:
        sources = package["sources"]
        sources["contract"], sources["mapping"] = sources["mapping"], sources["contract"]
        for role in ("contract", "mapping"):
            source = sources[role]
            incoming["sources"][role] = dict(source_sha=source.source_sha, blob_sha=source.blob_sha)
    if fault not in {"copied-labels", "source-role-swap"}:
        repin_contract(contract, mapping, incoming["task"])
    with pytest.raises(transport.ReviewTransportError):
        bind(package)


def test_matching_forged_candidate_labels_cannot_replace_independent_candidate_pin(source_package):
    package = source_package
    incoming = package["declaration"]
    incoming["candidate_sha"] = RUN_BASE
    for item in incoming["contract"]["obligations"] + incoming["mapping"]["proofs"]:
        item["conditions"]["candidate_sha"] = RUN_BASE
    repin_contract(incoming["contract"], incoming["mapping"], incoming["task"])
    binding = transport.bind_canonical_semantic_proof_sources(package["repo"],
        encoded({**incoming, "candidate_sha": TARGET, "contract": json.loads(committed(PATHS["contract"])),
                 "mapping": json.loads(committed(PATHS["mapping"]))}),
        expected_remote_url=package["remote"].as_posix(), expected_main_sha=package["main"],
        expected_candidate_sha=TARGET, sources=package["sources"])
    intake = binding.intake
    with pytest.raises(ProofCoverageError):
        validate_semantic_proof_intake(incoming, expected_task=task_binding(committed(TARGET_PATH, TARGET)),
            expected_candidate_sha=TARGET, expected_sources=incoming["sources"],
            expected_contract=asdict(intake.contract_pin), expected_mapping=asdict(intake.mapping_pin))


@pytest.mark.parametrize("role", ["task", "contract", "mapping"])
def test_changed_current_source_cannot_keep_matching_ancestor_labels(source_package, role):
    package = source_package
    path = package["repo"] / PATHS[role]
    path.write_bytes(path.read_bytes() + b" \n")
    package["main"] = commit_fixture_state(package["repo"], paths=[PATHS[role]],
        message="Changed current bytes", user_name="Source Fixture", user_email="source@example.invalid",
        remote=package["remote"], remote_ref="refs/heads/main")
    with pytest.raises(transport.ReviewTransportError):
        bind(package)


def test_legacy_target_cannot_be_claimed_as_future_semantic_task_readiness(source_package):
    with pytest.raises(transport.ReviewTransportError, match="prospective"):
        bind(source_package, reader=transport.read_semantic_task_readiness)


def test_mapping_review_delta_is_separate_from_historical_target_delta(source_package):
    task = parse_task(committed(REVIEW_TASK_PATH, RUN_BASE).decode())
    assert set(task.scope.modify) == MODIFIED
    assert TARGET_PATH in task.scope.inspect and TARGET_PATH not in task.scope.modify
    # TASK-382's reviewed delta is immutable; later TASKs may change production
    # readers and this regression without changing that historical subject.
    assert git(REPO, "diff", "--name-only", RUN_BASE, PUBLISHED).splitlines() == sorted(MODIFIED)
    assert git(REPO, "diff", "--name-only", RUN_BASE, PUBLISHED, "--", TARGET_PATH) == ""
    for path in ("src/aios_renew/task.py", "src/aios_renew/proof_coverage_contract.py",
                 "src/aios_renew/review_transport.py"):
        assert committed(path, PUBLISHED) == committed(path, TARGET)
    package = source_package
    assert package["main"] != TARGET and package["base"] != RUN_BASE
    assert set(git(package["repo"], "diff", "--name-only", package["base"], package["main"]).splitlines()) == {
        PATHS["contract"], PATHS["mapping"]}
    assert package["sources"]["mapping"].source_sha == package["main"]
    # Selecting the older target as the mapping source is a false role substitution.
    sources = dict(package["sources"])
    sources["mapping"] = replace(sources["mapping"], source_sha=package["base"])
    with pytest.raises(transport.ReviewTransportError):
        bind(package, sources=sources)


def snapshot_without_objects(repo):
    return {path.relative_to(repo).as_posix(): path.read_bytes() for path in repo.rglob("*")
            if path.is_file() and not path.relative_to(repo).as_posix().startswith((".git/objects/", "objects/"))}


def test_unselected_actual_pair_reader_only_observes_git_and_changes_no_state(source_package, monkeypatch):
    package = source_package
    before = snapshot_without_objects(package["repo"]), snapshot_without_objects(package["remote"])
    real = subprocess.run

    def read_only(argv, *args, **kwargs):
        assert Path(argv[0]).stem.lower() == "git"
        tail = list(argv[3:])
        if tail[0] == "--no-replace-objects":
            tail = tail[1:]
            while tail[0] == "-c":
                tail = tail[2:]
        assert tail[0] in {"symbolic-ref", "config", "remote", "ls-remote", "fetch", "rev-parse", "cat-file", "ls-tree"}
        if tail[0] == "config":
            assert tail[1].startswith("--get")
        if tail[0] == "fetch":
            assert {"--no-tags", "--no-write-fetch-head", "--refmap=", "--no-auto-maintenance",
                    "--no-recurse-submodules"} <= set(tail)
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", read_only)
    assert bind(package).reviewer_authority == "NOT_ESTABLISHED"
    assert (snapshot_without_objects(package["repo"]), snapshot_without_objects(package["remote"])) == before
