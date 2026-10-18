"""Prospective, synthetic sources in real local Git; no live provider or RUN."""

import copy
import hashlib
import json
import subprocess
import zlib
from dataclasses import FrozenInstanceError, replace

import pytest

import aios_renew.review_transport as transport
from aios_renew.review_transport import (
    GitSemanticSource, ReviewTransportError, bind_canonical_semantic_proof_sources,
)
from tests.git_fixture_support import _write_object, commit_fixture_state, materialize_git_baseline
from tests.test_proof_coverage_contract import CANDIDATE, contract, mapping_for, obligation


TASK = b"""task_id: TASK-SYNTHETIC
revision: 1
goal: Preserve declared safety.
problem: Synthetic source consistency needs exact identities.
assumptions: [Synthetic only]
scope:
  inspect: [subject.txt]
  modify: [subject.txt]
non_goals: [Production activation]
constraints:
  hard: [No execution]
acceptance:
  - id: AC1
    condition: Preserve isolated conditions.
  - id: AC2
    condition: Preserve integration conditions.
verification:
  policy: minimum-sufficient-v1
  required: [python -m pytest tests/test_synthetic.py]
"""
PATHS = dict(task=".ai/tasks/TASK-SYNTHETIC.yaml", contract="proof/contract.json",
             mapping="proof/mapping.json")


def git(repo, *args):
    return subprocess.run(("git", "-C", str(repo), *args), check=True,
                          capture_output=True).stdout.decode("utf-8").strip()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("utf-8")


def locator(repo, commit, path):
    return GitSemanticSource(commit, git(repo, "rev-parse", f"{commit}^{{tree}}"),
                             path, git(repo, "rev-parse", f"{commit}:{path}"))


def commit(repo, remote=None, paths=tuple(PATHS.values())):
    return commit_fixture_state(
        repo, paths=paths, message="synthetic semantic sources",
        user_name="L1 Synthetic", user_email="l1@example.invalid",
        **(dict(remote=remote, remote_ref="refs/heads/main") if remote else {}))


@pytest.fixture
def package(tmp_path):
    repo, remote, task_sha = materialize_git_baseline(
        tmp_path / "canonical", files={PATHS["task"]: TASK, "subject.txt": "synthetic\n"},
        user_name="L1 Synthetic", user_email="l1@example.invalid", commit_message="synthetic TASK")
    data = contract(obligation("integration-proof", acceptance_ids=["AC2"]))
    data["task"] = dict(task_id="TASK-SYNTHETIC", revision=1,
                        envelope_digest=hashlib.sha256(TASK).hexdigest(), acceptance_ids=["AC1", "AC2"])
    data["obligations"][1]["conditions"].update(
        fixture_refs=["integration-fixture-v1"], integration_ref="integration-v1",
        profile_ref="parallel-profile-v1", worker_mode="parallel", workers=2,
        concurrency_ref="concurrent-v1", ordering_ref="integration-order-v1")
    mapping = mapping_for(data)
    for role, document in (("contract", data), ("mapping", mapping)):
        target = repo / PATHS[role]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded(document))
    main = commit(repo, remote)
    sources = {role: locator(repo, task_sha if role == "task" else main, path)
               for role, path in PATHS.items()}
    declaration = dict(schema="task-semantic-proof-intake-v1", task=copy.deepcopy(data["task"]),
                       candidate_sha=CANDIDATE, sources={role: dict(
                           source_sha=source.source_sha, blob_sha=source.blob_sha)
                           for role, source in sources.items()}, contract=data, mapping=mapping)
    return dict(repo=repo, remote=remote, main=main, sources=sources, declaration=declaration)


def bind(package, **overrides):
    arguments = dict(expected_remote_url=package["remote"].as_posix(), expected_main_sha=package["main"],
                     expected_candidate_sha=CANDIDATE, sources=package["sources"])
    arguments.update(overrides)
    return bind_canonical_semantic_proof_sources(
        package["repo"], encoded(package["declaration"]), **arguments)


