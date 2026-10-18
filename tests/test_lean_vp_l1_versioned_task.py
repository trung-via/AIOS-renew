"""Future TASK fixtures only. No live consent, proof PASS or policy admission.

Real local immutable Git/source/review records and the existing authenticated
Issue reader are used; only its HTTP adapter is synthetic. No authority reader,
coverage validator, source reader or readiness result is replaced.
"""

import copy
import hashlib
import subprocess
from dataclasses import FrozenInstanceError

import pytest
import yaml

from aios_renew import review_transport as transport
from aios_renew.authoring_ingress import AuthoringIngressError, execute_ingress, parse_envelope
from aios_renew.task import SEMANTIC_TASK_SCHEMA, TaskValidationError, parse_semantic_task, parse_task
from tests import test_lean_vp_l1_review_authority as review_fixtures
from tests.test_lean_vp_l1_human_selection import (
    materialize_synthetic_selection, selection_fixture, selection_ref,
)
from tests.test_lean_vp_l1_review_authority import RUN_ID, fixture_ref, install_decision
from tests.test_lean_vp_l1_source_binding import PATHS, TASK, commit, encoded, git, locator, state_without_objects
from tests.test_proof_coverage_contract import CANDIDATE, contract, mapping_for, obligation


def future_document():
    data = yaml.safe_load(TASK)
    del data["verification"]
    data["schema"] = SEMANTIC_TASK_SCHEMA
    obligations = contract(obligation("integration-proof", acceptance_ids=["AC2"]))["obligations"]
    obligations[1]["conditions"].update(
        fixture_refs=["integration-fixture-v1"], integration_ref="integration-v1",
        profile_ref="parallel-profile-v1", worker_mode="parallel", workers=2,
        concurrency_ref="concurrent-v1", ordering_ref="integration-order-v1")
    data["proof_obligations"] = [{key: value for key, value in item.items() if key != "provenance"}
                                 for item in obligations]
    return data


def task_bytes(data):
    return yaml.safe_dump(data, sort_keys=False).encode()


def versioned_package(tmp_path, monkeypatch, *, selected=True):
    raw = task_bytes(future_document())
    # Change only the synthetic target TASK fixture, never the legacy decoder.
    with monkeypatch.context() as fixture_patch:
        fixture_patch.setattr(review_fixtures, "TASK", raw)
        package = selection_fixture(tmp_path, monkeypatch, inspect_contract_only=True)
    package["task_bytes"] = raw
    if selected:
        package["selection_sha"] = materialize_synthetic_selection(package)
    return package


def readiness(package, **overrides):
    arguments = dict(expected_remote_url=package["remote"].as_posix(),
                     expected_main_sha=package["main"], expected_candidate_sha=CANDIDATE,
                     sources=package["sources"])
    arguments.update(overrides)
    return transport.read_semantic_task_readiness(package["repo"], encoded(package["declaration"]), **arguments)


def assert_nonoperative(value):
    assert value.structure == "STRUCTURALLY_COMPLETE"
    assert value.source_consistency == "SOURCE_CONSISTENT"
    assert value.source_currentness == "ESTABLISHED_FOR_OBSERVATION"
    assert value.canonical_verification == value.evidence_applicability == "UNESTABLISHED"
    assert value.future_version_admission == "NOT_ADMITTED"
    assert value.ka01_g3 == value.ka01_g6 == value.production_opt_in == "NOT_ESTABLISHED"
    assert value.ka01 == "NOT_ACTIVATED"
    assert not value.production_eligible and not value.execution_authorized


def test_complete_independently_authenticated_future_sources_and_authority(tmp_path, monkeypatch):
    package = versioned_package(tmp_path, monkeypatch)
    value = readiness(package)
    assert_nonoperative(value)
    assert value.human_selection == "AUTHENTICATED_SELECTION"
    assert value.reviewer_authority == "REVIEW_AUTHORITY_ESTABLISHED"
    assert value.publisher_authority == "EXACT_SOURCE_PUBLICATION_ESTABLISHED"
    assert value.authority.lineage.human_selection_sha == package["selection_sha"]
    assert value.authority.lineage.published_sha == package["candidate"]
    assert value.authority.source_binding.intake.coverage.contract.task.envelope_digest == hashlib.sha256(
        package["task_bytes"]).hexdigest()
    assert {item.conditions.workers for item in value.task.proof_obligations} == {1, 2}
    with pytest.raises(FrozenInstanceError):
        value.task.goal = "changed"
    with pytest.raises(FrozenInstanceError):
        value.task.proof_obligations[0].conditions.workers = 99


@pytest.mark.parametrize("field", ["schema", "task_id", "acceptance", "proof_obligations"])
def test_future_decoder_requires_explicit_complete_envelope(field):
    data = future_document()
    del data[field]
    with pytest.raises(TaskValidationError):
        parse_semantic_task(task_bytes(data))


