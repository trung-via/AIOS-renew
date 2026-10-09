"""Disposable local fixtures are content tests, never admitted source EVIDENCE."""

import copy
import hashlib
import json
import subprocess
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import aios_renew.runtime as runtime
import aios_renew.runtime_provenance_issuer as issuer
import aios_renew.review_transport as transport
from aios_renew.artifacts import Claim, Result, ResultPackage, validate_evidence
from aios_renew.run import Run
from aios_renew.task import parse_task


TASK = """task_id: TASK-334-FIXTURE
revision: 1
goal: Capture terminal content without inventing authority.
problem: Content and admission are distinct.
assumptions: []
scope:
  inspect: []
  modify: [OUTPUT.txt]
non_goals: []
constraints:
  hard: []
acceptance:
  - id: AC1
    condition: Implement the fixture output.
verification:
  required: ['verify-source-fixture --password command-canary']
"""
RUN_ID = "RUN-334-FIXTURE-001"


def git(repo, *args, content=None):
    result = subprocess.run(("git", "-C", str(repo), *args), input=content,
                            capture_output=True, check=True)
    return result.stdout.decode("utf-8").strip()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def tree_commit(repo, files):
    """Create a synthetic local artifact tree for the offline diagnostic only."""
    entries = []
    for name, content in sorted(files.items()):
        blob = git(repo, "hash-object", "-w", "--stdin", content=content)
        entries.append(f"100644 blob {blob}\t{name}\n")
    tree = git(repo, "mktree", content="".join(entries).encode())
    ai = git(repo, "mktree", content=f"040000 tree {tree}\ttransport\n".encode())
    root = git(repo, "mktree", content=f"040000 tree {ai}\t.ai\n".encode())
    return git(repo, "commit-tree", root, "-m", "local content fixture")