def replace_source(package, role, content):
    (package["repo"] / PATHS[role]).write_bytes(content)
    package["main"] = commit(package["repo"], package["remote"], [PATHS[role]])
    source = locator(package["repo"], package["main"], PATHS[role])
    package["sources"][role] = source
    package["declaration"]["sources"][role] = dict(source_sha=source.source_sha, blob_sha=source.blob_sha)


def state_without_objects(repo):
    return {path.relative_to(repo).as_posix(): path.read_bytes()
            for path in repo.rglob("*") if path.is_file()
            and not path.relative_to(repo).as_posix().startswith((".git/objects/", "objects/"))}


def test_exact_immutable_sources_preserve_full_conditions_and_unestablished_authority(package):
    result = bind(package)
    assert result.main_before == result.main_after == package["main"]
    assert result.remote == "origin" and result.remote_url == package["remote"].as_posix()
    assert result.source_authentication == result.source_currentness == "ESTABLISHED_FOR_OBSERVATION"
    assert result.intake.coverage.contract.task.acceptance_ids == ("AC1", "AC2")
    obligations = result.intake.coverage.contract.obligations
    assert len(obligations) == 2
    assert {item.conditions.workers for item in obligations} == {1, 2}
    for dimension in ("fixture_refs", "profile_ref", "integration_ref", "concurrency_ref", "ordering_ref"):
        assert len({getattr(item.conditions, dimension) for item in obligations}) == 2
    assert result.readiness == "SOURCE_CONSISTENT_ONLY"
    assert result.canonical_verification == "UNESTABLISHED"
    assert not result.production_eligible and not result.execution_authorized
    assert result.ka01 == "NOT_ACTIVATED"
    assert set(result.unestablished) == {
        "independent_human_approval", "independent_semantic_mapping_review", "semantic_adequacy",
        "canonical_runtime_verification", "evidence_applicability", "future_version_policy_admission",
        "production_opt_in"}
    for source in result.sources:
        content = subprocess.run(("git", "-C", str(package["repo"]), "cat-file", "blob",
                                  source.locator.blob_sha), check=True, capture_output=True).stdout
        assert source.content_sha256 == hashlib.sha256(content).hexdigest()
        assert source.byte_count == len(content)
    with pytest.raises(FrozenInstanceError):
        result.main_after = "f" * 40
    with pytest.raises(FrozenInstanceError):
        result.sources[0].locator.blob_sha = "f" * 40


def test_reordering_is_stable_without_erasing_distinguishing_conditions(package):
    original = bind(package)
    incoming = package["declaration"]
    incoming["task"]["acceptance_ids"].reverse()
    incoming["contract"]["task"]["acceptance_ids"].reverse()
    incoming["contract"]["obligations"].reverse()
    incoming["mapping"]["entries"].reverse()
    incoming["mapping"]["proofs"].reverse()
    for item in incoming["contract"]["obligations"]:
        item["conditions"]["fixture_refs"].reverse()
    for item in incoming["mapping"]["proofs"]:
        item["conditions"]["fixture_refs"].reverse()
        item["claims"].reverse()
    assert bind(package) == original


