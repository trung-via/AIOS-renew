"""Synthetic mapping-review lineage only; these fixtures confer no live authority.

The public API has no trust-source argument. Only this isolated fixture replaces
the closed private Human-selection reader, with separately pinned expected facts.
No live provider, Executor, Publisher or verification command is invoked.
"""

import copy
import hashlib
import subprocess
from dataclasses import FrozenInstanceError, replace

import pytest

import aios_renew.review_transport as transport
from aios_renew.proof_coverage_contract import (
    DeclarationPin, MappingReviewAcceptance, MappingReviewScope, TaskBinding,
    proof_mapping_digest, validate_proof_contract,
)
from aios_renew.review_transport import (
    AuthenticatedSemanticSource, ReviewTransportError, bind_canonical_semantic_review_authority,
)
from tests.git_fixture_support import _write_object, _write_tree, materialize_git_baseline
from tests.test_lean_vp_l1_source_binding import (
    PATHS, TASK, commit, encoded, locator, state_without_objects,
)
from tests.test_proof_coverage_contract import CANDIDATE, contract, mapping_for, obligation


REVIEW_TASK_ID = "TASK-MAPPING-SYNTHETIC"
REVIEW_TASK_PATH = f".ai/tasks/{REVIEW_TASK_ID}.yaml"
RUN_ID = "RUN-MAPPING-SYNTHETIC-001"
REVIEW_ID = "REVIEW-MAPPING-SYNTHETIC-001"
COMMAND = "python -m pytest tests/test_synthetic_mapping.py"
REVIEW_TASK = b"""task_id: TASK-MAPPING-SYNTHETIC
revision: 1
goal: Review the exact synthetic semantic mapping.
problem: Exact mapping scope needs independent semantic judgment.
assumptions: [Synthetic only]
scope:
  inspect: [.ai/tasks/TASK-SYNTHETIC.yaml]
  modify: [proof/contract.json, proof/mapping.json]
non_goals: [Production activation]
constraints:
  hard: [Synthetic isolated subject only]
acceptance:
  - id: MR1
    condition: Assess the isolated semantic obligation and mapping.
  - id: MR2
    condition: Assess the integration semantic obligation and mapping.
verification:
  policy: minimum-sufficient-v1
  required: [python -m pytest tests/test_synthetic_mapping.py]
"""


def digest(content):
    return hashlib.sha256(content).hexdigest()


def fixture_ref(package, ref, files, *, parents=()):
    """Materialize only synthetic local authority fixtures, without a Git writer."""
    for objects in (package["repo"] / ".git/objects", package["remote"] / "objects"):
        root = {}
        for path, content in files.items():
            node = root
            parts = path.split("/")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = bytes.fromhex(_write_object(objects, "blob", content))
        tree = _write_tree(objects, root)
        headers = f"tree {tree}\n" + "".join(f"parent {parent}\n" for parent in parents)
        identity = "Synthetic Authority <synthetic@example.invalid> 946684800 +0000"
        body = (headers + f"author {identity}\ncommitter {identity}\n\nsynthetic fixture\n").encode()
        sha = _write_object(objects, "commit", body)
    target = package["remote"] / ref
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(sha + "\n", encoding="ascii")
    return sha


def install_artifacts(package, *, repin=True, run_bytes=None, result_bytes=None, extra=None):
    run_bytes = encoded(package["run"]) if run_bytes is None else run_bytes
    files = {".ai/transport/run.json": run_bytes,
             ".ai/transport/result.json": encoded(package["result"]) if result_bytes is None else result_bytes}
    files.update(extra or {})
    sha = fixture_ref(package, f"refs/heads/aios/artifacts/{RUN_ID}", files)
    if repin:
        package["selection"] = replace(package["selection"], artifacts_sha=sha, run_sha256=digest(run_bytes))
    return sha


def install_decision(package, *, repin=True, review_bytes=None, extra=None, parents=None):
    files = dict(package["candidate_files"])
    files[f".ai/reviews/{REVIEW_ID}.yaml"] = encoded(package["review"]) if review_bytes is None else review_bytes
    files.update(extra or {})
    sha = fixture_ref(package, f"refs/heads/aios/review-decision/{RUN_ID}", files,
                      parents=(package["candidate"],) if parents is None else parents)
    if repin:
        package["selection"] = replace(package["selection"], decision_sha=sha)
    return sha


