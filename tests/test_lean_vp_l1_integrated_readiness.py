"""L1-03 falsification: actual published objects, explicit snapshot transports.

The embedded pack contains the exact published selection/decision/artifact Git
objects, excluding their already-published main ancestry. Fixture Git remotes
and replayed HTTP observations are isolated test transports, NEVER live Human
approval or Runtime EVIDENCE. No source/coverage/selection/authority reader is
replaced. The separately opt-in live observation has no snapshot fallback.
"""

import ast
import base64
import copy
import hashlib
import inspect
import json
import os
import subprocess
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import pytest
import yaml

from aios_renew import github_issue_ingress as carrier
from aios_renew import review_transport as transport
from aios_renew.proof_coverage_contract import ProofConditions
from aios_renew.task import TaskValidationError, parse_semantic_task, parse_task
from tests.test_lean_vp_l1_real_mapping import MANDATORY, repin_contract
from tests.test_lean_vp_l1_source_binding import encoded, state_without_objects
from tests.test_lean_vp_l1_versioned_task import (
    future_document, readiness, task_bytes, versioned_package,
)


REPO = Path(__file__).resolve().parents[1]
URL = "https://github.com/trung-via/AIOS-renew"
TARGET = "bf1b3fa121fc7daebaa8257aa81d6546611e06e7"
PUBLISHED = "0ff5a56674c5f56012e0df551a069d6c461fd8fa"
SELECTION = "c735c45134da36cd581ebce6547d4acf991a72d9"
DECISION = "504a88d2ee5076ce8508c61907d00a5b46682600"
ARTIFACTS = "a760736536391ed10a232b5b1790c5d21b90718c"
SELECTION_REF = f"refs/heads/aios/l1-human-selection/TASK-381/{TARGET}"
REVIEW_PATH = ".ai/reviews/REVIEW-382-001.yaml"
PATHS = dict(task=".ai/tasks/TASK-381.yaml",
             contract="docs/aios-proof-contracts/TASK-381-contract.json",
             mapping="docs/aios-proof-contracts/TASK-381-mapping.json")