def test_fetch_exact_objects_without_git_state_writes_or_verification(package, tmp_path, monkeypatch):
    observer, _, _ = materialize_git_baseline(
        tmp_path / "observer", files={"observer.txt": "unrelated local branch"},
        user_name="Observer", user_email="observer@example.invalid", commit_message="unrelated")
    git(observer, "config", "remote.origin.url", package["remote"].as_posix())
    assert subprocess.run(("git", "-C", str(observer), "cat-file", "-e", package["main"]),
                          capture_output=True).returncode != 0
    package["repo"] = observer
    before, remote_before = state_without_objects(observer), state_without_objects(package["remote"])
    real_run, calls = subprocess.run, []

    def only_read_only_git(argv, *args, **kwargs):
        assert argv[0] == "git"  # No shell, provider, verifier or Executor child.
        tail = list(argv[3:])
        if tail[0] == "--no-replace-objects":
            tail = tail[1:]
            while tail[0] == "-c":
                tail = tail[2:]
        command = tail[0]
        assert command in {"symbolic-ref", "config", "remote", "ls-remote", "fetch",
                           "rev-parse", "cat-file", "ls-tree"}
        if command == "config":
            assert tail[1].startswith("--get")
        if command == "remote":
            assert tail[1] == "get-url"
        if command == "fetch":
            assert {"--no-tags", "--no-write-fetch-head", "--refmap=",
                    "--no-auto-maintenance", "--no-recurse-submodules"} <= set(tail)
            assert tail[-2:] == [package["remote"].as_posix(), package["main"]]
        calls.append(command)
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", only_read_only_git)
    result = bind(package)
    assert result.main_after == package["main"]
    assert calls.count("ls-remote") == 2 and calls.count("fetch") == 1
    assert state_without_objects(observer) == before
    assert state_without_objects(package["remote"]) == remote_before


@pytest.mark.parametrize("role", ["task", "contract", "mapping"])
@pytest.mark.parametrize("fault", ["missing", "wrong-blob", "wrong-tree", "blob-as-commit", "wrong-type"])
def test_missing_forged_swapped_paths_and_object_types_fail_closed(package, role, fault):
    source = package["sources"][role]
    changes = {
        "missing": dict(path="missing/file.json"),
        "wrong-blob": dict(blob_sha=package["sources"]["mapping" if role != "mapping" else "contract"].blob_sha),
        "wrong-tree": dict(tree_sha=source.blob_sha),
        "blob-as-commit": dict(source_sha=source.blob_sha),
        "wrong-type": dict(blob_sha=source.source_sha),
    }[fault]
    package["sources"][role] = replace(source, **changes)
    with pytest.raises(ReviewTransportError):
        bind(package)


@pytest.mark.parametrize("path", ["../contract.json", "proof/../contract.json", "/proof/contract.json",
                                  "proof\\contract.json", "proof/*.json", "proof//contract.json",
                                  "proof/./contract.json", ":(glob)*", ".git/config"])
def test_nonnormalized_paths_are_rejected_before_git(package, path, monkeypatch):
    package["sources"]["contract"] = replace(package["sources"]["contract"], path=path)
    monkeypatch.setattr(transport, "_git_cmd", lambda *a, **kw: pytest.fail("unexpected Git call"))
    with pytest.raises(ReviewTransportError):
        bind(package)


def test_swapped_role_paths_are_not_authenticated_by_matching_blob_labels(package):
    package["sources"]["contract"], package["sources"]["mapping"] = (
        package["sources"]["mapping"], package["sources"]["contract"])
    for role in ("contract", "mapping"):
        source = package["sources"][role]
        package["declaration"]["sources"][role] = dict(source_sha=source.source_sha, blob_sha=source.blob_sha)
    with pytest.raises(ReviewTransportError):
        bind(package)


def test_unanchored_local_commit_and_locally_forged_grafts_do_not_establish_provenance(package):
    (package["repo"] / "subject.txt").write_text("local-only commit", encoding="utf-8")
    local = commit(package["repo"], paths=["subject.txt"])
    package["sources"]["contract"] = locator(package["repo"], local, PATHS["contract"])
    # Git's graph convenience commands could see this forged local ancestry.
    graft = package["repo"] / ".git/info/grafts"
    graft.parent.mkdir(exist_ok=True)
    graft.write_text(f"{package['main']} {local}\n{local}\n", encoding="ascii")
    with pytest.raises(ReviewTransportError, match="not anchored"):
        bind(package)