@pytest.mark.parametrize("version", [True, 1, None, "task-semantic-v2", "minimum-sufficient-v2", [SEMANTIC_TASK_SCHEMA]])
def test_future_decoder_rejects_wrong_or_ambiguous_version(version):
    data = future_document()
    data["schema"] = version
    with pytest.raises(TaskValidationError):
        parse_semantic_task(task_bytes(data))


@pytest.mark.parametrize("field", ["verification", "executor", "return_affinity", "human_selection",
    "contract_digest", "mapping_pin", "sources", "envelope_digest", "policy", "budget", "ka01"])
def test_future_task_cannot_carry_runtime_authority_or_circular_source_pins(field):
    data = future_document()
    data[field] = "self-declared"
    with pytest.raises(TaskValidationError, match="unknown"):
        parse_semantic_task(task_bytes(data))


@pytest.mark.parametrize("fault", ["duplicate-ac", "partial-ac", "unknown-ac", "duplicate-obligation",
    "missing-obligation", "missing-condition", "conflicting-claim", "provenance", "candidate", "revision"])
def test_future_decoder_rejects_partial_or_conflicting_requirements(fault):
    data = future_document()
    obligations = data["proof_obligations"]
    if fault == "duplicate-ac":
        data["acceptance"].append(copy.deepcopy(data["acceptance"][0]))
    elif fault == "partial-ac":
        data["acceptance"].pop()
    elif fault == "unknown-ac":
        obligations[0]["acceptance_ids"] = ["OTHER"]
    elif fault == "duplicate-obligation":
        obligations.append(copy.deepcopy(obligations[0]))
    elif fault == "missing-obligation":
        obligations.pop()
    elif fault == "missing-condition":
        del obligations[0]["conditions"]["collection_ref"]
    elif fault == "conflicting-claim":
        obligations[1]["claim"]["text"] = "Conflicting text for the same identity."
    elif fault == "provenance":
        obligations[0]["provenance"] = {"authority_ref": "human-approved"}
    elif fault == "candidate":
        obligations[1]["conditions"]["candidate_sha"] = "e" * 40
    else:
        data["revision"] = True
    with pytest.raises(TaskValidationError):
        parse_semantic_task(task_bytes(data))


@pytest.mark.parametrize("command", ["pytest tests/test_task.py", "python -c pass", "npm test",
    "bash -c true", "./proof.sh", "echo PASS", "cargo test", "valid; whoami", "valid | sh",
    "proof.sh", "bin/check.exe --flag", "custom-check --flag", "valid $(whoami)", "valid `whoami`",
    "valid && echo PASS", "valid > file", "__import__('os').system('whoami')", "iex command"])
def test_future_semantic_claims_reject_shell_shaped_commands(command):
    data = future_document()
    data["proof_obligations"][0]["claim"]["text"] = command
    with pytest.raises(TaskValidationError):
        parse_semantic_task(task_bytes(data))


@pytest.mark.parametrize("suffix", [b"schema: task-semantic-v1\n", b"---\nschema: task-semantic-v1\n"])
def test_future_decoder_rejects_duplicate_fields_or_documents(suffix):
    with pytest.raises(TaskValidationError):
        parse_semantic_task(task_bytes(future_document()) + suffix)


@pytest.mark.parametrize("raw", [b"schema: &version task-semantic-v1\n", b"schema: !!str task-semantic-v1\n",
    b"\xef\xbb\xbfschema: task-semantic-v1\n", b"\xff", b"[]", b"x" * 262145, "task-semantic-v1"])
def test_future_decoder_rejects_unsupported_encoding_graphs_and_shapes(raw):
    with pytest.raises(TaskValidationError):
        parse_semantic_task(raw)