@pytest.fixture
def terminal(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--initial-branch=main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    task_file = repo / ".ai" / "tasks" / "TASK-334-FIXTURE.yaml"
    task_file.parent.mkdir(parents=True)
    task_file.write_text(TASK, encoding="utf-8")
    git(repo, "add", ".ai")
    git(repo, "commit", "-m", "fixture task")
    base = git(repo, "rev-parse", "HEAD")
    task = parse_task(TASK)
    (repo / "OUTPUT.txt").write_text("implementation", encoding="utf-8")
    git(repo, "add", "OUTPUT.txt")
    git(repo, "commit", "-m", "fixture candidate")
    subject = git(repo, "rev-parse", "HEAD")
    run = Run.from_task(run_id=RUN_ID, task=task, executor="codex",
                        base_sha=base, workspace=str(repo))
    root = repo / ".git" / "aios"
    state = SimpleNamespace(**{key: root / name for key, name in (
        ("staging", "staging"), ("preverification", "pre-verification"),
        ("verification", "verification"), ("results", "results"),
        ("failures", "failures"), ("observations", "observations"), ("repairs", "repairs"))})
    run_path = root / "runs" / f"{RUN_ID}.json"
    write_json(run_path, asdict(run))
    profile_path = root / "execution-profiles" / f"{RUN_ID}.json"
    # This is content, deliberately NOT an admission credential.
    write_json(profile_path, {"run_id": RUN_ID, "executor": "codex"})
    package = ResultPackage(Result(subject, (Claim("C1", ("AC1",), "Implemented output.", ()),),
                                   ("OUTPUT.txt",), ()), ())
    calls = []

    def verification_runner(command, **kwargs):
        calls.append(("verification", command))
        return subprocess.CompletedProcess(command, 0, b"raw-secret-canary\n", b"credential-canary\n")

    def post_pass(repo, **kwargs):
        calls.append(("transport", kwargs))
        assert "runtime_source" not in kwargs
        assert completion.source_observation.status == "BLOCK"
        assert kwargs["result_path"].is_file()

    monkeypatch.setattr(runtime, "transport_post_pass", post_pass)
    completion = runtime.RuntimeCompletion(repo=repo, state=state, task=task, run=run,
        run_path=run_path, verification_runner=verification_runner,
        observation_tracker=None, error_type=RuntimeError)
    outcome = completion.complete(package, runtime.primary_completion_policy(task, base_sha=base))
    result = json.loads(outcome.result_path.read_bytes())
    canonical = ResultPackage(replace(package.result, claims=(replace(package.result.claims[0],
        evidence=(result["evidence"][0]["evidence_id"],)),)),
        tuple(validate_evidence(item) for item in result["evidence"]))
    return SimpleNamespace(repo=repo, root=root, state=state, completion=completion,
        outcome=outcome, package=canonical, calls=calls, run_path=run_path, profile_path=profile_path)


def capture(fixture):
    return issuer._capture_completion(fixture.completion, fixture.package, "PRIMARY")


def source(fixture):
    observation = fixture.completion.source_observation
    assert observation.source_path is not None
    return json.loads(observation.source_path.read_bytes())["source"]


def install_content_transport(fixture, metadata=None):
    files = {"run.json": fixture.run_path.read_bytes(),
             "result.json": fixture.outcome.result_path.read_bytes(),
             "execution-profile.json": fixture.profile_path.read_bytes()}
    if metadata is not None:
        files["runtime-source-v1.json"] = issuer._dump(metadata)
    commit = tree_commit(fixture.repo, files)
    git(fixture.repo, "update-ref", f"refs/heads/aios/artifacts/{RUN_ID}", commit)
    git(fixture.repo, "update-ref", f"refs/heads/aios/review/{RUN_ID}", fixture.outcome.head_sha)
    return commit


def install_review(fixture, *, verdict="PASS", subject=None, ambiguous=False):
    text = (f"review_id: REVIEW-FIXTURE\nreviewed_sha: {subject or fixture.outcome.head_sha}\n"
            f"mode: PRIMARY\nverdict: {verdict}\nacceptance:\n  AC1: PASS\nfindings: []\n")
    blob = git(fixture.repo, "hash-object", "-w", "--stdin", content=text.encode())
    entries = f"100644 blob {blob}\tREVIEW-FIXTURE.yaml\n"
    if ambiguous:
        entries += f"100644 blob {blob}\tSECOND.yaml\n"
    reviews = git(fixture.repo, "mktree", content=entries.encode())
    ai = git(fixture.repo, "mktree", content=f"040000 tree {reviews}\treviews\n".encode())
    root = git(fixture.repo, "mktree", content=f"040000 tree {ai}\t.ai\n".encode())
    decision = git(fixture.repo, "commit-tree", root, "-m", "independent-looking content only")
    git(fixture.repo, "update-ref", f"refs/heads/aios/review-decision/{RUN_ID}", decision)
    return decision


def test_real_completion_hook_captures_content_after_verification_before_transport(terminal):
    observation = terminal.completion.source_observation
    assert observation.status == "BLOCK"
    assert issuer.ADMISSION_BLOCKER in observation.reasons
    assert not observation.issuer_authenticated
    assert [call[0] for call in terminal.calls] == ["verification", "transport"]
    metadata = source(terminal)
    assert metadata["candidate"]["subject_sha"] == terminal.outcome.head_sha
    assert metadata["run"]["base_sha"] == terminal.completion.run.base_sha
    assert metadata["task"]["record"]["blob_sha"] == git(terminal.repo, "rev-parse",
        f"{terminal.completion.run.base_sha}:.ai/tasks/TASK-334-FIXTURE.yaml")
    assert metadata["result"]["sha256"] == hashlib.sha256(terminal.outcome.result_path.read_bytes()).hexdigest()
    assert not observation.reviewer_pass


def test_safe_metadata_and_protected_original_raw(terminal):
    metadata = source(terminal)
    encoded = issuer._dump(metadata)
    assert issuer.decode_content_source(encoded) == metadata
    for private in (b"raw-secret-canary", b"credential-canary", b"command-canary", b"raw_path", str(terminal.repo).encode()):
        assert private not in encoded
    raw = Path(terminal.package.evidence[0].raw_path).read_bytes()
    assert b"raw-secret-canary" in raw
    assert metadata["evidence"][0]["raw"] == {
        "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "availability": "LOCAL_CAPTURED"}
    assert not git(terminal.repo, "status", "--porcelain")


def test_capture_is_idempotent_and_raw_swap_conflicts_without_overwrite(terminal):
    observation = capture(terminal)
    original = observation.source_path.read_bytes()
    assert capture(terminal) == observation
    Path(terminal.package.evidence[0].raw_path).write_bytes(b"swapped")
    replay = capture(terminal)
    assert "SOURCE_CAPTURE_REPLAY_CONFLICT" in replay.reasons
    assert observation.source_path.read_bytes() == original


@pytest.mark.parametrize("change,reason", [
    ("run", "SWAPPED_RUN"), ("result", "SWAPPED_RESULT_OR_EVIDENCE"),
    ("subject", "PRETERMINAL_OR_SWAPPED_SUBJECT"),
    ("state", "UNPROTECTED_RUNTIME_STATE"), ("profile", "SWAPPED_EXECUTION_PROFILE"),
])
def test_swapped_completion_inputs_fail_closed(terminal, change, reason):
    if change == "run":
        write_json(terminal.run_path, {**asdict(terminal.completion.run), "base_sha": "a" * 40})
    elif change == "result":
        data = json.loads(terminal.outcome.result_path.read_bytes())
        data["evidence"][0]["run_id"] = "RUN-OUTSIDER-001"
        write_json(terminal.outcome.result_path, data)
    elif change == "subject":
        terminal.completion.verification_subject_sha = "a" * 40
    elif change == "state":
        terminal.state.results = terminal.repo / "public-state"
    else:
        write_json(terminal.profile_path, {"run_id": "RUN-OUTSIDER-001", "executor": "codex"})
    observation = capture(terminal)
    assert observation.status == "BLOCK" and reason in observation.reasons
    assert not observation.issuer_authenticated


def test_forged_runtime_and_preterminal_have_no_issuance(terminal):
    outsider = SimpleNamespace(**vars(terminal.completion))
    assert "UNOWNED_RUNTIME_INSTANCE" in issuer._capture_completion(outsider, terminal.package, "PRIMARY").reasons
    terminal.completion.verification_subject_sha = None
    assert "PRETERMINAL_OR_SWAPPED_SUBJECT" in capture(terminal).reasons
    assert issuer.observe_issued_source(RUN_ID).status == "UNKNOWN"


def test_missing_and_external_raw_never_gets_digest_authority(terminal, tmp_path):
    raw = Path(terminal.package.evidence[0].raw_path)
    raw.unlink()
    assert "LOCAL_RECORD_UNAVAILABLE" in capture(terminal).reasons
    external = tmp_path / "private.raw"
    external.write_bytes(b"outsider")
    terminal.package = replace(terminal.package, evidence=(replace(terminal.package.evidence[0], raw_path=str(external)),))
    write_json(terminal.outcome.result_path, runtime.result_package_data(terminal.package))
    assert "UNPROTECTED_LOCAL_PATH" in capture(terminal).reasons


def test_raw_symlink_size_and_torn_read_refusal(tmp_path, monkeypatch):
    root = tmp_path / "verification"
    root.mkdir()
    path = root / "raw"
    path.write_bytes(b"original")
    monkeypatch.setattr(issuer, "MAX_RAW", 3)
    with pytest.raises(issuer.SourceError, match="RECORD_LIMIT"):
        issuer._raw_identity(path, root)
    monkeypatch.setattr(issuer, "MAX_RAW", 100)
    real_stat = issuer.os.fstat
    reads = []

    def changed_stat(fd):
        value = real_stat(fd)
        reads.append(value)
        if len(reads) == 2:
            return SimpleNamespace(st_dev=value.st_dev, st_ino=value.st_ino,
                st_size=value.st_size, st_mtime_ns=value.st_mtime_ns + 1,
                st_ctime_ns=value.st_ctime_ns, st_mode=value.st_mode)
        return value

    monkeypatch.setattr(issuer.os, "fstat", changed_stat)
    with pytest.raises(issuer.SourceError, match="TORN_LOCAL_RECORD"):
        issuer._raw_identity(path, root)
    monkeypatch.setattr(issuer.os, "fstat", real_stat)
    # Deterministically exercise the reparse/symlink refusal without depending
    # on Windows developer-mode privileges to create an actual link.
    real_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda value: value == path or real_symlink(value))
    with pytest.raises(issuer.SourceError, match="UNPROTECTED_LOCAL_PATH"):
        issuer._raw_identity(path, root)


def test_unreviewed_then_delayed_review_content_stays_unknown(terminal):
    artifact = install_content_transport(terminal, source(terminal))
    before = issuer.inspect_content_source(terminal.repo, RUN_ID)
    assert before.status == "UNKNOWN" and before.review_content == "UNREVIEWED"
    decision = install_review(terminal)
    after = issuer.inspect_content_source(terminal.repo, RUN_ID)
    assert after.status == "UNKNOWN" and after.review_content == "CONTENT_CONSISTENT"
    assert after.recorded_verdict == "PASS" and not after.reviewer_pass
    assert after.artifact_sha == artifact and after.decision_sha == decision
    assert after.raw_state == "RAW_UNAVAILABLE" and not after.issuer_authenticated
    assert issuer.REVIEW_BLOCKER in after.reasons


@pytest.mark.parametrize("case", ["ambiguous", "stale", "failure", "main", "tree", "run", "raw"])
def test_adversarial_content_and_currentness(terminal, case):
    metadata = copy.deepcopy(source(terminal))
    if case == "tree":
        metadata["candidate"]["tree_sha"] = "a" * 40
    elif case == "run":
        metadata["run"]["record"]["sha256"] = "a" * 64
    elif case == "raw":
        metadata["evidence"][0]["raw"]["sha256"] = "a" * 64
    artifact = install_content_transport(terminal, metadata)
    if case == "ambiguous":
        install_review(terminal, ambiguous=True)
    elif case == "stale":
        install_review(terminal, subject=terminal.completion.run.base_sha)
    elif case == "failure":
        git(terminal.repo, "update-ref", f"refs/heads/aios/failure-artifacts/{RUN_ID}", artifact)
    elif case == "main":
        task = terminal.repo / ".ai" / "tasks" / "TASK-334-FIXTURE.yaml"
        task.write_text(TASK.replace("revision: 1", "revision: 2"), encoding="utf-8")
        git(terminal.repo, "add", ".ai")
        git(terminal.repo, "commit", "-m", "stale task")
    outcome = issuer.inspect_content_source(terminal.repo, RUN_ID)
    if case == "raw":
        # No authorized independent raw retrieval exists: a forged hash cannot
        # become a match, even when all other records are internally coherent.
        assert outcome.status == "UNKNOWN" and "RAW_UNAVAILABLE" in outcome.reasons
    else:
        assert outcome.status == "BLOCK"
    assert not outcome.reviewer_pass and not outcome.issuer_authenticated


def test_before_after_drift_does_not_claim_cas_or_aba(terminal, monkeypatch):
    install_content_transport(terminal, source(terminal))
    install_review(terminal)
    original = issuer._refs
    calls = []

    def drifting(repo, run_id):
        pins = original(repo, run_id)
        calls.append(pins)
        if len(calls) == 2:
            pins["refs/heads/main"] = "a" * 40
        return pins

    monkeypatch.setattr(issuer, "_refs", drifting)
    outcome = issuer.inspect_content_source(terminal.repo, RUN_ID)
    assert "REF_SNAPSHOT_DRIFT" in outcome.reasons
    assert not outcome.reviewer_pass and not outcome.atomic_currentness
    assert outcome.recorded_verdict is None


def test_legacy_sources_are_unissued_and_missing_sources_unknown(terminal):
    assert "TERMINAL_SOURCE_MISSING" in issuer.inspect_content_source(terminal.repo, RUN_ID).reasons
    install_content_transport(terminal)
    legacy = issuer.inspect_content_source(terminal.repo, RUN_ID)
    assert legacy.status == "UNKNOWN" and "HISTORIC_SOURCE_UNISSUED" in legacy.reasons


@pytest.mark.parametrize("forgery,reason", [
    ("issuer", "FORGED_ISSUER_ASSERTION"), ("effect", "UNAUTHORIZED_EFFECT"),
    ("extra", "INVALID_SOURCE_FIELDS"), ("version", "UNSUPPORTED_SOURCE_VERSION_OR_PHASE"),
])
def test_strict_versioned_non_authorizing_metadata(terminal, forgery, reason):
    metadata = source(terminal)
    if forgery == "issuer":
        metadata["authority"] = "AUTHENTICATED_RUNTIME"
    elif forgery == "effect":
        metadata["effects"]["proof_reuse"] = True
    elif forgery == "version":
        metadata["version"] = True
    else:
        metadata["credential"] = "canary"
    with pytest.raises(issuer.SourceError) as error:
        issuer.decode_content_source(issuer._dump(metadata))
    assert error.value.reason == reason


def test_public_transport_refuses_metadata_before_network_or_object_write(terminal, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unowned source reached transport Git")
    monkeypatch.setattr(transport, "_git_cmd", forbidden)
    metadata = issuer._dump(source(terminal))
    with pytest.raises(transport.ReviewTransportError) as error:
        transport.transport_post_pass(terminal.repo, run_id=RUN_ID,
            head_sha=terminal.outcome.head_sha, run_path=terminal.run_path,
            result_path=terminal.outcome.result_path, runtime_source=metadata)
    assert error.value.reason == issuer.ADMISSION_BLOCKER
    with pytest.raises(transport.ReviewTransportError):
        transport._create_artifacts_commit(terminal.repo, run_id=RUN_ID,
            run_path=terminal.run_path, result_path=terminal.outcome.result_path,
            runtime_source=metadata)
    assert transport._require_owned_runtime_source(None) is None


def test_capture_persistence_error_never_masks_original_transport_cause(terminal, monkeypatch):
    monkeypatch.setattr(runtime, "_capture_completion", lambda *args: (_ for _ in ()).throw(OSError("private-canary")))
    # The exception guard is intentionally independent of the issuer's own
    # typed failures. Existing ordering/terminal transport remains the owner.
    assert terminal.outcome.result_path.is_file()
    def fail_transport(*args, **kwargs):
        raise transport.ReviewTransportError("original transport cause")
    monkeypatch.setattr(runtime, "transport_post_pass", fail_transport)
    structural = replace(terminal.package, result=replace(terminal.package.result,
        claims=(replace(terminal.package.result.claims[0], evidence=()),)), evidence=())
    with pytest.raises(RuntimeError, match="original transport cause"):
        terminal.completion.complete(structural, runtime.primary_completion_policy(
            terminal.completion.task, base_sha=terminal.completion.run.base_sha))
    assert terminal.completion.source_observation.reasons == (
        issuer.ADMISSION_BLOCKER, "SOURCE_CAPTURE_FAILED")
    assert terminal.outcome.result_path.is_file()


def test_diagnostic_has_no_network_and_all_effects_disabled(terminal, monkeypatch):
    install_content_transport(terminal, source(terminal))
    before = git(terminal.repo, "show-ref")
    original = issuer._git
    def local_only(repo, *args, **kwargs):
        assert args[0] in {"show-ref", "ls-tree", "show", "cat-file", "hash-object", "rev-parse"}
        assert "-w" not in args
        return original(repo, *args, **kwargs)
    monkeypatch.setattr(issuer, "_git", local_only)
    outcome = issuer.inspect_content_source(terminal.repo, RUN_ID)
    assert outcome.status == "UNKNOWN" and outcome.activation == "NOT_ACTIVATED"
    assert git(terminal.repo, "show-ref") == before
    assert source(terminal)["effects"] == issuer._EFFECTS
    assert all(value is False for key, value in issuer._EFFECTS.items() if key != "state")


def test_no_caller_selected_canonical_root_ref_or_issuer():
    with pytest.raises(TypeError):
        issuer.observe_issued_source(RUN_ID, repo=Path("."))
    with pytest.raises(issuer.SourceError):
        issuer.observe_issued_source("../main")
    with pytest.raises(issuer.SourceError, match="DUPLICATE_FIELD"):
        issuer.decode_content_source(b'{"version":1,"version":1}')