def test_non_git_line_separators_in_commit_author_cannot_forge_ancestry(package):
    (package["repo"] / "subject.txt").write_text("unanchored", encoding="utf-8")
    local = commit(package["repo"], paths=["subject.txt"])
    package["sources"]["contract"] = locator(package["repo"], local, PATHS["contract"])
    main = subprocess.run(("git", "-C", str(package["repo"]), "cat-file", "commit", package["main"]),
                          capture_output=True, check=True).stdout
    lines = main.split(b"\n")
    for index, line in enumerate(lines):
        if line.startswith(b"author "):
            lines[index] = line + b"\vparent " + local.encode("ascii")
            break
    raw = b"\n".join(lines)
    forged = _write_object(package["repo"] / ".git/objects", "commit", raw)
    assert _write_object(package["remote"] / "objects", "commit", raw) == forged
    (package["remote"] / "refs/heads/main").write_text(forged + "\n", encoding="ascii")
    package["main"] = forged
    with pytest.raises(ReviewTransportError, match="not anchored"):
        bind(package)


def test_changed_current_source_path_cannot_reuse_ancestor_blob(package):
    old = package["sources"]["mapping"]
    (package["repo"] / PATHS["mapping"]).write_bytes(encoded(dict(unrelated="changed")))
    package["main"] = commit(package["repo"], package["remote"], [PATHS["mapping"]])
    assert package["sources"]["mapping"] == old
    with pytest.raises(ReviewTransportError, match="stale"):
        bind(package)


@pytest.mark.parametrize("kind", ["commit", "tree", "blob"])
def test_forged_local_object_payload_cannot_keep_the_claimed_object_identity(package, kind):
    source = package["sources"]["contract"]
    sha = dict(commit=source.source_sha, tree=source.tree_sha, blob=source.blob_sha)[kind]
    target = package["repo"] / ".git/objects" / sha[:2] / sha[2:]
    # Only this sandbox's loose object is changed, never a template alternate.
    assert target.is_file()
    header, body = zlib.decompress(target.read_bytes()).split(b"\0", 1)
    assert header.startswith(kind.encode() + b" ")
    body = body[:-1] + bytes([body[-1] ^ 1]) if kind == "tree" else body + b" "
    target.write_bytes(zlib.compress(f"{kind} {len(body)}\0".encode() + body))
    with pytest.raises(ReviewTransportError):
        bind(package)


@pytest.mark.parametrize("fault", ["missing-upstream", "local-upstream", "duplicate-upstream",
                                   "duplicate-url", "detached-ambiguous", "unexpected-remote", "url-rewrite"])
def test_canonical_binding_is_required_without_origin_or_branch_fallback(package, tmp_path, fault):
    repo = package["repo"]
    if fault == "missing-upstream":
        git(repo, "config", "--unset-all", "branch.main.remote")
    elif fault == "local-upstream":
        git(repo, "config", "branch.main.remote", ".")
    elif fault == "duplicate-upstream":
        git(repo, "config", "--add", "branch.main.remote", "origin")
    elif fault == "duplicate-url":
        git(repo, "config", "--add", "remote.origin.url", package["remote"].as_posix())
    elif fault == "detached-ambiguous":
        git(repo, "checkout", "--detach")
        git(repo, "config", "branch.other.remote", "other")
    elif fault == "url-rewrite":
        git(repo, "config", f"url.{(tmp_path / 'unexpected.git').as_posix()}.insteadOf",
            package["remote"].as_posix())
    else:
        # Same immutable objects at another URL cannot satisfy the expected root.
        replay = tmp_path / "replay.git"
        subprocess.run(("git", "clone", "--bare", str(package["remote"]), str(replay)),
                       check=True, capture_output=True)
        git(repo, "config", "remote.origin.url", str(replay))
    with pytest.raises(ReviewTransportError):
        bind(package)


def test_detached_reader_requires_unanimous_configured_canonical_remote(package):
    git(package["repo"], "checkout", "--detach")
    assert bind(package).remote == "origin"