# Exact immutable object capture, not a newly issued authority carrier. Git
# index-pack authenticates the pack; tests below assert the original object IDs.
_PUBLISHED_OBJECT_PACK = (
    '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'
    'dzhHIGckqWoStPX9/Hr4UIaVbFpCcSQZ94prKwZFrOBxug4t8qCtB1Ta5YgCpBYUlUaKJTKmI8UcTbbRWZMpVJ0tl8E77kTi0URD'
    'XlKNoUWY9lchmDBDzobcXzF+JOQR17/TN2XT3GMfifbcCM40cyvpvILFUXE6/fT5zcX78Sc/Tm5Ef8e/vT3lV6O1gfn9LCKhFNyS'
    'eo13VVMSxMdt3IddNQxrSsvnYvub3gWXynhrmeJP3dlf99i0XT7x7ztMHYimJSzPJD9292v1GmdVi9P7+pYM3LTtXXN0eHhdtjf3'
    '8SBVs8Nvxk3BPS/u0/OfLseTyXR8/mF8dvFu3GUEcN6WbV/VbaivsZ22ofm0yOTVaPJ2KB3vLFGJ1GFZLoummP4yeveOTC5bpWuQ'
    '8HhbBehs1fhQ4ueFraa6rxcFt9U3ySoNySeP0aI0ysfApFdC8JQYJOd1ssl73Zttb0jjIJSHnb3mcBmXOHgMs9uuRXoHS8NIAaMX'
    'PMbsBACXnNrSuix1UiGATykkQQek19a40uqqVEWqJ2NRJw4W0StqKWsYo7ZD4ZllnPWpoFz3YELzAz5TmEMJVB6vr4b+Ti7fn3fQ'
    'h4xi2RoO1oYchIkes4SYVRboJQjpggySU6aditGp7AVoBzpK7bXRzmbhWNbKdbiaRJe8mAsJ79rFlPjjS1HF2/K6v3sqi4ZExRXh'
    'HR4zUXwcrG56o7OondFJF9+ypJ4d9hZGJ7L4+DT4rvE3Pbzlq1i9nm+k59tSuXo93nxwTNJd4Ynd4XV2Fh/tC3Wy8TnZkxK526fa'
    '6WdEfnYZV7uN6z3G1+kc7Umc/jd+Jid7UJg918J2l83FPvt2t32/0/zl5qovNwk720jPNtKrjfRqT1m43TGZvght/3R7SnHdCZTo'
    '3U793kQPliX58eupFTOPMgcuOtIDAWMIjsgOPTkYrYzhHJnBLuZUzduaOMT0ruzHJpTX/bosFNMim5iSCxisCIIr5iSN0qjIEDOm'
    '409SYmQ5OJ0deM455KgDKKGSW03E1ZYaNjgLtMzScOWyWMBv+q3V7epZuKMorr8KBSItoqhQRk1IPA1H55SkYU5uGB15Y6XLGhhj'
    'EpMEEwK3NpI00XzF9P1Qlh6/imRrPz6fuN+bQINlhy/672VRDBalS4xh/oC3NMana2yYkha0SgTSA50IyoNMOjCwsVu41kIKtLuk'
    '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'
)
# Exact original Issue body, whose digest is in the published selection record.
# The HTTP adapter below replays it synthetically, including edit-history state.
_ORIGINAL_ISSUE_BODY = '{"format":"AIOS_INGRESS_ENVELOPE","version":1,"operation":"SELECT_MAPPING_REVIEW","identity":{"target_task_id":"TASK-381"},"expected_state":{"expected_main_sha":"0ff5a56674c5f56012e0df551a069d6c461fd8fa","expected_remote_url":"https://github.com/trung-via/AIOS-renew"},"payload":{"scope":{"version":1,"review_task":{"task_id":"TASK-382","revision":1,"envelope_digest":"ecc5269d2e69de82a49d3c5a0d7b394277dca9213431a275ca1d8cf1864f03e1","acceptance_ids":["AC1","AC2","AC3","AC4","AC5","AC6","AC7","AC8","AC9"]},"target_task":{"task_id":"TASK-381","revision":1,"envelope_digest":"d18a89744492a3e1d7cb31b78969a2f976788d1ca4dedf717378fc0b22465a8d","acceptance_ids":["AC1","AC2","AC3","AC4","AC5","AC6","AC7","AC8","AC9","AC10"]},"candidate_sha":"bf1b3fa121fc7daebaa8257aa81d6546611e06e7","contract_pin":{"id":"TASK-381-semantic-contract","revision":1,"digest":"4052f6bcc8aea72a214083e38b47aa0669c2533eb0fa85f8d9111dfb5ad424c8"},"mapping_pin":{"id":"TASK-381-semantic-mapping","revision":1,"digest":"db9c9b4e3b5baa93598843bf84c809c996738f5d0003ec3d6aa177b673ca3aec"},"acceptance":[{"review_acceptance_id":"AC1","target_acceptance_ids":["AC3"],"obligation_ids":["T381-B02"]},{"review_acceptance_id":"AC2","target_acceptance_ids":["AC1","AC2","AC3"],"obligation_ids":["T381-D01","T381-D02","T381-N01","T381-N02","T381-N03","T381-B01","T381-B03"]},{"review_acceptance_id":"AC3","target_acceptance_ids":["AC4"],"obligation_ids":["T381-S01","T381-S02"]},{"review_acceptance_id":"AC4","target_acceptance_ids":["AC5"],"obligation_ids":["T381-A01"]},{"review_acceptance_id":"AC5","target_acceptance_ids":["AC5"],"obligation_ids":["T381-A02","T381-A03"]},{"review_acceptance_id":"AC6","target_acceptance_ids":["AC10"],"obligation_ids":["T381-SC01"]},{"review_acceptance_id":"AC7","target_acceptance_ids":["AC9"],"obligation_ids":["T381-DOC01"]},{"review_acceptance_id":"AC8","target_acceptance_ids":["AC6","AC7","AC8"],"obligation_ids":["T381-R01","T381-R02","T381-L01","T381-L02","T381-T01","T381-T02"]},{"review_acceptance_id":"AC9","target_acceptance_ids":["AC10","AC4"],"obligation_ids":["T381-S03","T381-SC02"]}]},"sources":{"task":{"source_sha":"bf1b3fa121fc7daebaa8257aa81d6546611e06e7","tree_sha":"5caf8e0ad6f39bc94d74677e5e38c7a437afb0a5","path":".ai/tasks/TASK-381.yaml","blob_sha":"3db2f20f9a42560d0d1b55db522bafb0cca87683"},"contract":{"source_sha":"0ff5a56674c5f56012e0df551a069d6c461fd8fa","tree_sha":"09e0b4bc63657216bfc2d73f6efd4b507fc849d6","path":"docs/aios-proof-contracts/TASK-381-contract.json","blob_sha":"f6d72b058e4276f0c3f0e0f6215e0369014fc70e"},"mapping":{"source_sha":"0ff5a56674c5f56012e0df551a069d6c461fd8fa","tree_sha":"09e0b4bc63657216bfc2d73f6efd4b507fc849d6","path":"docs/aios-proof-contracts/TASK-381-mapping.json","blob_sha":"00d8410d8f9d1086e286c6b07e0290442f68d0c2"}},"review_task_source":{"source_sha":"e381e921bbf82dd131c7178f35c4aad9ccac22dd","tree_sha":"005b4b50867e5c1d7ee944f176004ade29070101","path":".ai/tasks/TASK-382.yaml","blob_sha":"c745dc9c9eb7e3649ba0394221cc0dc895c7c995"},"run":{"id":"RUN-382-001","sha256":"77afa26b9ef3dbf4f2e93d238a3a315dc84bb84f92d58d5b35956587f280f548","base_sha":"e381e921bbf82dd131c7178f35c4aad9ccac22dd","candidate_sha":"0ff5a56674c5f56012e0df551a069d6c461fd8fa"}}}'