@pytest.fixture
def reviewed_package(tmp_path):
    data = contract(obligation("integration-proof", acceptance_ids=["AC2"]))
    data["task"] = dict(task_id="TASK-SYNTHETIC", revision=1,
                        envelope_digest=digest(TASK), acceptance_ids=["AC1", "AC2"])
    data["obligations"][1]["conditions"].update(
        fixture_refs=["integration-fixture-v1"], integration_ref="integration-v1",
        profile_ref="parallel-profile-v1", worker_mode="parallel", workers=2,
        concurrency_ref="concurrent-v1", ordering_ref="integration-order-v1")
    mapping = mapping_for(data)
    repo, remote, base = materialize_git_baseline(
        tmp_path / "synthetic", files={PATHS["task"]: TASK, REVIEW_TASK_PATH: REVIEW_TASK,
            "subject.txt": b"synthetic only\n", PATHS["contract"]: encoded(data)},
        user_name="Synthetic", user_email="synthetic@example.invalid", commit_message="synthetic TASKs/contract")
    candidate_files = {PATHS["task"]: TASK, REVIEW_TASK_PATH: REVIEW_TASK,
                       "subject.txt": b"synthetic only\n",
                       PATHS["contract"]: encoded(data), PATHS["mapping"]: encoded(mapping)}
    for path in (PATHS["contract"], PATHS["mapping"]):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(candidate_files[path])
    candidate = commit(repo, remote)
    sources = {role: locator(repo, candidate if role == "mapping" else base, path)
               for role, path in PATHS.items()}
    declaration = dict(schema="task-semantic-proof-intake-v1", task=copy.deepcopy(data["task"]),
                       candidate_sha=CANDIDATE, sources={role: dict(
                           source_sha=source.source_sha, blob_sha=source.blob_sha)
                           for role, source in sources.items()}, contract=data, mapping=mapping)
    parsed = validate_proof_contract(data, expected_task=data["task"])
    scope = MappingReviewScope(1,
        TaskBinding(REVIEW_TASK_ID, 1, digest(REVIEW_TASK), ("MR1", "MR2")),
        parsed.task, CANDIDATE, DeclarationPin(parsed.id, parsed.revision, parsed.digest),
        DeclarationPin(mapping["id"], mapping["revision"], proof_mapping_digest(mapping)),
        (MappingReviewAcceptance("MR1", ("AC1",), ("proof-unit",)),
         MappingReviewAcceptance("MR2", ("AC2",), ("integration-proof",))))
    run = dict(run_id=RUN_ID, task=dict(id=REVIEW_TASK_ID, revision=1), executor="codex",
               base_sha=base, head_sha=None, workspace="synthetic", status="ACTIVE",
               return_affinity=dict(kind="LEGACY_REPOSITORY_DEFAULT_ROUTE"))
    result = dict(result=dict(head_sha=candidate, claims=[dict(id="synthetic-mapping-claim",
        satisfies=["MR1", "MR2"], claim="The exact synthetic mapping was assessed.", evidence=["SYNTHETIC-E1"])],
        changed_files=[PATHS["mapping"]], unresolved=[]), evidence=[dict(
        evidence_id="SYNTHETIC-E1", run_id=RUN_ID, subject_sha=candidate, type="VERIFICATION",
        source=dict(command=COMMAND), result=dict(exit_code=0, summary="Synthetic fixture, never executed"),
        raw=dict(path="synthetic-only.raw"))])
    review = dict(review_id=REVIEW_ID, reviewed_sha=candidate, mode="PRIMARY", verdict="PASS",
                  acceptance=dict(MR1="PASS", MR2="PASS"), findings=[])
    package = dict(repo=repo, remote=remote, main=candidate, candidate=candidate, base=base,
                   sources=sources, declaration=declaration, candidate_files=candidate_files,
                   run=run, result=result, review=review)
    # Expected Human scope/pins are built independently of any incoming caller
    # declaration; they are not reflected from a call to the binder under test.
    package["selection"] = transport._L1MappingReviewSelection(scope,
        tuple(AuthenticatedSemanticSource(role, sources[role], digest(candidate_files[PATHS[role]]),
                                           len(candidate_files[PATHS[role]]))
              for role in ("task", "contract", "mapping")),
        locator(repo, base, REVIEW_TASK_PATH), RUN_ID, digest(encoded(run)), base, candidate,
        REVIEW_ID, "0" * 40, "0" * 40, candidate)
    candidate_ref = remote / f"refs/heads/aios/review/{RUN_ID}"
    candidate_ref.parent.mkdir(parents=True, exist_ok=True)
    candidate_ref.write_text(candidate + "\n", encoding="ascii")
    install_artifacts(package)
    install_decision(package)
    return package