def test_future_decoder_has_no_io_and_retains_normalized_immutable_meaning(monkeypatch):
    import builtins
    import socket
    from pathlib import Path
    data = future_document()
    raw = task_bytes(data)

    def forbidden(*args, **kwargs):
        raise AssertionError("prospective decoder performed I/O")

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(Path, "read_bytes", forbidden)
        guard.setattr(subprocess, "run", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(socket, "socket", forbidden)
        parsed = parse_semantic_task(raw)
    data["acceptance"].reverse()
    data["proof_obligations"].reverse()
    for item in data["proof_obligations"]:
        item["conditions"]["fixture_refs"].reverse()
    assert parse_semantic_task(task_bytes(data)) == parsed
    data["proof_obligations"][0]["conditions"]["fixture_refs"].append("later-mutation")
    assert all("later-mutation" not in item.conditions.fixture_refs for item in parsed.proof_obligations)


def test_no_legacy_reinterpretation_and_no_implicit_ingress(tmp_path, monkeypatch):
    raw = task_bytes(future_document())
    with pytest.raises(TaskValidationError):
        parse_semantic_task(TASK)
    with pytest.raises(TaskValidationError):
        parse_task(raw.decode())
    package = versioned_package(tmp_path, monkeypatch)
    # Both historical source/readiness APIs keep the legacy TASK shape.
    with pytest.raises(transport.ReviewTransportError):
        review_fixtures.bind(package)
    envelope = parse_envelope(encoded(dict(format="AIOS_INGRESS_ENVELOPE", version=1, operation="AUTHOR_TASK",
        identity=dict(task_id="TASK-SYNTHETIC"), expected_state=dict(expected_main_sha=package["main"]),
        payload=raw.decode())))
    before = state_without_objects(package["repo"]), state_without_objects(package["remote"])
    with pytest.raises(AuthoringIngressError, match="invalid TASK contract"):
        execute_ingress(envelope, repo=package["repo"])
    # The PRIMARY loader is a read-only rejection boundary, without dispatch.
    from aios_renew.operator import OperatorError, load_task
    with pytest.raises(OperatorError, match="invalid TASK"):
        load_task(package["repo"], "TASK-SYNTHETIC")
    assert (state_without_objects(package["repo"]), state_without_objects(package["remote"])) == before


def test_no_selection_even_with_unrelated_pass_and_declared_approval_labels(tmp_path, monkeypatch):
    package = versioned_package(tmp_path, monkeypatch, selected=False)
    value = readiness(package)
    assert_nonoperative(value)
    assert value.human_selection == value.reviewer_authority == value.publisher_authority == "NOT_ESTABLISHED"
    assert value.authority.lineage is None


@pytest.mark.parametrize("fault", ["untrusted-human", "missing-review", "partial-review", "swapped-run",
    "changed-decision", "conflicting-selection", "wrong-selection", "stale-policy", "copied-provenance",
    "unrelated-pass"])
def test_future_selection_and_review_are_independently_required(tmp_path, monkeypatch, fault):
    package = versioned_package(tmp_path, monkeypatch)
    if fault == "untrusted-human":
        package["issue"]["user"]["login"] = "untrusted"
    elif fault == "missing-review":
        (package["remote"] / f"refs/heads/aios/review-decision/{RUN_ID}").unlink()
    elif fault == "partial-review":
        package["review"]["acceptance"].pop("MR2")
        install_decision(package)
    elif fault == "swapped-run":
        package["request"]["payload"]["run"]["id"] = "RUN-377-002"
        package["issue"]["body"] = encoded(package["request"]).decode()
        materialize_synthetic_selection(package)
    elif fault == "changed-decision":
        install_decision(package, extra={PATHS["contract"]: b"{}"})
    elif fault == "unrelated-pass":
        package["review"]["review_id"] = "REVIEW-377-001"
        install_decision(package)
    elif fault == "conflicting-selection":
        ref = selection_ref(package)
        # Keep the exact ref packed so the loose descendant has no file/directory collision.
        git(package["remote"], "pack-refs", "--all", "--prune")
        competing_sha = fixture_ref(package, ref + "/competing", {"unrelated.txt": b"synthetic"})
        advertised = git(package["repo"], "ls-remote", "--refs", package["remote"].as_posix(), ref, ref + "/*")
        assert {name: sha for sha, name in (line.split() for line in advertised.splitlines())} == {
            ref: package["selection_sha"], ref + "/competing": competing_sha}
    elif fault == "wrong-selection":
        package["request"]["payload"]["scope"]["target_task"]["revision"] += 1
        package["issue"]["body"] = encoded(package["request"]).decode()
        materialize_synthetic_selection(package)
    elif fault == "copied-provenance":
        record = transport._l1_selection_record(package["repo"], package["selection_sha"])
        record["provenance"]["actor_id"] += 1
        import json
        raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        fixture_ref(package, selection_ref(package),
            {**package["candidate_files"], transport.L1_SELECTION_PATH: raw}, parents=(package["main"],))
    else:
        (package["repo"] / ".ai/brain-ingress-carriers.yaml").write_bytes(b"{}")
        package["main"] = commit(package["repo"], package["remote"], [".ai/brain-ingress-carriers.yaml"])
    value = readiness(package)
    assert_nonoperative(value)
    assert value.reviewer_authority == value.publisher_authority == "NOT_ESTABLISHED"
    assert value.authority.lineage is None
    if fault == "conflicting-selection":
        assert value.human_selection == "NOT_ESTABLISHED"
        with pytest.raises(transport.ReviewTransportError, match="ambiguous L1 Human selection"):
            transport._l1_authenticated_mapping_review_selection(package["repo"], value.authority.source_binding)


def test_wrong_immutable_future_version_is_rejected_at_the_source_boundary(tmp_path, monkeypatch):
    package = versioned_package(tmp_path, monkeypatch)
    data = future_document()
    data["schema"] = "task-semantic-v2"
    (package["repo"] / PATHS["task"]).write_bytes(task_bytes(data))
    package["main"] = commit(package["repo"], package["remote"], [PATHS["task"]])
    package["sources"]["task"] = locator(package["repo"], package["main"], PATHS["task"])
    with pytest.raises(transport.ReviewTransportError, match="prospective"):
        readiness(package)


@pytest.mark.parametrize("fault", ["task", "revision", "acceptance", "candidate", "circular-pin"])
def test_incoming_self_consistent_substitutions_cannot_replace_actual_sources(tmp_path, monkeypatch, fault):
    package = versioned_package(tmp_path, monkeypatch)
    declaration = package["declaration"]
    data = declaration["contract"]
    if fault == "task":
        data["task"]["task_id"] = "TASK-OTHER"
    elif fault == "revision":
        data["task"]["revision"] += 1
    elif fault == "acceptance":
        data["task"]["acceptance_ids"] = ["AC1"]
        data["obligations"].pop()
    elif fault == "candidate":
        for item in data["obligations"]:
            item["conditions"]["candidate_sha"] = "e" * 40
        declaration["candidate_sha"] = "e" * 40
    else:
        data["task"]["envelope_digest"] = declaration["mapping"]["contract_digest"]
    declaration["task"] = copy.deepcopy(data["task"])
    declaration["mapping"] = mapping_for(data)
    with pytest.raises(transport.ReviewTransportError):
        readiness(package)


@pytest.mark.parametrize("role", ["task", "contract", "mapping"])
def test_changed_current_semantic_sources_cannot_reuse_ancestor_bindings(tmp_path, monkeypatch, role):
    package = versioned_package(tmp_path, monkeypatch)
    target = package["repo"] / PATHS[role]
    target.write_bytes(target.read_bytes() + b" \n")
    package["main"] = commit(package["repo"], package["remote"], [PATHS[role]])
    with pytest.raises(transport.ReviewTransportError):
        readiness(package)


def test_current_contract_cannot_omit_a_distinguishing_task_condition(tmp_path, monkeypatch):
    package = versioned_package(tmp_path, monkeypatch)
    data = package["declaration"]["contract"]
    data["obligations"][1]["conditions"]["integration_ref"] = "weakened-integration"
    mapping = mapping_for(data)
    for role, document in (("contract", data), ("mapping", mapping)):
        (package["repo"] / PATHS[role]).write_bytes(encoded(document))
    package["main"] = commit(package["repo"], package["remote"])
    for role in ("contract", "mapping"):
        source = locator(package["repo"], package["main"], PATHS[role])
        package["sources"][role] = source
        package["declaration"]["sources"][role] = dict(source_sha=source.source_sha, blob_sha=source.blob_sha)
    package["declaration"]["mapping"] = mapping
    with pytest.raises(transport.ReviewTransportError, match="inconsistent"):
        readiness(package)


@pytest.mark.parametrize("race", ["main", "selection", "decision", "remote"])
def test_future_readiness_rechecks_current_subjects(tmp_path, monkeypatch, race):
    package = versioned_package(tmp_path, monkeypatch)
    real = transport._l1_mapping_review_lineage

    def observe(*args):
        value = real(*args)
        if race == "main":
            (package["remote"] / "refs/heads/main").write_text(package["base"] + "\n", encoding="ascii")
        elif race == "remote":
            config = package["repo"] / ".git/config"
            config.write_text(config.read_text().replace(package["remote"].as_posix(), "untrusted"))
        else:
            ref = selection_ref(package) if race == "selection" else f"refs/heads/aios/review-decision/{RUN_ID}"
            (package["remote"] / ref).write_text(package["base"] + "\n", encoding="ascii")
        return value

    monkeypatch.setattr(transport, "_l1_mapping_review_lineage", observe)
    if race in {"main", "remote"}:
        with pytest.raises(transport.ReviewTransportError):
            readiness(package)
    else:
        value = readiness(package)
        assert value.reviewer_authority == value.publisher_authority == "NOT_ESTABLISHED"


def test_future_reader_has_no_process_or_repository_side_effects(tmp_path, monkeypatch):
    package = versioned_package(tmp_path, monkeypatch)
    before = state_without_objects(package["repo"]), state_without_objects(package["remote"])
    real = subprocess.run

    def read_only(argv, *args, **kwargs):
        assert argv[0] == "git"
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
    assert readiness(package).reviewer_authority == "REVIEW_AUTHORITY_ESTABLISHED"
    assert (state_without_objects(package["repo"]), state_without_objects(package["remote"])) == before