def test_missing_or_stale_main_is_rejected(package):
    with pytest.raises(ReviewTransportError, match="main"):
        bind(package, expected_main_sha=package["sources"]["task"].source_sha)
    (package["remote"] / "refs/heads/main").unlink()
    with pytest.raises(ReviewTransportError, match="main"):
        bind(package)


def test_main_movement_during_observation_fails_final_recheck(package, monkeypatch):
    old = package["main"]
    (package["repo"] / "subject.txt").write_text("next main", encoding="utf-8")
    moved = commit(package["repo"], package["remote"], ["subject.txt"])
    main_ref = package["remote"] / "refs/heads/main"
    main_ref.write_text(old + "\n", encoding="ascii")
    real_query, observations = transport._exact_remote_refs, []

    def move_at_recheck(repo, remote, *patterns):
        observations.append(patterns)
        if len(observations) == 2:
            main_ref.write_text(moved + "\n", encoding="ascii")
        return real_query(repo, remote, *patterns)

    monkeypatch.setattr(transport, "_exact_remote_refs", move_at_recheck)
    with pytest.raises(ReviewTransportError, match="moved"):
        bind(package)
    assert len(observations) == 2


def test_conflicting_main_identity_on_final_recheck_fails_closed(package, monkeypatch):
    original, calls = transport._git_cmd, []

    def conflict(repo, *args, **kwargs):
        value = original(repo, *args, **kwargs)
        if args[0] == "ls-remote":
            calls.append(args)
            if len(calls) == 2:
                return 0, value[1] + f"\n{'f' * 40}\trefs/heads/main", ""
        return value

    monkeypatch.setattr(transport, "_git_cmd", conflict)
    with pytest.raises(ReviewTransportError, match="ambiguous"):
        bind(package)


def test_configured_remote_change_at_final_recheck_fails_closed(package, monkeypatch):
    original, calls = transport._l1_remote, []

    def change_binding(repo, url):
        calls.append(url)
        if len(calls) == 2:
            git(repo, "config", "remote.origin.url", package["remote"].as_posix() + "-other")
        return original(repo, url)

    monkeypatch.setattr(transport, "_l1_remote", change_binding)
    with pytest.raises(ReviewTransportError, match="remote URL"):
        bind(package)


@pytest.mark.parametrize("role", ["contract", "mapping"])
@pytest.mark.parametrize("content", [
    b'{"schema":"one","schema":"two"}', b'{"nested":{"id":1,"id":2}}',
    b'{"x":"\xff"}', b'{"x":NaN}', b'{"x":1e999}', b'{"x":"\\ud800"}',
    b"[" * 40 + b"]" * 40, b" " * (transport.L1_SOURCE_MAX_BYTES + 1),
])
def test_committed_semantic_json_is_duplicate_rejecting_strict_utf8_and_bounded(package, role, content):
    replace_source(package, role, content)
    with pytest.raises(ReviewTransportError):
        bind(package)


@pytest.mark.parametrize("content", [
    TASK + b"revision: 2\n", TASK.replace(b"revision: 1", b"revision: \xff"),
    TASK.replace(b"goal: Preserve declared safety.", b"goal: &label Preserve declared safety."),
    b" " * (transport.L1_TASK_MAX_BYTES + 1),
])
def test_task_bytes_are_bounded_strict_and_unambiguous(package, content):
    replace_source(package, "task", content)
    with pytest.raises(ReviewTransportError):
        bind(package)


@pytest.mark.parametrize("content", [
    b'{"schema":"one","schema":"two"}', b'{"x":"\xff"}', b"[]", b"{} {}",
    b"[" * 40 + b"]" * 40, b" " * (transport.L1_INTAKE_MAX_BYTES + 1),
])
def test_intake_decode_fails_before_git_for_malformed_or_unbounded_bytes(package, content, monkeypatch):
    monkeypatch.setattr(transport, "_git_cmd", lambda *a, **kw: pytest.fail("unexpected Git call"))
    with pytest.raises(ReviewTransportError):
        bind_canonical_semantic_proof_sources(
            package["repo"], content, expected_remote_url=package["remote"].as_posix(),
            expected_main_sha=package["main"], expected_candidate_sha=CANDIDATE,
            sources=package["sources"])