def bind(package, **overrides):
    arguments = dict(expected_remote_url=package["remote"].as_posix(),
                     expected_main_sha=package["main"], expected_candidate_sha=CANDIDATE,
                     sources=package["sources"])
    arguments.update(overrides)
    return bind_canonical_semantic_review_authority(
        package["repo"], encoded(package["declaration"]), **arguments)


def trust_fixture(package, monkeypatch):
    # Isolated test trust source only: no public selector or persisted authority.
    monkeypatch.setattr(transport, "_l1_authenticated_mapping_review_selection",
                        lambda repo, binding: package["selection"])


def assert_not_established(value):
    assert value.source_status == "SOURCE_CONSISTENT"
    assert value.reviewer_authority == "NOT_ESTABLISHED" and value.lineage is None
    assert not value.production_eligible and not value.execution_authorized


def test_public_boundary_is_closed_even_with_ordinary_canonical_run_pass(reviewed_package):
    value = bind(reviewed_package)
    assert_not_established(value)
    assert value.reason == "HUMAN_MAPPING_SELECTION_UNAVAILABLE"


def test_runtime_active_run_with_null_head_binds_canonical_result_candidate(reviewed_package, monkeypatch):
    package = reviewed_package
    run_bytes = encoded(package["run"])
    assert package["run"]["status"] == "ACTIVE" and package["run"]["head_sha"] is None
    assert package["result"]["result"]["head_sha"] == package["candidate"]
    trust_fixture(package, monkeypatch)
    value = bind(package)
    assert value.reviewer_authority == "REVIEW_AUTHORITY_ESTABLISHED"
    assert value.lineage.run_sha256 == digest(run_bytes)
    assert value.lineage.base_sha == package["base"]
    assert value.lineage.reviewed_sha == value.lineage.published_sha == package["candidate"]
    assert value.lineage.result_sha256 == digest(encoded(package["result"]))
    assert encoded(package["run"]) == run_bytes


def test_unchanged_contract_from_base_allows_a_mapping_only_delta(reviewed_package, monkeypatch):
    package = reviewed_package
    changed_files = subprocess.run(
        ["git", "-C", str(package["repo"]), "diff", "--name-only", package["base"], package["candidate"]],
        check=True, capture_output=True, text=True).stdout.splitlines()
    assert changed_files == package["result"]["result"]["changed_files"] == [PATHS["mapping"]]
    contract_source = package["sources"]["contract"]
    assert contract_source == locator(package["repo"], package["base"], PATHS["contract"])
    assert contract_source.blob_sha == locator(package["repo"], package["candidate"], PATHS["contract"]).blob_sha
    trust_fixture(package, monkeypatch)
    value = bind(package)
    assert value.reviewer_authority == "REVIEW_AUTHORITY_ESTABLISHED"
    assert value.source_binding.sources == package["selection"].sources


@pytest.mark.parametrize("changed_files", [[], [PATHS["contract"], PATHS["mapping"]]],
                         ids=["mapping-omitted", "unchanged-contract-claimed"])
def test_result_semantic_changes_must_match_the_git_delta(reviewed_package, monkeypatch, changed_files):
    package = reviewed_package
    package["result"]["result"]["changed_files"] = changed_files
    install_artifacts(package)
    trust_fixture(package, monkeypatch)
    assert_not_established(bind(package))


def test_null_run_head_rejects_swapped_result_and_evidence_candidate(reviewed_package, monkeypatch):
    package = reviewed_package
    (package["repo"] / "subject.txt").write_bytes(b"swapped candidate\n")
    swapped = commit(package["repo"], package["remote"], ["subject.txt"])
    package["main"] = swapped
    package["result"]["result"]["head_sha"] = swapped
    for item in package["result"]["evidence"]:
        item["subject_sha"] = swapped
    install_artifacts(package)
    trust_fixture(package, monkeypatch)
    assert_not_established(bind(package))