def git_bytes(repo, *args, content=None):
    return subprocess.run(("git", "-C", str(repo), *args), input=content,
                          capture_output=True, check=True).stdout


def git(repo, *args, content=None):
    return git_bytes(repo, *args, content=content).decode().strip()


def blob(repo, sha, path):
    return git_bytes(repo, "cat-file", "blob", f"{sha}:{path}")


def declaration(repo):
    contract = json.loads(blob(repo, PUBLISHED, PATHS["contract"]))
    return dict(schema="task-semantic-proof-intake-v1", task=copy.deepcopy(contract["task"]),
        candidate_sha=TARGET, sources={role: dict(source_sha=TARGET if role == "task" else PUBLISHED,
            blob_sha=git(repo, "rev-parse", f"{TARGET if role == 'task' else PUBLISHED}:{path}"))
            for role, path in PATHS.items()}, contract=contract,
        mapping=json.loads(blob(repo, PUBLISHED, PATHS["mapping"])))


def set_ref(package, ref, sha):
    path = package["remote"] / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sha + "\n", encoding="ascii")


@pytest.fixture
def published_snapshot(tmp_path, monkeypatch):
    repo, remote = tmp_path / "observer", tmp_path / "snapshot.git"
    repo.mkdir()
    remote.mkdir()
    git(repo, "init", "-b", "main")
    git(remote, "init", "--bare")
    common = Path(git(REPO, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = REPO / common
    alternate = remote / "objects/info/alternates"
    # Git treats a CR from Windows newline translation as part of the path.
    alternate.write_text((common / "objects").resolve().as_posix() + "\n",
                         encoding="utf-8", newline="\n")
    git(remote, "index-pack", "--stdin", content=base64.b64decode(_PUBLISHED_OBJECT_PACK))
    alternate = repo / ".git/objects/info/alternates"
    alternate.write_text((remote / "objects").as_posix() + "\n",
                         encoding="utf-8", newline="\n")
    git(repo, "config", "remote.origin.url", URL)
    git(repo, "config", "branch.main.remote", "origin")
    git(repo, "config", "branch.main.merge", "refs/heads/main")
    package = dict(repo=repo, remote=remote, main=PUBLISHED, declaration=declaration(repo))
    for ref, sha in (("refs/heads/main", PUBLISHED), (SELECTION_REF, SELECTION),
                     ("refs/heads/aios/review/RUN-382-001", PUBLISHED),
                     ("refs/heads/aios/artifacts/RUN-382-001", ARTIFACTS),
                     ("refs/heads/aios/review-decision/RUN-382-001", DECISION)):
        set_ref(package, ref, sha)
        assert git(repo, "cat-file", "-t", sha) == "commit"
    record = json.loads(blob(repo, SELECTION, transport.L1_SELECTION_PATH))
    provenance = record["provenance"]
    assert hashlib.sha256(_ORIGINAL_ISSUE_BODY.encode()).hexdigest() == provenance["body_sha256"]
    assert json.loads(_ORIGINAL_ISSUE_BODY) == record["request"]
    package.update(record=record, last_edited_at=None,
        issue=dict(number=2178, id=provenance["issue_id"], node_id=provenance["issue_node_id"],
            title="[AIOS BRAIN INGRESS]", repository_url=f"https://api.github.com/repos/{provenance['repository']}",
            created_at=provenance["created_at"], body=_ORIGINAL_ISSUE_BODY,
            user=dict(login=provenance["actor"], id=provenance["actor_id"], type="User")))
    real_run = subprocess.run

    def route(argv, *args, **kwargs):
        # Only transport I/O is replayed. Configured and selection URL bytes
        # remain original; no private reader or positive result is substituted.
        argv = list(argv)
        if "fetch" in argv or "ls-remote" in argv:
            argv = [remote.as_posix() if value == URL else value for value in argv]
        return real_run(argv, *args, **kwargs)

    def http(repository, suffix):
        assert repository == "trung-via/AIOS-renew"
        issue = package["issue"]
        if suffix == "":
            return dict(full_name=repository, id=provenance["repository_id"])
        if suffix == "/issues/2178":
            return copy.deepcopy(issue)
        if suffix == "/selection-edit-history/2178":
            return dict(data=dict(repository=dict(nameWithOwner=repository,
                databaseId=provenance["repository_id"], issue=dict(id=issue["node_id"],
                    number=issue["number"], title=issue["title"], body=issue["body"],
                    createdAt=issue["created_at"], lastEditedAt=package["last_edited_at"]))))
        raise AssertionError("unexpected snapshot HTTP subject")

    monkeypatch.setattr(subprocess, "run", route)
    monkeypatch.setattr(carrier, "_github_selection_json", http)
    return package


def read(package, **overrides):
    options = dict(expected_remote_url=URL, expected_main_sha=package["main"])
    options.update(overrides)
    return transport.read_l1_integrated_readiness(package["repo"], encoded(package["declaration"]), **options)


def assert_nonoperative(value):
    assert value.future_version_admission == "NOT_ADMITTED"
    assert value.ka01_g3 == value.ka01_g6 == value.production_opt_in == "NOT_ESTABLISHED"
    assert value.ka01 == "NOT_ACTIVATED"
    assert value.vp01_matched_measurement == "UNKNOWN_DEFERRED_TO_L4"
    assert not value.production_eligible and not value.execution_authorized
    for lane in (value.legacy_selected_overlay, value.prospective_semantic_task):
        assert lane.canonical_verification == lane.evidence_applicability == "UNESTABLISHED"
        assert not lane.production_eligible and not lane.execution_authorized
    future = value.prospective_semantic_task
    assert future.task_source is future.human_selection is None
    assert future.subject_status == "NO_DISTINCT_FUTURE_SUBJECT_OBSERVED"
    assert future.structure == future.source_consistency == future.source_currentness == "UNKNOWN"
    assert future.human_intent == future.reviewer_authority == future.publisher_authority == "NOT_ESTABLISHED"
    assert future.future_version_admission == "NOT_ADMITTED"
    assert future.bridge == "UNSUPPORTED_LEGACY_TO_SEMANTIC_V1"


def assert_blocked(value):
    assert_nonoperative(value)
    assert value.observation_status == "NOT_ESTABLISHED"
    lane = value.legacy_selected_overlay
    assert lane.lineage is None
    assert lane.reviewer_authority == lane.publisher_authority == "NOT_ESTABLISHED"
    assert lane.mapping_review_verification == "UNESTABLISHED"
    assert lane.semantic_adequacy == "NOT_ESTABLISHED" and lane.reviewer_verdict == "UNKNOWN"
    assert lane.review_acceptance_count is None


def test_published_snapshot_composes_unchanged_readers_and_complete_correspondence(published_snapshot):
    package = published_snapshot
    value = read(package)
    assert_nonoperative(value)
    assert value.observation_status == "SELECTED_LEGACY_LINEAGE_ESTABLISHED"
    lane = value.legacy_selected_overlay
    assert lane.reason == "EXACT_SELECTED_LEGACY_LINEAGE"
    assert lane.structure == "STRUCTURALLY_COMPLETE"
    assert lane.source_authentication == lane.source_currentness == "ESTABLISHED_FOR_OBSERVATION"
    assert lane.human_intent == "AUTHENTICATED_SELECTION"
    assert lane.human_selection.selection_sha == SELECTION
    assert lane.lineage.decision_sha == DECISION and lane.lineage.artifacts_sha == ARTIFACTS
    assert lane.lineage.run_id == "RUN-382-001" and lane.lineage.review_id == "REVIEW-382-001"
    assert lane.lineage.reviewed_sha == lane.lineage.published_sha == PUBLISHED
    assert lane.reviewer_authority == "REVIEW_AUTHORITY_ESTABLISHED"
    assert lane.publisher_authority == "EXACT_SOURCE_PUBLICATION_ESTABLISHED"
    assert lane.mapping_review_verification == "CANONICAL_RESULT_EVIDENCE_BOUND_TO_REVIEW_RUN"
    assert lane.reviewer_verdict == "PRIMARY_PASS_FOR_TASK_382_AC1_AC9"
    assert lane.semantic_adequacy == "EXISTING_MAPPING_REVIEW_JUDGMENT_OBSERVED"
    assert (lane.target_acceptance_count, lane.review_acceptance_count, lane.obligation_count,
            lane.proof_count, lane.mapping_entry_count) == (10, 9, 23, 23, 23)
    assert lane.target_acceptance == parse_task(blob(package["repo"], TARGET, PATHS["task"]).decode()).acceptance
    assert lane.review_acceptance == parse_task(blob(package["repo"], PUBLISHED, ".ai/tasks/TASK-382.yaml").decode()).acceptance
    intake = lane.source_binding.intake
    assert {item.id for item in intake.coverage.contract.obligations} == {
        f"T381-{suffix}" for suffixes in MANDATORY.values() for suffix in suffixes}
    selected_sources = package["record"]["request"]["payload"]["sources"]
    for source in lane.source_binding.sources:
        assert asdict(source.locator) == selected_sources[source.role]
        raw = blob(package["repo"], source.locator.source_sha, source.locator.path)
        assert source.content_sha256 == hashlib.sha256(raw).hexdigest()
        assert source.byte_count == len(raw)
    # Check complete projected semantics against the independently read original
    # blobs, rather than treating counts or matching labels as completeness.
    contract = json.loads(blob(package["repo"], PUBLISHED, PATHS["contract"]))
    mapping = json.loads(blob(package["repo"], PUBLISHED, PATHS["mapping"]))
    obligations = {item["id"]: item for item in contract["obligations"]}
    proofs = {item["id"]: item for item in mapping["proofs"]}
    for item in intake.coverage.contract.obligations:
        original = obligations[item.id]
        original["acceptance_ids"].sort()
        original["conditions"]["fixture_refs"].sort()
        assert json.loads(json.dumps(asdict(item))) == original
    for item in intake.coverage.mapping.proofs:
        original = proofs[item.id]
        original["claims"].sort(key=lambda claim: claim["id"])
        original["conditions"]["fixture_refs"].sort()
        assert json.loads(json.dumps(asdict(item))) == original
    assert {item.obligation_id: asdict(item) for item in intake.coverage.mapping.entries} == {
        item["obligation_id"]: item for item in mapping["entries"]}
    assert asdict(lane.lineage.scope)["acceptance"] == tuple(
        dict(review_acceptance_id=item["review_acceptance_id"],
             target_acceptance_ids=tuple(sorted(item["target_acceptance_ids"])),
             obligation_ids=tuple(sorted(item["obligation_ids"])))
        for item in package["record"]["request"]["payload"]["scope"]["acceptance"])
    assert hashlib.sha256(blob(package["repo"], ARTIFACTS, ".ai/transport/result.json")).hexdigest() == lane.lineage.result_sha256
    assert hashlib.sha256(blob(package["repo"], ARTIFACTS, ".ai/transport/run.json")).hexdigest() == lane.lineage.run_sha256
    assert hashlib.sha256(blob(package["repo"], DECISION, REVIEW_PATH)).hexdigest() == lane.lineage.review_sha256
    assert value.prospective_semantic_task.legacy_source_compatibility == "REJECTED_BY_TASK_SEMANTIC_V1"


def mutate_commit(package, sha, edits, *, parents=None):
    """Adversarial objects in the disposable fixture, never canonical writers."""
    repo = package["remote"]

    def tree_changed(tree, path, content):
        entries = {}
        for row in git_bytes(repo, "ls-tree", "-z", tree).split(b"\0"):
            if row:
                meta, name = row.split(b"\t")
                entries[name] = meta
        name, separator, rest = path.partition("/")
        key = name.encode()
        if separator:
            child = entries[key].split()[-1].decode()
            oid = tree_changed(child, rest, content)
            entries[key] = f"040000 tree {oid}".encode()
        else:
            oid = git(repo, "hash-object", "-w", "--stdin", content=content)
            entries[key] = f"100644 blob {oid}".encode()
        raw = b"".join(meta + b"\t" + name + b"\0" for name, meta in entries.items())
        return git(repo, "mktree", "-z", content=raw)

    raw = git_bytes(repo, "cat-file", "commit", sha)
    headers, message = raw.split(b"\n\n", 1)
    lines = headers.split(b"\n")
    tree = lines[0][5:].decode()
    for path, content in edits.items():
        tree = tree_changed(tree, path, content)
    if parents is not None:
        lines = [line for line in lines if not line.startswith(b"parent ")]
        lines[1:1] = [f"parent {parent}".encode() for parent in parents]
    lines[0] = f"tree {tree}".encode()
    return git(repo, "hash-object", "-t", "commit", "-w", "--stdin",
               content=b"\n".join(lines) + b"\n\n" + message)


@pytest.mark.parametrize("fault", ["missing", "competing", "replayed", "copied-body", "untrusted", "edited", "unavailable"])
def test_selection_is_derived_exact_independent_and_never_replaced(published_snapshot, monkeypatch, fault):
    package = published_snapshot
    ref = package["remote"] / SELECTION_REF
    if fault in {"missing", "replayed"}:
        ref.unlink()
        if fault == "replayed":
            set_ref(package, SELECTION_REF.replace(TARGET, PUBLISHED), SELECTION)
    elif fault == "competing":
        # An exact packed ref plus a conflicting loose descendant cannot be
        # represented by ordinary loose files alone; both are observable.
        ref.unlink()
        (package["remote"] / "packed-refs").write_text(f"{SELECTION} {SELECTION_REF}\n", encoding="ascii")
        set_ref(package, SELECTION_REF + "/competing", SELECTION)
    elif fault == "copied-body":
        package["issue"]["body"] += " "
    elif fault == "untrusted":
        package["issue"]["user"]["login"] = "not-allowlisted"
    elif fault == "edited":
        package["last_edited_at"] = "2026-10-19T01:23:16Z"
    else:
        def unavailable(*args):
            raise carrier.GitHubIssueIngressError("external observation unavailable")
        monkeypatch.setattr(carrier, "_github_selection_json", unavailable)
    value = read(package)
    assert_blocked(value)
    assert value.legacy_selected_overlay.human_intent == "NOT_ESTABLISHED"
    assert value.legacy_selected_overlay.structure == "STRUCTURALLY_COMPLETE"


@pytest.mark.parametrize("fault", ["target", "review-task", "run", "candidate", "correspondence", "mapping-pin"])
def test_changed_selection_subjects_cannot_reuse_actual_receipt(published_snapshot, fault):
    package = published_snapshot
    record = package["record"]
    payload = record["request"]["payload"]
    if fault == "target":
        payload["scope"]["target_task"]["task_id"] = "TASK-OTHER"
    elif fault == "review-task":
        payload["scope"]["review_task"]["revision"] = 2
    elif fault == "run":
        payload["run"]["id"] = "RUN-382-002"
    elif fault == "candidate":
        payload["run"]["candidate_sha"] = TARGET
    elif fault == "correspondence":
        payload["scope"]["acceptance"].pop()
    else:
        payload["scope"]["mapping_pin"]["digest"] = "0" * 64
    raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    sha = mutate_commit(package, SELECTION, {transport.L1_SELECTION_PATH: raw})
    set_ref(package, SELECTION_REF, sha)
    assert_blocked(read(package))


@pytest.mark.parametrize("fault", ["missing-review", "partial-review", "unrelated-review", "wrong-candidate",
    "not-primary", "findings", "wrong-run", "wrong-result", "wrong-evidence", "missing-evidence", "conflict"])
def test_review_runtime_artifacts_and_publisher_are_independent_requirements(published_snapshot, fault):
    package = published_snapshot
    if fault == "missing-review":
        (package["remote"] / "refs/heads/aios/review-decision/RUN-382-001").unlink()
    elif fault == "conflict":
        set_ref(package, "refs/heads/aios/failure/RUN-382-001", ARTIFACTS)
    elif fault in {"wrong-run", "wrong-result", "wrong-evidence", "missing-evidence"}:
        path = ".ai/transport/run.json" if fault == "wrong-run" else ".ai/transport/result.json"
        data = json.loads(blob(package["repo"], ARTIFACTS, path))
        if fault == "wrong-run":
            data["run_id"] = "RUN-382-002"
        elif fault == "wrong-result":
            data["result"]["head_sha"] = TARGET
        elif fault == "wrong-evidence":
            data["evidence"][0]["subject_sha"] = TARGET
        else:
            data["evidence"] = []
        sha = mutate_commit(package, ARTIFACTS, {path: encoded(data)})
        set_ref(package, "refs/heads/aios/artifacts/RUN-382-001", sha)
    else:
        review = yaml.safe_load(blob(package["repo"], DECISION, REVIEW_PATH))
        if fault == "partial-review":
            review["acceptance"].pop("AC9")
        elif fault == "unrelated-review":
            review["review_id"] = "REVIEW-381-001"
        elif fault == "wrong-candidate":
            review["reviewed_sha"] = TARGET
        elif fault == "not-primary":
            review["mode"] = "DELTA"
        else:
            review["findings"] = [dict(finding_id="UNRELATED-FINDING")]
        sha = mutate_commit(package, DECISION, {REVIEW_PATH: encoded(review)})
        set_ref(package, "refs/heads/aios/review-decision/RUN-382-001", sha)
    value = read(package)
    assert_blocked(value)
    assert value.legacy_selected_overlay.human_intent == "AUTHENTICATED_SELECTION"


@pytest.mark.parametrize("role", ["task", "contract", "mapping"])
def test_current_source_changes_invalidate_the_whole_source_observation(published_snapshot, role):
    package = published_snapshot
    path = PATHS[role]
    sha = mutate_commit(package, PUBLISHED, {path: blob(package["repo"], PUBLISHED, path) + b" \n"}, parents=(PUBLISHED,))
    set_ref(package, "refs/heads/main", sha)
    package["main"] = sha
    value = read(package)
    assert_blocked(value)
    lane = value.legacy_selected_overlay
    assert lane.source_binding is None and lane.structure == lane.source_currentness == "UNKNOWN"
    assert lane.obligation_count is lane.target_acceptance_count is None


@pytest.mark.parametrize("fault", ["stale-main", "unpublished-twin", "remote", "remote-spelling"])
def test_current_main_containment_and_exact_transport_cannot_be_copied(published_snapshot, fault):
    package = published_snapshot
    if fault in {"stale-main", "unpublished-twin"}:
        parents = (PUBLISHED,) if fault == "stale-main" else (TARGET,)
        sha = mutate_commit(package, PUBLISHED, {}, parents=parents)
        set_ref(package, "refs/heads/main", sha)
        if fault == "unpublished-twin":
            package["main"] = sha  # Same tree, but actual published SHA is not contained.
    else:
        git(package["repo"], "config", "remote.origin.url", URL + (".git" if fault == "remote-spelling" else "/other"))
    assert_blocked(read(package))


def test_later_main_can_preserve_exact_published_source_without_advancing_any_gate(published_snapshot):
    package = published_snapshot
    sha = mutate_commit(package, PUBLISHED, {}, parents=(PUBLISHED,))
    set_ref(package, "refs/heads/main", sha)
    package["main"] = sha
    value = read(package)
    assert value.observation_status == "SELECTED_LEGACY_LINEAGE_ESTABLISHED"
    assert value.legacy_selected_overlay.source_binding.main_after == sha
    assert_nonoperative(value)


def test_changed_canonical_carrier_policy_cannot_reuse_historical_human_selection(published_snapshot):
    package = published_snapshot
    path = ".ai/brain-ingress-carriers.yaml"
    sha = mutate_commit(package, PUBLISHED,
        {path: blob(package["repo"], PUBLISHED, path) + b"\n"}, parents=(PUBLISHED,))
    set_ref(package, "refs/heads/main", sha)
    package["main"] = sha
    value = read(package)
    assert_blocked(value)
    assert value.legacy_selected_overlay.structure == "STRUCTURALLY_COMPLETE"
    assert value.legacy_selected_overlay.human_intent == "NOT_ESTABLISHED"


@pytest.mark.parametrize("dimension", sorted(ProofConditions.__dataclass_fields__))
def test_each_changed_proof_condition_cannot_borrow_the_selected_source(published_snapshot, dimension):
    package = published_snapshot
    data = package["declaration"]
    obligation = data["contract"]["obligations"][0]
    entry = next(item for item in data["mapping"]["entries"] if item["obligation_id"] == obligation["id"])
    proof = next(item for item in data["mapping"]["proofs"] if item["id"] == entry["proof_id"])
    original = obligation["conditions"][dimension]
    changed = (original + 1 if type(original) is int else ["different-fixture"] if type(original) is list
               else PUBLISHED if dimension in {"candidate_sha", "base_sha"} else "different-condition")
    obligation["conditions"][dimension] = proof["conditions"][dimension] = changed
    # A valid coherent repin is still merely incoming data, never canonical
    # authority. Invalid combinations must fail without reaching a green report.
    try:
        repin_contract(data["contract"], data["mapping"], data["task"])
    except ValueError:
        pass
    assert_blocked(read(package))


@pytest.mark.parametrize("fault", ["task", "revision", "target-digest", "candidate", "source-swap", "contract", "mapping", "partial"])
def test_incoming_source_and_contract_substitutions_never_define_authority(published_snapshot, fault):
    package = published_snapshot
    data = package["declaration"]
    if fault == "task":
        data["task"]["task_id"] = "TASK-OTHER"
    elif fault == "revision":
        data["task"]["revision"] = 2
    elif fault == "target-digest":
        data["task"]["envelope_digest"] = "0" * 64
    elif fault == "candidate":
        data["candidate_sha"] = PUBLISHED
    elif fault == "source-swap":
        data["sources"]["mapping"] = copy.deepcopy(data["sources"]["contract"])
    elif fault in {"contract", "mapping"}:
        data[fault]["id"] = "unrelated-source"
    else:
        data["mapping"]["entries"].pop()
    assert_blocked(read(package))


@pytest.mark.parametrize("race", ["selection", "decision", "main", "remote"])
def test_repeated_observations_reject_mid_read_authority_or_source_changes(published_snapshot, monkeypatch, race):
    package = published_snapshot
    run = subprocess.run
    calls = 0

    def moving(argv, *args, **kwargs):
        nonlocal calls
        if "ls-remote" in argv and SELECTION_REF in argv:
            calls += 1
            if calls == 4:
                if race == "selection":
                    set_ref(package, SELECTION_REF, TARGET)
                elif race == "decision":
                    set_ref(package, "refs/heads/aios/review-decision/RUN-382-001", TARGET)
                elif race == "main":
                    set_ref(package, "refs/heads/main", TARGET)
                else:
                    git(package["repo"], "config", "remote.origin.url", URL + "/changed")
        return run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", moving)
    assert_blocked(read(package))


def test_legacy_bytes_and_future_synthetic_readiness_are_distinct_subjects(published_snapshot, tmp_path, monkeypatch):
    package = published_snapshot
    original = blob(package["repo"], TARGET, PATHS["task"])
    with pytest.raises(TaskValidationError):
        parse_semantic_task(original)
    with pytest.raises(transport.ReviewTransportError, match="prospective"):
        transport.read_semantic_task_readiness(package["repo"], encoded(package["declaration"]),
            expected_remote_url=URL, expected_main_sha=PUBLISHED, expected_candidate_sha=TARGET,
            sources={role: transport.GitSemanticSource(**source)
                     for role, source in package["record"]["request"]["payload"]["sources"].items()})
    selected_legacy = read(package)
    # Existing fixture authenticates a SYNTHETIC future subject through its
    # synthetic HTTP adapter; it cannot replace or populate the actual L1 lane.
    with monkeypatch.context() as future_patch:
        future = versioned_package(tmp_path / "separate-future", future_patch)
        synthetic = readiness(future)
        assert synthetic.human_selection == "AUTHENTICATED_SELECTION"
        assert not synthetic.production_eligible and synthetic.future_version_admission == "NOT_ADMITTED"
        assert_blocked(transport.read_l1_integrated_readiness(future["repo"], encoded(future["declaration"]),
            expected_remote_url=future["remote"].as_posix(), expected_main_sha=future["main"]))
        from aios_renew.operator import OperatorError, load_task

        before = state_without_objects(future["repo"]), state_without_objects(future["remote"])
        with pytest.raises(OperatorError, match="invalid TASK"):
            load_task(future["repo"], synthetic.task.task_id)
        assert (state_without_objects(future["repo"]), state_without_objects(future["remote"])) == before
    assert selected_legacy.prospective_semantic_task.task_source is None
    assert selected_legacy.prospective_semantic_task.human_selection is None
    assert_nonoperative(selected_legacy)


@pytest.mark.parametrize("policy", [None, "minimum-sufficient-v1", "minimum-sufficient-v2"])
def test_legacy_parser_behavior_is_independent_of_integrated_and_future_readers(monkeypatch, policy):
    from tests.test_task import VALID_TASK

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy parser invoked a prospective observation")

    monkeypatch.setattr(transport, "read_l1_integrated_readiness", forbidden)
    monkeypatch.setattr(transport, "read_semantic_task_readiness", forbidden)
    source = VALID_TASK if policy is None else VALID_TASK.replace("verification:\n", f"verification:\n  policy: {policy}\n")
    task = parse_task(source)
    assert task.verification.policy == policy
    assert task.verification.required == ("pytest tests/test_task.py",)
    with pytest.raises(TaskValidationError):
        parse_task(source.replace("- pytest tests/test_task.py", ""))
    with pytest.raises(TaskValidationError):
        parse_task(task_bytes(future_document()).decode())


def test_published_reader_and_parser_definitions_are_preserved():
    for path in ("src/aios_renew/review_transport.py", "src/aios_renew/task.py",
                 "src/aios_renew/proof_coverage_contract.py"):
        original = ast.parse(blob(REPO, PUBLISHED, path))
        current = ast.parse((REPO / path).read_text(encoding="utf-8"))
        definitions = {node.name: ast.dump(node) for node in current.body
                       if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        for node in original.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                assert definitions[node.name] == ast.dump(node), (path, node.name)


def test_result_is_deeply_immutable_and_does_not_retain_mutable_input(published_snapshot):
    value = read(published_snapshot)
    before = asdict(value)
    published_snapshot["declaration"]["mapping"]["proofs"].clear()
    assert asdict(value) == before
    with pytest.raises(FrozenInstanceError):
        value.execution_authorized = True
    with pytest.raises(FrozenInstanceError):
        value.legacy_selected_overlay.source_binding.intake.coverage.contract.obligations[0].conditions.workers = 2
    with pytest.raises(TypeError):
        transport.L1IntegratedReadiness(value.legacy_selected_overlay, value.prospective_semantic_task,
                                       production_eligible=True)


@pytest.mark.parametrize("extra", ["sources", "review_ref", "selection", "verdict", "resolver", "future_subject"])
def test_integrated_api_accepts_no_alternate_authority_or_fixture(published_snapshot, extra):
    assert set(inspect.signature(transport.read_l1_integrated_readiness).parameters) == {
        "repo", "declaration", "expected_remote_url", "expected_main_sha"}
    with pytest.raises(TypeError):
        read(published_snapshot, **{extra: "PASS"})


def test_read_report_and_decoder_have_no_process_or_repository_writes(published_snapshot, monkeypatch):
    from aios_renew import authoring_ingress, operator, publication, verification

    package = published_snapshot
    before = state_without_objects(package["repo"]), state_without_objects(package["remote"])
    real = subprocess.run

    def forbidden(*args, **kwargs):
        raise AssertionError("observation attempted lifecycle or proof execution")

    for module, name in ((authoring_ingress, "execute_ingress"), (operator, "run_task"),
                         (operator, "run_repair"), (operator, "run_remediation"),
                         (publication, "publish_review_decision"), (verification, "execute_verification"),
                         (verification, "execute_minimum_verification")):
        monkeypatch.setattr(module, name, forbidden)

    def reads_only(argv, *args, **kwargs):
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

    monkeypatch.setattr(subprocess, "run", reads_only)
    value = read(package)
    assert value.observation_status == "SELECTED_LEGACY_LINEAGE_ESTABLISHED"
    assert_nonoperative(value)
    json.dumps(asdict(value))  # Reporting is a pure projection too.
    assert (state_without_objects(package["repo"]), state_without_objects(package["remote"])) == before


def test_explicit_live_observation_never_falls_back_to_snapshot_authority():
    # Runtime/operator can request this bounded external observation separately.
    # No replay transport or synthetic HTTP provider is installed in this test.
    if os.environ.get("AIOS_L1_LIVE_OBSERVATION") != "1":
        pytest.skip("live external observation not requested; live lineage remains UNKNOWN")
    expected_main = os.environ["AIOS_L1_EXPECTED_MAIN_SHA"]
    value = transport.read_l1_integrated_readiness(REPO, encoded(declaration(REPO)),
        expected_remote_url=URL, expected_main_sha=expected_main)
    assert_nonoperative(value)
    if value.observation_status == "SELECTED_LEGACY_LINEAGE_ESTABLISHED":
        assert value.legacy_selected_overlay.lineage.human_selection_sha == SELECTION
        assert value.legacy_selected_overlay.lineage.artifacts_sha == ARTIFACTS
    else:
        assert_blocked(value)
        assert value.legacy_selected_overlay.reason in {"SOURCE_OBSERVATION_NOT_ESTABLISHED",
            "HUMAN_MAPPING_SELECTION_UNAVAILABLE", "MAPPING_REVIEW_NOT_ESTABLISHED"}