@pytest.mark.parametrize("fault", ["task", "revision", "acceptance", "digest", "candidate", "policy",
                                   "missing-mapping", "source-label", "review-label", "condition"])
def test_incoming_claims_cannot_substitute_for_authenticated_source_content(package, fault):
    incoming = package["declaration"]
    if fault == "task":
        incoming["task"]["task_id"] = "TASK-OTHER"
    elif fault == "revision":
        incoming["task"]["revision"] = 2
    elif fault == "acceptance":
        incoming["task"]["acceptance_ids"] = ["AC1"]
    elif fault == "digest":
        incoming["task"]["envelope_digest"] = "f" * 64
    elif fault == "candidate":
        incoming["candidate_sha"] = "f" * 40
    elif fault == "policy":
        incoming["schema"] = "minimum-sufficient-v2"
    elif fault == "missing-mapping":
        del incoming["mapping"]
    elif fault == "source-label":
        incoming["sources"]["contract"]["blob_sha"] = "f" * 40
    elif fault == "review-label":
        incoming["mapping"]["provenance"]["review_ref"] = "forged-review-pass"
    else:
        incoming["mapping"]["proofs"][0]["conditions"]["ordering_ref"] = "different-order-v1"
    with pytest.raises(ReviewTransportError):
        bind(package)


def test_authenticated_bogus_review_labels_still_do_not_establish_review(package):
    mapping = package["declaration"]["mapping"]
    mapping["provenance"]["review_ref"] = "bogus-independent-review-pass"
    replace_source(package, "mapping", encoded(mapping))
    result = bind(package)
    assert result.intake.coverage.mapping.provenance.review_ref == "bogus-independent-review-pass"
    assert "independent_semantic_mapping_review" in result.unestablished
    assert "independent_human_approval" in result.unestablished


@pytest.mark.parametrize("fault", ["task", "revision", "acceptance", "candidate", "mapping-condition",
                                   "mapping-missing-entry", "contract-schema"])
def test_authenticated_sources_must_satisfy_existing_task_and_complete_mapping_validators(package, fault):
    data, mapping = package["declaration"]["contract"], package["declaration"]["mapping"]
    if fault == "task":
        data["task"]["task_id"] = "TASK-OTHER"
    elif fault == "revision":
        data["task"]["revision"] = 2
    elif fault == "acceptance":
        data["task"]["acceptance_ids"].append("AC3")
    elif fault == "candidate":
        for item in data["obligations"]:
            item["conditions"]["candidate_sha"] = "f" * 40
    elif fault == "contract-schema":
        data["schema"] = "proof-coverage-future"
    elif fault == "mapping-condition":
        mapping["proofs"][0]["conditions"]["fixture_refs"] = ["different-fixture-v1"]
    else:
        mapping["entries"].pop()
    if fault in {"mapping-condition", "mapping-missing-entry"}:
        replace_source(package, "mapping", encoded(mapping))
    else:
        replace_source(package, "contract", encoded(data))
    with pytest.raises(ReviewTransportError):
        bind(package)


def test_expected_candidate_is_an_independent_mandatory_pin(package):
    with pytest.raises(ReviewTransportError):
        bind(package, expected_candidate_sha="f" * 40)


def test_historical_task_reader_and_transport_resolution_remain_separate(package):
    source = package["sources"]["task"]
    assert transport.read_remote_task(package["repo"], commit_sha=source.source_sha,
                                      task_id="TASK-SYNTHETIC") == TASK
    git(package["repo"], "checkout", "--detach")
    assert bind(package).remote == "origin"
    with pytest.raises(ReviewTransportError, match="current branch"):
        transport.resolve_transport_remote(package["repo"])