def test_exact_independently_selected_fixture_lineage_preserves_all_separate_statuses(reviewed_package, monkeypatch):
    trust_fixture(reviewed_package, monkeypatch)
    value = bind(reviewed_package)
    assert value.reviewer_authority == "REVIEW_AUTHORITY_ESTABLISHED"
    assert value.lineage.run_id == RUN_ID
    assert value.lineage.scope.target_task.acceptance_ids == ("AC1", "AC2")
    assert value.lineage.scope.review_task.acceptance_ids == ("MR1", "MR2")
    assert value.lineage.published_sha == reviewed_package["candidate"]
    assert value.lineage.decision_sha == reviewed_package["selection"].decision_sha
    assert value.semantic_adequacy == "REQUIRES_INDEPENDENT_REVIEWER_JUDGMENT"
    assert value.canonical_verification == value.evidence_applicability == "UNESTABLISHED"
    assert value.future_version_admission == "NOT_ADMITTED"
    assert value.ka01_g3 == value.ka01_g6 == value.production_opt_in == "NOT_ESTABLISHED"
    assert value.production_activation == "NOT_ACTIVATED"
    assert not value.production_eligible and not value.execution_authorized
    with pytest.raises(FrozenInstanceError):
        value.lineage.scope.version = 2
    with pytest.raises(FrozenInstanceError):
        value.lineage = None


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("version", True), ("candidate_sha", "f" * 40),
    ("target_task", TaskBinding("TASK-OTHER", 1, "c" * 64, ("AC1", "AC2"))),
    ("review_task", TaskBinding(REVIEW_TASK_ID, 2, digest(REVIEW_TASK), ("MR1", "MR2"))),
    ("review_task", TaskBinding(REVIEW_TASK_ID, 1, "f" * 64, ("MR1", "MR2"))),
    ("review_task", TaskBinding(REVIEW_TASK_ID, 1, digest(REVIEW_TASK), ("MR1",))),
    ("contract_pin", DeclarationPin("contract-v1", 1, "f" * 64)),
    ("mapping_pin", DeclarationPin("reviewed-map-v1", 1, "f" * 64)),
    ("acceptance", (MappingReviewAcceptance("MR1", ("AC1",), ("proof-unit",)),)),
    ("acceptance", (MappingReviewAcceptance("MR1", ("AC2",), ("proof-unit",)),
                    MappingReviewAcceptance("MR2", ("AC1",), ("integration-proof",)))),
])
def test_exact_scope_version_full_acceptance_and_correspondence_are_required(reviewed_package, monkeypatch, field, value):
    package = reviewed_package
    package["selection"] = replace(package["selection"], scope=replace(package["selection"].scope, **{field: value}))
    trust_fixture(package, monkeypatch)
    assert_not_established(bind(package))


@pytest.mark.parametrize("field,value", [
    ("run_id", "RUN-377-002"), ("review_id", "REVIEW-377-001"),
    ("run_sha256", "f" * 64), ("decision_sha", "f" * 40),
    ("artifacts_sha", "f" * 40), ("reviewed_sha", "f" * 40),
    ("published_sha", "f" * 40), ("base_sha", "f" * 40), ("sources", ()),
])
def test_unrelated_run_review_and_untrusted_pins_cannot_substitute(reviewed_package, monkeypatch, field, value):
    package = reviewed_package
    package["selection"] = replace(package["selection"], **{field: value})
    trust_fixture(package, monkeypatch)
    assert_not_established(bind(package))


@pytest.mark.parametrize("ref", ["review", "review-decision", "artifacts"])
def test_missing_existing_lineage_is_not_established(reviewed_package, monkeypatch, ref):
    package = reviewed_package
    (package["remote"] / f"refs/heads/aios/{ref}/{RUN_ID}").unlink()
    trust_fixture(package, monkeypatch)
    assert_not_established(bind(package))


@pytest.mark.parametrize("fault", ["verdict", "failed-ac", "partial-ac", "findings", "wrong-subject",
                                   "wrong-review", "delta", "forged-reviewer", "duplicate", "oversized", "parents",
                                   "swapped-source"])
def test_forged_incomplete_contradictory_or_unrecognized_decisions_fail_closed(reviewed_package, monkeypatch, fault):
    package = reviewed_package
    review = package["review"]
    options = {}
    if fault == "verdict":
        review["verdict"] = "BLOCKED"
    elif fault == "failed-ac":
        review["acceptance"]["MR2"] = "FAIL"
    elif fault == "partial-ac":
        del review["acceptance"]["MR2"]
    elif fault == "findings":
        review["findings"] = [dict(id="F1", basis="MR1", action="CODE_FIX", location="mapping",
                                    issue="outstanding", expected="resolve")]
    elif fault == "wrong-subject":
        review["reviewed_sha"] = package["base"]
    elif fault == "wrong-review":
        review["review_id"] = "REVIEW-377-001"
    elif fault == "delta":
        review["mode"] = "DELTA"
    elif fault == "forged-reviewer":
        review["reviewer"] = "independent-Human-authorized-reviewer"
    elif fault == "duplicate":
        options["review_bytes"] = b'{"verdict":"PASS",' + encoded(review)[1:]
    elif fault == "oversized":
        options["review_bytes"] = b" " * (transport.L1_TASK_MAX_BYTES + 1)
    elif fault == "parents":
        options["parents"] = (package["base"],)
    elif fault == "swapped-source":
        options["extra"] = {PATHS["mapping"]: b"{}"}
    install_decision(package, **options)
    trust_fixture(package, monkeypatch)
    assert_not_established(bind(package))


@pytest.mark.parametrize("fault", ["other-task", "other-run", "run-head", "result-head", "result-unresolved",
                                   "result-partial", "evidence-subject", "duplicate-run", "correction"])
def test_immutable_run_result_and_existing_proof_lineage_are_cross_bound(reviewed_package, monkeypatch, fault):
    package = reviewed_package
    options = {}
    if fault == "other-task":
        package["run"]["task"]["id"] = "TASK-377"
    elif fault == "other-run":
        package["run"]["run_id"] = "RUN-377-002"
    elif fault == "run-head":
        package["run"]["head_sha"] = package["base"]
    elif fault == "result-head":
        package["result"]["result"]["head_sha"] = package["base"]
    elif fault == "result-unresolved":
        package["result"]["result"]["unresolved"] = ["unassessed mapping"]
    elif fault == "result-partial":
        package["result"]["result"]["claims"][0]["satisfies"] = ["MR1"]
    elif fault == "evidence-subject":
        package["result"]["evidence"][0]["subject_sha"] = package["base"]
    elif fault == "duplicate-run":
        options["run_bytes"] = b'{"run_id":"forged",' + encoded(package["run"])[1:]
    elif fault == "correction":
        options["extra"] = {".ai/transport/repair.json": b"{}"}
    install_artifacts(package, **options)
    trust_fixture(package, monkeypatch)
    assert_not_established(bind(package))


@pytest.mark.parametrize("source", [None, {"reviewer": "trusted", "verdict": "PASS"}, "REVIEW-377-001"])
def test_missing_or_unknown_human_selection_never_uses_caller_labels(reviewed_package, monkeypatch, source):
    monkeypatch.setattr(transport, "_l1_authenticated_mapping_review_selection", lambda *args: source)
    assert_not_established(bind(reviewed_package))


def test_unavailable_human_authority_never_synthesizes_success(reviewed_package, monkeypatch):
    def unavailable(*args):
        raise ReviewTransportError("independent selection unavailable")
    monkeypatch.setattr(transport, "_l1_authenticated_mapping_review_selection", unavailable)
    assert_not_established(bind(reviewed_package))


def test_no_actual_mapping_delta_cannot_be_approved_by_an_ordinary_later_run(reviewed_package, monkeypatch):
    package = reviewed_package
    # RESULT claims a mapping change even though the RUN base already has it.
    assert package["result"]["result"]["changed_files"] == [PATHS["mapping"]]
    package["run"]["base_sha"] = package["candidate"]
    package["selection"] = replace(package["selection"], base_sha=package["candidate"])
    install_artifacts(package)
    trust_fixture(package, monkeypatch)
    assert_not_established(bind(package))


def test_published_ancestor_with_unchanged_semantic_bytes_is_current(reviewed_package, monkeypatch):
    package = reviewed_package
    (package["repo"] / "subject.txt").write_bytes(b"unrelated later publication\n")
    package["main"] = commit(package["repo"], package["remote"], ["subject.txt"])
    trust_fixture(package, monkeypatch)
    assert bind(package).reviewer_authority == "REVIEW_AUTHORITY_ESTABLISHED"


def test_replay_after_semantic_bytes_change_rejects_old_human_source_association(reviewed_package, monkeypatch):
    package = reviewed_package
    # Equivalent JSON meaning still has different immutable source bytes.
    (package["repo"] / PATHS["mapping"]).write_bytes(encoded(package["declaration"]["mapping"]) + b"\n")
    package["main"] = commit(package["repo"], package["remote"], [PATHS["mapping"]])
    new = locator(package["repo"], package["main"], PATHS["mapping"])
    package["sources"]["mapping"] = new
    package["declaration"]["sources"]["mapping"] = dict(source_sha=new.source_sha, blob_sha=new.blob_sha)
    trust_fixture(package, monkeypatch)
    assert_not_established(bind(package))


@pytest.mark.parametrize("moved", ["review", "decision", "artifacts", "human", "main"])
def test_changed_approval_or_main_during_observation_fails_closed(reviewed_package, monkeypatch, moved):
    package = reviewed_package
    trust_fixture(package, monkeypatch)
    real = transport._exact_remote_refs
    calls = 0

    def observe(repo, url, *patterns):
        nonlocal calls
        if any("review-decision" in pattern for pattern in patterns):
            calls += 1
            if calls == 2:
                if moved == "human":
                    package["selection"] = None
                elif moved == "main":
                    (package["remote"] / "refs/heads/main").write_text(package["base"] + "\n", encoding="ascii")
                else:
                    ref = "review-decision" if moved == "decision" else moved
                    (package["remote"] / f"refs/heads/aios/{ref}/{RUN_ID}").write_text(package["base"] + "\n", encoding="ascii")
        return real(repo, url, *patterns)

    monkeypatch.setattr(transport, "_exact_remote_refs", observe)
    if moved == "main":
        with pytest.raises(ReviewTransportError, match="main"):
            bind(package)
    else:
        assert_not_established(bind(package))


def test_public_input_cannot_select_a_trust_source_or_review_task(reviewed_package):
    with pytest.raises(TypeError):
        bind(reviewed_package, review_task=REVIEW_TASK_ID)
    reviewed_package["declaration"]["reviewer"] = "independent"
    with pytest.raises(ReviewTransportError):
        bind(reviewed_package)


def test_read_only_git_and_bounded_source_inputs(reviewed_package, monkeypatch):
    package = reviewed_package
    trust_fixture(package, monkeypatch)
    before = state_without_objects(package["repo"])
    remote_before = state_without_objects(package["remote"])
    real_run, commands = subprocess.run, []

    def read_only(argv, *args, **kwargs):
        assert argv[0] == "git"
        tail = list(argv[3:])
        if tail[0] == "--no-replace-objects":
            tail = tail[1:]
            while tail[0] == "-c":
                tail = tail[2:]
        command = tail[0]
        assert command in {"symbolic-ref", "config", "remote", "ls-remote", "fetch", "rev-parse", "cat-file", "ls-tree"}
        if command == "config":
            assert tail[1].startswith("--get")
        if command == "remote":
            assert tail[1] == "get-url"
        if command == "fetch":
            assert {"--no-tags", "--no-write-fetch-head", "--refmap=", "--no-auto-maintenance",
                    "--no-recurse-submodules"} <= set(tail)
            assert tail[-2] == package["remote"].as_posix()
            assert tail[-1] in {package["main"], package["selection"].artifacts_sha, package["selection"].decision_sha}
        commands.append(command)
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", read_only)
    assert bind(package).reviewer_authority == "REVIEW_AUTHORITY_ESTABLISHED"
    assert state_without_objects(package["repo"]) == before
    assert state_without_objects(package["remote"]) == remote_before
    assert commands.count("fetch") == 3
    package["declaration"]["schema"] = "task-semantic-proof-intake-v2"
    with pytest.raises(ReviewTransportError):
        bind(package)
    with pytest.raises(ReviewTransportError):
        bind_canonical_semantic_review_authority(package["repo"], b" " * (transport.L1_INTAKE_MAX_BYTES + 1),
            expected_remote_url=package["remote"].as_posix(), expected_main_sha=package["main"],
            expected_candidate_sha=CANDIDATE, sources=package["sources"])
