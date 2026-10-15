import json
import subprocess
from pathlib import Path

import pytest

import aios_renew.review_transport as review_transport
from tests.git_fixture_support import (
    commit_fixture_state,
    materialize_git_baseline,
    read_git_ref,
)
from aios_renew.review_transport import (
    ReviewTransportError,
    read_remote_task,
    resolve_remote_primary_recovery,
    resolve_remote_repair_recovery,
    resolve_remote_remediation_lineages,
    resolve_remote_run_namespace,
    resolve_remote_task_lifecycle,
    resolve_detached_observation_remote,
    resolve_transport_remote,
    task_run_prefix,
    transport_failure,
    transport_post_pass,
    validate_runtime_failure_binding,
)


def _failure_transport_recovery_fixture(
    *, run_id="RUN-058-901", task=None, base_sha="a" * 40, head_sha="b" * 40,
    changed_files=("subject.txt",), nodes=8101, failures=84,
):
    """Generate private synthetic proof at test time; never store historical raw."""
    from aios_renew.verification_contract import verification_digest

    task = task or {"id": "TASK-058", "revision": 2}
    command = "python scripts/aios_parallel_full_suite.py"
    profile = dict(profile="bounded-parallel-full-suite-v1", workers=12,
        distribution="load", max_worker_restart=0, collect_only=False,
        environment_digest="c" * 64, isolation_policy="clean-clone-external-temp-v1",
        observer_blob="d" * 40, selected_policy_blob="e" * 40,
        repository_tool_blobs={"tests/conftest.py": "f" * 40},
        selection_provenance={"authority": "HUMAN", "task_id": "TASK-231"})
    toolchain = dict(python_implementation="CPython", python_version="3.14.7",
        python_executable="C:/private/runtime/python.exe", platform_system="Windows",
        platform_machine="AMD64", pytest_version="8.4.2", pytest_xdist_version="3.8.0",
        installed_distributions_digest="1" * 64)
    reports = []
    for number in range(nodes):
        node = f"tests/test_synthetic.py::test_case[{number:05d}-secret_fixture]"
        for phase in ("setup", "call", "teardown"):
            failed = phase == "call" and number < failures
            report = dict(nodeid=node, phase=phase, outcome="FAIL" if failed else "PASS",
                captured_output="private capture secret_fixture C:/private/runtime/ " + "x" * 80)
            if failed:
                detail = "private traceback secret_fixture C:/private/runtime/ " + "y" * 512
                report.update(detail=detail, fingerprint=verification_digest(detail),
                              profile=profile, toolchain=toolchain)
            reports.append(report)
    candidate = dict(subject_sha=head_sha, command=command, profile=profile,
        toolchain=toolchain, complete=False, unstable=True, exit_code=2,
        failure_count=failures, reports=reports)
    base_profile = dict(profile, observer_blob="2" * 40,
                        repository_tool_blobs={"tests/conftest.py": "3" * 40})
    base = dict(candidate, subject_sha=base_sha, profile=base_profile, reports=[], failure_count=0)
    binding = dict(subject_sha=head_sha, base_sha=base_sha, tree_sha="4" * 40, command=command,
        profile=profile, toolchain=toolchain, envelope_digest="5" * 64,
        changed_files_digest=verification_digest(list(changed_files)),
        failure_set_digest=verification_digest(base))
    reference = dict(policy="minimum-sufficient-v2", disposition="EXECUTED",
        evidence_id=run_id + "-V001", run_id=run_id, binding=binding,
        candidate=candidate, candidate_digest=verification_digest(candidate),
        raw_path="C:/private/runtime/candidate.raw", raw_digest="6" * 64,
        base=base, base_digest=verification_digest(base),
        base_raw_path="C:/private/runtime/base.raw", base_raw_digest="7" * 64,
        attribution=[dict(detail="private attribution secret_fixture")])
    run = dict(run_id=run_id, task=task, executor="codex", base_sha=base_sha,
               workspace="C:/private/runtime/workspace", head_sha=None, status="ACTIVE")
    failure = dict(kind="FAILURE", run_id=run_id, task=task, executor="codex",
        base_sha=base_sha, failed_head_sha=head_sha, phase="VERIFICATION",
        candidate=dict(transportable=True, repairable=True, dirty=False, descends_from_base=True,
                       changed_files=list(changed_files), outside_task_scope=[]),
        error=dict(type="RuntimeVerificationError", message="private diagnostics secret_fixture",
            native_diagnostics=dict(stdout="private capture secret_fixture"),
            executor_diagnostics=dict(stderr="private traceback secret_fixture"),
            verification=[dict(command=command, exit_code=2, summary="private secret_fixture",
                               verification=reference)]))
    content = json.dumps(failure, sort_keys=True, separators=(",", ":")).encode()
    return run, failure, content


def _native_failure_fixture(*, exit_code=None, reason="TIMEOUT", dirty=False):
    """Entirely synthetic native record, including deliberately private strings."""
    run = dict(run_id="RUN-358-001", task=dict(id="TASK-358", revision=1), executor="codex",
               base_sha="a" * 40, workspace="C:/private/synthetic-workspace", status="ACTIVE", head_sha=None)
    failure = dict(kind="FAILURE", run_id=run["run_id"], task=run["task"], executor=run["executor"],
        base_sha=run["base_sha"], failed_head_sha="b" * 40, phase="EXECUTION",
        candidate=dict(transportable=not dirty, repairable=not dirty, dirty=dirty,
                       descends_from_base=True, changed_files=[], outside_task_scope=[]),
        error=dict(type="CodexExecutionError", message="private credential=synthetic-secret",
            exit_code=exit_code, native_reason=reason,
            native_diagnostics=dict(limit_chars=4096,
                stdout=dict(availability="captured", text="credential=synthetic-secret C:/private/host", truncated=True),
                stderr=dict(availability="unavailable"))))
    return run, failure


@pytest.mark.parametrize("exit_code", [None, -1, 0, 17])
def test_native_failure_diagnostics_preserve_identity_unknown_exit_and_original_bytes(exit_code):
    from aios_renew.verification_contract import verification_digest
    run, private = _native_failure_fixture(exit_code=exit_code)
    original = json.dumps(private, indent=2).encode()
    public_bytes = review_transport.public_failure(original)
    public = review_transport.failure_document(public_bytes)
    assert review_transport.public_failure(original) == review_transport.public_failure(public_bytes) == public_bytes
    for key in ("run_id", "task", "executor", "base_sha", "failed_head_sha", "phase", "candidate"):
        assert public[key] == private[key]
    diagnostic = public["error"]["diagnostic"]
    assert diagnostic["category"] == "NATIVE_TIMEOUT"
    assert diagnostic["native_exit"] == dict(availability="UNKNOWN" if exit_code is None else "AVAILABLE", value=exit_code)
    assert diagnostic["capture"]["stdout"] == dict(availability="CAPTURED", truncated=True, partial=True, withheld=True)
    assert diagnostic["capture"]["stderr"]["availability"] == "UNAVAILABLE"
    assert public["public_projection"]["private_failure_digest"] == verification_digest(private)
    assert operator_failure_matches(private, public)
    assert json.dumps(private, indent=2).encode() == original
    assert all(value not in public_bytes for value in (b"synthetic-secret", b"C:/private", b"credential="))
    context = review_transport.failure_context(public, artifact_sha="c" * 40, candidate_available=True)
    review_transport.validate_failure_context(context)
    assert context["source"]["artifact_sha"] == "c" * 40
    assert context["proof"] == "NON_PASS_DIAGNOSTIC" and context["run_id"] == run["run_id"]


@pytest.mark.parametrize("text,category", [
    ("error: invalid api key: credential=synthetic-secret", "NATIVE_AUTHENTICATION"),
    ("rate limit exceeded credential=synthetic-secret", "NATIVE_RATE_LIMIT"),
    ("insufficient_quota credential=synthetic-secret", "NATIVE_QUOTA"),
    ("connection refused C:/private/host", "NATIVE_TRANSPORT"),
    ("unrecognized credential=synthetic-secret", "NATIVE_EXIT"),
])
def test_recognized_native_causes_export_only_allowlisted_categories(text, category):
    _, private = _native_failure_fixture(exit_code=9, reason="PROCESS_EXIT")
    private["error"]["native_diagnostics"]["stderr"] = dict(availability="captured", text=text, truncated=False)
    public = review_transport.public_failure(json.dumps(private).encode())
    assert json.loads(public)["error"]["diagnostic"]["category"] == category
    assert b"synthetic-secret" not in public and b"C:/private" not in public


@pytest.mark.parametrize("exit_code", [True, False, "1", 1.5, []])
def test_native_failure_exit_rejects_forged_non_integer_values(exit_code):
    _, private = _native_failure_fixture(exit_code=exit_code)
    for reader in (review_transport.public_failure, review_transport.failure_document):
        with pytest.raises(review_transport.FailureDiagnosticError):
            reader(json.dumps(private).encode())


@pytest.mark.parametrize("selector", ['"native and (timeout or dirty)"', "'native and not timeout'", '"test_token or test_secret"'])
def test_public_failure_preserves_exact_safe_quoted_pytest_identity(selector):
    _, private = _native_failure_fixture()
    command = "python -m pytest tests/test_synthetic.py -k " + selector
    private["phase"] = "VERIFICATION"
    private["error"] = dict(type="RuntimeVerificationError", message="private synthetic-secret",
                            verification=[dict(command=command, exit_code=1, summary="private synthetic-secret")])
    public = review_transport.failure_document(review_transport.public_failure(json.dumps(private).encode()))
    assert public["error"]["verification"][0]["command"] == command
    context = review_transport.failure_context(public)
    assert context["diagnostic"]["category"] == "VERIFICATION_TEST_FAILURE"
    assert context["verification"][0]["observation_availability"] == "UNAVAILABLE"


@pytest.mark.parametrize("command", [
    'python -m pytest -k "safe; echo synthetic-secret"',
    'python -m pytest -k "$(echo synthetic-secret)"',
    'python -m pytest -k "`echo synthetic-secret`"',
    "python -m pytest -k 'safe' && echo synthetic-secret",
    'python -m pytest -k "safe', 'python -m pytest -k "safe"suffix',
    "python -m pytest --token synthetic-secret", "python -m pytest credential=synthetic-secret",
    "python -m pytest --access-token=synthetic-secret", "python -m pytest --basetemp=/home/private/host",
    "python -m pytest C:/private/synthetic-secret", 'python -c "print(synthetic-secret)"',
])
def test_failure_commands_reject_malicious_quoting_and_credentials(command):
    with pytest.raises(ValueError, match="unsafe public verification command"):
        review_transport._public_command(command)


@pytest.mark.parametrize("missing", ["detail", "fingerprint", "both", "conditions"])
def test_incomplete_historical_observation_never_manufactures_detail_or_fingerprints(missing):
    from aios_renew.verification_contract import verification_digest, validate_observation
    _, private, _ = _failure_transport_recovery_fixture(nodes=1, failures=1)
    ref = private["error"]["verification"][0]["verification"]
    report = next(item for item in ref["candidate"]["reports"] if item["outcome"] == "FAIL")
    if missing in {"detail", "both", "conditions"}:
        report.pop("detail")
    if missing in {"fingerprint", "both", "conditions"}:
        report.pop("fingerprint")
    if missing == "conditions":
        report.pop("profile"); report.pop("toolchain")
    ref["candidate_digest"] = verification_digest(ref["candidate"])
    original = json.dumps(private).encode()
    public = review_transport.failure_document(review_transport.public_failure(original))
    observation = public["error"]["verification"][0]["verification"]["candidate"]
    assert "fingerprint" not in observation["failed_reports"][0]
    assert observation["failed_reports"][0]["detail_status"] in {"UNAVAILABLE", "WITHHELD"}
    assert observation["complete"] is False
    with pytest.raises(ValueError):
        validate_observation(observation)
    assert json.dumps(private).encode() == original


def test_historical_versions_and_bounded_protected_incomplete_reference_remain_readable():
    from aios_renew.verification_contract import verification_digest
    _, private, raw = _failure_transport_recovery_fixture(nodes=1, failures=1)
    v1 = review_transport._project_failure(private, projection_format=review_transport.LEGACY_PUBLIC_FAILURE_FORMAT)
    assert review_transport.failure_document(json.dumps(v1).encode()) == v1
    assert operator_failure_matches(private, v1)
    legacy = dict(kind="FAILURE", run_id="RUN-358-001", error=dict(type="CodexExecutionError",
                  message="Protected Runtime diagnostics remain local.", exit_code=None))
    assert review_transport.failure_document(json.dumps(legacy).encode()) == legacy
    private["error"].pop("native_diagnostics"); private["error"].pop("executor_diagnostics")
    private["error"]["message"] = "Protected Runtime diagnostics remain local."
    ref = private["error"]["verification"][0]["verification"]
    for report in ref["candidate"]["reports"]:
        for key in ("captured_output", "detail", "fingerprint"):
            report.pop(key, None)
    ref["candidate_digest"] = verification_digest(ref["candidate"])
    original = json.dumps(private).encode()
    assert review_transport.failure_document(original) == private
    context = review_transport.failure_context(private)
    assert context["verification"][0]["complete"] is False
    assert context["verification"][0]["detail_status"] == "UNAVAILABLE"
    assert b"C:/private" not in json.dumps(context).encode()
    assert json.dumps(private).encode() == original and raw != original
    ref.pop("policy")
    with pytest.raises(ReviewTransportError):
        review_transport.failure_document(json.dumps(private).encode())


@pytest.mark.parametrize("defect", ["detail", "category", "capture", "exit", "digest"])
def test_typed_public_failure_rejects_tampering_and_secret_fields(defect):
    _, private = _native_failure_fixture()
    public = json.loads(review_transport.public_failure(json.dumps(private).encode()))
    diagnostic = public["error"]["diagnostic"]
    if defect == "detail": diagnostic["detail"] = "credential=synthetic-secret"
    elif defect == "category": diagnostic["category"] = "PASS"
    elif defect == "capture": diagnostic["capture"]["stdout"]["text"] = "synthetic-secret"
    elif defect == "exit": diagnostic["native_exit"]["value"] = 0
    else: public["public_projection"]["private_failure_digest"] = "0" * 64
    _failure_transport_recovery_reseal(public)
    if defect != "digest":
        with pytest.raises(ReviewTransportError):
            review_transport.failure_document(json.dumps(public).encode())
    assert not operator_failure_matches(private, public)


def test_dirty_failure_transports_artifacts_and_attention_without_candidate_or_uncommitted_bytes(tmp_path, monkeypatch):
    repo, remote = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    run, private = _native_failure_fixture(dirty=True)
    run.update(task=TASK, base_sha=head)
    private.update(task=TASK, base_sha=head, failed_head_sha=head)
    (repo / "subject.txt").write_text("uncommitted credential=synthetic-secret", encoding="utf-8")
    run_path, failure_path = tmp_path / "private/run.json", tmp_path / "private/failure.json"
    write_json(run_path, run)
    original = write_json(failure_path, private)
    attention_calls = []
    monkeypatch.setattr(review_transport, "publish_terminal_attention", lambda *a, **kw: attention_calls.append(kw))
    transport_failure(repo, run_id=run["run_id"], head_sha=head, run_path=run_path,
                      failure_path=failure_path, publish_candidate=False)
    refs = git(remote, "for-each-ref", "--format=%(refname)")
    assert "refs/heads/aios/failure/" not in refs
    artifact = git(remote, "rev-parse", "refs/heads/aios/failure-artifacts/" + run["run_id"])
    names = git(remote, "ls-tree", "-r", "--name-only", artifact).splitlines()
    assert set(names) == {".ai/transport/run.json", ".ai/transport/failure.json"}
    assert attention_calls[0]["artifact_sha"] == artifact
    assert failure_path.read_bytes() == original
    assert (repo / "subject.txt").read_text(encoding="utf-8") == "uncommitted credential=synthetic-secret"
    with pytest.raises(review_transport.FailureDiagnosticError, match="FAILURE_CANDIDATE_NOT_PUBLISHABLE"):
        transport_failure(repo, run_id=run["run_id"], head_sha=head, run_path=run_path, failure_path=failure_path)


def _failure_transport_recovery_reseal(public):
    """Exercise schema/binding gates independently of summary integrity."""
    from aios_renew.verification_contract import verification_digest
    public["public_projection"]["projection_digest"] = verification_digest(
        {key: value for key, value in public.items() if key != "public_projection"})


def test_failure_transport_recovery_large_private_public_identity_and_privacy():
    from aios_renew.unified_state import _matches_public_document
    from aios_renew.verification_contract import (
        attributed_task_passes, derive_minimum_verification, validate_observation, verification_digest,
    )
    _, private, raw = _failure_transport_recovery_fixture()
    assert 4491342 <= len(raw) <= review_transport.PUBLIC_FAILURE_MAX_BYTES
    public_bytes = review_transport.public_failure(raw)
    public = review_transport.failure_document(public_bytes)
    assert review_transport.public_failure(raw) == public_bytes
    assert review_transport.public_failure(public_bytes) == public_bytes
    assert len(public_bytes) < review_transport.PUBLIC_ARTIFACT_MAX_BYTES
    assert all(text not in public_bytes for text in (
        b"secret_fixture", b"C:/private", b"captured_output", b"traceback", b"raw_path"))
    assert _matches_public_document(private, public, "METADATA")
    assert json.dumps(private, sort_keys=True, separators=(",", ":")).encode() == raw
    for key in ("run_id", "task", "executor", "base_sha", "failed_head_sha", "candidate", "phase"):
        assert public[key] == private[key]
    assert public["public_projection"]["private_failure_digest"] == verification_digest(private)
    original = private["error"]["verification"][0]["verification"]
    reference = public["error"]["verification"][0]["verification"]
    assert reference["raw_digest"] == original["raw_digest"]
    assert reference["binding_digest"] == verification_digest(original["binding"])
    assert reference["private_reference_digest"] == verification_digest(original)
    for side in ("candidate", "base"):
        assert reference[side + "_digest"] == original[side + "_digest"]
        assert reference[side]["complete"] is False and reference[side]["unstable"] is True
        assert reference[side]["exit_code"] == 2
        for conditions in ("profile", "toolchain"):
            assert reference[side][conditions] == {"private_identity_digest": verification_digest(original[side][conditions])}
        with pytest.raises(ValueError):
            validate_observation(reference[side])
    observed = reference["candidate"]
    assert (observed["report_count"], observed["node_count"], observed["failure_count"]) == (24303, 8101, 84)
    expected = sorted((verification_digest(report["nodeid"]), report["phase"], report["fingerprint"])
                      for report in original["candidate"]["reports"] if report["outcome"] == "FAIL")
    assert [(report["nodeid_digest"], report["phase"], report["fingerprint"])
            for report in observed["failed_reports"]] == expected
    command = original["binding"]["command"]
    assert not attributed_task_passes(reference, subject_sha=private["failed_head_sha"], command=command, exit_code=2)
    plan = derive_minimum_verification((command,), base_sha=private["base_sha"],
        candidate_sha=private["failed_head_sha"], changed_files=("subject.txt",),
        modification_scope=("subject.txt",), operation="PRIMARY", bindings={command: original["binding"]},
        still_valid_evidence=(reference,), failed_observations=(observed,))
    assert plan.reused == () and plan.commands == (command,)


def test_failure_transport_recovery_remote_roundtrip(tmp_path):
    repo, remote = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    head = commit_candidate(repo, "synthetic failed candidate")
    run, private, raw = _failure_transport_recovery_fixture(base_sha=base, head_sha=head)
    files = tmp_path / "private"
    run_path, failure_path = files / "run.json", files / "failure.json"
    write_json(run_path, run)
    failure_path.write_bytes(raw)
    expected = review_transport.public_failure(raw)
    for _ in range(2):
        transport_failure(repo, run_id=run["run_id"], head_sha=head,
                          run_path=run_path, failure_path=failure_path)
    artifacts = git(remote, "rev-parse", f"refs/heads/aios/failure-artifacts/{run['run_id']}")
    assert review_transport._read_local_blob(remote, artifacts, ".ai/transport/failure.json") == expected
    recovery = resolve_remote_repair_recovery(repo, failed_run_id=run["run_id"])
    assert len(recovery.failures) == 1 and recovery.failures[0].failure == expected
    lifecycle = resolve_remote_task_lifecycle(repo, task_id="TASK-058", task_revision=2)
    assert len(lifecycle.terminals) == 1 and lifecycle.terminals[0].terminal == expected
    assert review_transport.prove_remote_failed_candidate(
        repo, failed_run_id=run["run_id"], failed_head_sha=head)[0] == base
    assert failure_path.read_bytes() == raw and json.loads(raw) == private


def test_failure_transport_recovery_remote_failure_budget_and_historical_gate(tmp_path, monkeypatch):
    repo, _ = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    run, _, raw = _failure_transport_recovery_fixture(base_sha=base, head_sha=base, nodes=1, failures=1,
                                                     changed_files=())
    public = review_transport.public_failure(raw)
    # Valid JSON whitespace exercises the actual remote blob gate above the old
    # 1 MiB cap, independently of the compact writer's privacy reduction.
    public += b" " * (4491342 - len(public))
    directory = repo / ".ai" / "transport"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "run.json").write_bytes(review_transport.public_run(json.dumps(run).encode()))
    (directory / "failure.json").write_bytes(public)
    artifacts = commit_fixture_state(repo, paths=(".ai/transport/run.json", ".ai/transport/failure.json"),
        message="synthetic bounded public FAILURE", user_name="Review Transport Test",
        user_email="transport@example.invalid")
    git(repo, "push", "origin", f"{base}:refs/heads/aios/failure/{run['run_id']}",
        f"{artifacts}:refs/heads/aios/failure-artifacts/{run['run_id']}")
    assert review_transport.prove_remote_failed_candidate(repo,
        failed_run_id=run["run_id"], failed_head_sha=base)[0] == base
    assert resolve_remote_repair_recovery(repo, failed_run_id=run["run_id"]).failures[0].failure == public
    assert resolve_remote_task_lifecycle(repo, task_id="TASK-058", task_revision=2).terminals[0].terminal == public
    monkeypatch.setattr(review_transport, "PUBLIC_FAILURE_MAX_BYTES", len(public) - 1)
    for reader in (
        lambda: review_transport.prove_remote_failed_candidate(repo, failed_run_id=run["run_id"], failed_head_sha=base),
        lambda: resolve_remote_repair_recovery(repo, failed_run_id=run["run_id"]),
        lambda: resolve_remote_task_lifecycle(repo, task_id="TASK-058", task_revision=2),
    ):
        with pytest.raises(ReviewTransportError, match="byte bound"):
            reader()


@pytest.mark.parametrize("defect", [
    "origin", "digest", "subject", "run", "count", "node_count", "exit", "raw", "capture", "conditions", "failed_identity",
])
def test_failure_transport_recovery_rejects_forged_public_projection(defect):
    from aios_renew.unified_state import _matches_public_document
    _, private, raw = _failure_transport_recovery_fixture(nodes=1, failures=1)
    public = json.loads(review_transport.public_failure(raw))
    record = public["error"]["verification"][0]["verification"]
    if defect == "origin": public["public_projection"]["private_failure_digest"] = "0" * 64
    elif defect == "digest": record["candidate_digest"] = "0" * 64
    elif defect == "subject": record["binding"]["subject_sha"] = "0" * 40
    elif defect == "run": record["run_id"] = "RUN-058-999"
    elif defect == "count": record["candidate"]["report_count"] += 1
    elif defect == "node_count": record["candidate"]["node_count"] = 0
    elif defect == "exit": record["candidate"]["exit_code"] = 0
    elif defect == "raw": record["raw_path"] = "C:/private/secret_fixture.raw"
    elif defect == "capture": record["candidate"]["captured_output"] = "secret_fixture"
    elif defect == "conditions": record["candidate"]["profile"]["private_identity_digest"] = "0" * 64
    else: record["candidate"]["failed_reports"][0]["nodeid_digest"] = "0" * 64
    _failure_transport_recovery_reseal(public)
    if defect in {"origin", "failed_identity"}:
        # An authenticated remote digest is not independently raw proof. Exact
        # local-origin reconciliation catches syntactically valid false claims.
        assert not _matches_public_document(private, public, "METADATA")
    else:
        with pytest.raises((ReviewTransportError, ValueError)):
            review_transport.failure_document(json.dumps(public).encode())
    assert not operator_failure_matches(private, public)


@pytest.mark.parametrize("defect", ["partial_binding", "private_digest", "wrong_subject", "complete", "report_conditions",
                                     "duplicate_phase", "unsafe_command"])
def test_failure_transport_recovery_private_projection_fails_closed(defect):
    from aios_renew.verification_contract import verification_digest
    _, private, _ = _failure_transport_recovery_fixture(nodes=1, failures=1)
    outcome = private["error"]["verification"][0]
    ref = outcome["verification"]
    if defect == "partial_binding": private.pop("task")
    elif defect == "private_digest": ref["candidate_digest"] = "0" * 64
    elif defect == "wrong_subject": ref["binding"]["subject_sha"] = "0" * 40
    elif defect == "complete": ref["candidate"]["complete"] = True
    elif defect == "report_conditions":
        ref["candidate"]["reports"][1]["profile"] = dict(ref["candidate"]["profile"], workers=4)
    elif defect == "duplicate_phase": ref["candidate"]["reports"].append(ref["candidate"]["reports"][0])
    else:
        command = "python -m pytest credential=secret_fixture"
        outcome["command"] = ref["binding"]["command"] = command
        ref["base"]["command"] = ref["candidate"]["command"] = command
        ref["base_digest"] = verification_digest(ref["base"])
        ref["binding"]["failure_set_digest"] = ref["base_digest"]
    if defect != "private_digest":
        ref["candidate_digest"] = verification_digest(ref["candidate"])
    with pytest.raises(ReviewTransportError):
        review_transport.public_failure(json.dumps(private).encode())


def operator_failure_matches(private, public):
    from aios_renew.operator import _matches_failure_projection
    return _matches_failure_projection(private, public)


@pytest.mark.parametrize("reader", [review_transport.failure_document,
    lambda data: review_transport._json_mapping(data, "FAILURE"),
    lambda data: review_transport._performance_json_mapping(data, "FAILURE"), review_transport.public_failure])
@pytest.mark.parametrize("content", [b'{"kind":"FAILURE","kind":"FAILURE"}',
    b'{"kind":"FAILURE","value":NaN}', b'{"kind":"FAILURE","value":1e999}', b'\xff', b'[]',
    b'{"kind":"FAILURE","nested":' + b'[' * 65 + b'0' + b']' * 65 + b'}'])
def test_failure_transport_recovery_shared_json_depth_gates(reader, content):
    with pytest.raises(ReviewTransportError):
        reader(content)


def test_failure_transport_recovery_limits_are_failure_specific(monkeypatch):
    _, _, raw = _failure_transport_recovery_fixture(nodes=1, failures=1)
    padded = raw + b" " * (review_transport.PUBLIC_ARTIFACT_MAX_BYTES + 1)
    assert review_transport.public_failure(padded)
    assert review_transport.failure_document(padded)
    for project in (review_transport.public_run, review_transport.public_result, review_transport.public_metadata):
        with pytest.raises(ReviewTransportError, match="byte bound"):
            project(padded)
    monkeypatch.setattr(review_transport, "PUBLIC_FAILURE_MAX_BYTES", len(raw))
    for project in (review_transport.public_failure, review_transport.failure_document):
        with pytest.raises(ReviewTransportError, match="byte bound"):
            project(raw + b" ")
    with pytest.raises(ReviewTransportError, match="byte bound"):
        review_transport._public_bytes(dict(note="x" * review_transport.PUBLIC_ARTIFACT_MAX_BYTES))


def test_failure_transport_recovery_small_legacy_interoperability():
    legacy = dict(kind="FAILURE", run_id="RUN-058-001", task=TASK, executor="codex",
        base_sha="a" * 40, failed_head_sha="b" * 40,
        candidate=dict(repairable=True, changed_files=["subject.txt"]),
        error=dict(type="OperatorError", message="private secret_fixture",
                   native_diagnostics=dict(stdout="private capture secret_fixture")))
    content = json.dumps(legacy).encode()
    public = review_transport.public_failure(content)
    assert public == review_transport.public_metadata(content)
    assert b"secret_fixture" not in public
    assert "public_projection" not in review_transport.failure_document(public)
    evidence = dict(evidence_id="E1", run_id="RUN-058-001", subject_sha="b" * 40, type="VERIFICATION",
        source=dict(command="git diff --check"), result=dict(exit_code=0, summary="private secret_fixture"),
        raw=dict(path="C:/private/runtime/proof.raw"))
    package = dict(result=dict(head_sha="b" * 40, claims=[], changed_files=[], unresolved=[]), evidence=[evidence])
    projected = json.loads(review_transport.public_result(json.dumps(package).encode()))
    assert projected["result"] == package["result"]
    assert projected["evidence"][0]["raw"] == {"path": "protected-runtime-raw/E1"}
    assert projected["evidence"][0]["result"] == {"exit_code": 0, "summary": "Runtime verification exit code 0"}
    legacy["error"]["evidence"] = [evidence]
    private_bytes = json.dumps(legacy).encode()
    public_bytes = review_transport.public_failure(private_bytes)
    assert public_bytes == review_transport.public_metadata(private_bytes)
    assert operator_failure_matches(legacy, review_transport.failure_document(public_bytes))


@pytest.mark.parametrize("placement", ["root", "error", "outcome", "command", "candidate_path"])
def test_failure_transport_recovery_legacy_writer_rejects_private_contamination(placement):
    failure = dict(kind="FAILURE", run_id="RUN-058-001", task=TASK, executor="codex",
        base_sha="a" * 40, failed_head_sha="b" * 40,
        candidate=dict(repairable=True, changed_files=["subject.txt"]),
        error=dict(type="OperatorError", message="private diagnostics", verification=[
            dict(command="git diff --check", exit_code=1, summary="private diagnostics")]))
    if placement == "root": failure["captured_output"] = "secret_fixture"
    elif placement == "error": failure["error"]["captured_output"] = "secret_fixture"
    elif placement == "outcome": failure["error"]["verification"][0]["stdout"] = "secret_fixture"
    elif placement == "command": failure["error"]["verification"][0]["command"] = "python -m pytest token=secret_fixture"
    else: failure["candidate"]["changed_files"] = ["C:/private/secret_fixture"]
    with pytest.raises(ReviewTransportError):
        review_transport.public_failure(json.dumps(failure).encode())
    if placement in {"root", "error", "outcome"}:
        failure["error"]["message"] = "Protected Runtime diagnostics remain local."
        with pytest.raises(ReviewTransportError, match="private captured output"):
            review_transport.failure_document(json.dumps(failure).encode())


def test_failure_transport_recovery_inherited_public_diagnostics_are_rejected():
    from aios_renew.unified_state import _validated_private_public_view

    _, private, raw = _failure_transport_recovery_fixture(nodes=1, failures=1)
    # The historical outer redaction preserved V2 raw paths and phase detail.
    # Model that inherited representation using synthetic proof only.
    historical_public = _validated_private_public_view(private, "METADATA")
    assert historical_public["error"]["message"] == "Protected Runtime diagnostics remain local."
    historical_bytes = json.dumps(historical_public).encode()
    assert b"C:/private/runtime/" in historical_bytes and b"secret_fixture" in historical_bytes
    for reader in (review_transport.failure_document, review_transport.public_failure,
                   lambda content: review_transport._json_mapping(content, "FAILURE"),
                   lambda content: review_transport._performance_json_mapping(content, "FAILURE")):
        with pytest.raises(ReviewTransportError):
            reader(historical_bytes)
    # Exact private originals stay readable. The lawful prospective projection
    # binds the same non-PASS facts without publishing or recreating raw proof.
    assert review_transport.failure_document(raw) == private
    public_bytes = review_transport.public_failure(raw)
    assert all(text not in public_bytes for text in (
        b"C:/private/", b"secret_fixture", b"captured_output", b"detail", b"raw_path"))
    public = review_transport.failure_document(public_bytes)
    original = private["error"]["verification"][0]["verification"]
    projected = public["error"]["verification"][0]["verification"]
    assert projected["raw_digest"] == original["raw_digest"]
    assert projected["candidate_digest"] == original["candidate_digest"]
    assert projected["candidate"]["exit_code"] == 2
    assert projected["candidate"]["complete"] is False and projected["candidate"]["unstable"] is True
    assert operator_failure_matches(private, public)
    assert json.dumps(private, sort_keys=True, separators=(",", ":")).encode() == raw


@pytest.mark.parametrize("placement", ["raw_path", "base_raw_path", "candidate_path",
    "evidence_path", "evidence_summary", "captured_output"])
def test_failure_transport_recovery_public_legacy_contamination_fails_closed(monkeypatch, placement):
    private = dict(kind="FAILURE", run_id="RUN-058-001", task=TASK, executor="codex",
        base_sha="a" * 40, failed_head_sha="b" * 40,
        candidate=dict(repairable=True, changed_files=["subject.txt"]),
        error=dict(type="OperatorError", message="synthetic private diagnostics", evidence=[
            dict(evidence_id="E1", run_id="RUN-058-001", subject_sha="b" * 40, type="VERIFICATION",
                 source=dict(command="git diff --check"), result=dict(exit_code=1, summary="synthetic capture"),
                 raw=dict(path="C:/private/synthetic.raw"))]))
    lawful = review_transport.public_failure(json.dumps(private).encode())
    assert review_transport.public_failure(lawful) == lawful
    public = json.loads(lawful)
    if placement in {"raw_path", "base_raw_path"}:
        public["error"][placement] = "C:/private/synthetic.raw"
    elif placement == "candidate_path":
        public["candidate"]["changed_files"] = ["C:/private/synthetic.py"]
    elif placement == "evidence_path":
        public["error"]["evidence"][0]["raw"]["path"] = "C:/private/synthetic.raw"
    elif placement == "evidence_summary":
        public["error"]["evidence"][0]["result"]["summary"] = "synthetic captured output secret_fixture"
    else:
        public["error"]["captured_output"] = "synthetic capture secret_fixture"
    contaminated = json.dumps(public).encode()
    for reader in (review_transport.failure_document, review_transport.public_failure,
                   lambda content: review_transport._json_mapping(content, "FAILURE"),
                   lambda content: review_transport._performance_json_mapping(content, "FAILURE")):
        with pytest.raises(ReviewTransportError):
            reader(contaminated)

    def remote_blob(_repo, *args, **_kwargs):
        if args[0] == "show":
            assert args[1].endswith(":.ai/transport/failure.json")
            return 0, contaminated.decode(), ""
        assert args[0] == "fetch"
        return 0, "", ""

    monkeypatch.setattr(review_transport, "_git_cmd", remote_blob)
    for reader in (
        lambda: review_transport._read_remote_blob(Path("synthetic"), "origin", "a" * 40, ".ai/transport/failure.json"),
        lambda: review_transport._read_lifecycle_blob(Path("synthetic"), "origin", "a" * 40, ".ai/transport/failure.json"),
        lambda: review_transport._read_local_blob(Path("synthetic"), "a" * 40, ".ai/transport/failure.json"),
    ):
        with pytest.raises(ReviewTransportError):
            reader()


def test_public_transport_redacts_nested_private_records_and_preserves_bindings() -> None:
    reference = {"policy": "minimum-sufficient-v2", "evidence_id": "E1",
                 "raw_path": "immutable-reference", "raw_digest": "d" * 64}
    evidence = dict(evidence_id="E1", run_id="RUN-339-001", subject_sha="a" * 40,
        type="VERIFICATION", source=dict(command="git diff --check"),
        result=dict(exit_code=0, summary="private diagnostic credential=fixture-secret"),
        raw=dict(path="C:/private/runtime/raw.log"), verification=reference)
    run = dict(run_id="RUN-339-001", task=dict(id="TASK-339", revision=1),
        workspace="C:/private/runtime", executor="codex", base_sha="b" * 40,
        head_sha=None, status="ACTIVE")
    failure = dict(kind="FAILURE", run_id=run["run_id"], failed_head_sha="a" * 40,
        error=dict(type="RuntimeVerificationError", message="private message fixture-secret",
            native_diagnostics=dict(stderr="credential=fixture-secret"),
            executor_diagnostics=dict(stdout="credential=fixture-secret"),
            verification=[dict(command="git diff --check", exit_code=0,
                summary="private outcome fixture-secret", verification=reference)], evidence=[evidence]))
    correction = dict(run=run, failure=failure, failed_run_id=run["run_id"],
        failed_head_sha="a" * 40, root_base_sha="b" * 40, task=run["task"], repair={})
    raw = json.dumps(correction).encode()
    public = review_transport.public_metadata(raw)
    data = json.loads(public)
    assert b"fixture-secret" not in public and b"C:/private" not in public
    assert data["run"]["workspace"] == "protected-runtime-workspace"
    error = data["failure"]["error"]
    assert error["message"] == "Protected Runtime diagnostics remain local."
    assert "native_diagnostics" not in error and "executor_diagnostics" not in error
    assert error["verification"][0]["verification"] == reference
    assert error["evidence"][0]["verification"] == reference
    assert error["evidence"][0]["raw"] == {"path": "protected-runtime-raw/E1"}
    assert error["evidence"][0]["subject_sha"] == evidence["subject_sha"]
    assert json.loads(raw) == correction
    assert review_transport.public_metadata(public) == public
    assert json.loads(review_transport.public_result(json.dumps(dict(evidence=[evidence])).encode()))["evidence"] == error["evidence"]
    for document in (run, dict(kind="REMEDIATION", execution=dict(run=run))):
        projected = json.loads(review_transport.public_run(json.dumps(document).encode()))
        public_run = projected["execution"]["run"] if "execution" in projected else projected
        assert public_run == dict(run, workspace="protected-runtime-workspace")


@pytest.mark.parametrize("project", [review_transport.public_run,
    review_transport.public_result, review_transport.public_metadata])
@pytest.mark.parametrize("content", [b'{"run_id":"one","run_id":"two"}',
    b"[]", b'{"value":NaN}', b'"unterminated', b'\xff'])
def test_public_transport_rejects_malformed_json(project, content) -> None:
    with pytest.raises(ReviewTransportError):
        project(content)


@pytest.mark.parametrize("project", [review_transport.public_run,
    review_transport.public_result, review_transport.public_metadata])
def test_public_transport_writer_bounds_do_not_change_historical_readers(project) -> None:
    large = json.dumps(dict(note="x" * review_transport.PUBLIC_ARTIFACT_MAX_BYTES)).encode()
    with pytest.raises(ReviewTransportError, match="byte bound"):
        project(large)
    assert review_transport._performance_json_mapping(large, "historical")["note"]
    nested = b'{"nested":' * 65 + b"0" + b"}" * 65
    with pytest.raises(ReviewTransportError, match="nesting bound"):
        project(nested)


@pytest.mark.parametrize("identity", ["../private", "contains space", "", None])
def test_public_transport_rejects_unsafe_raw_placeholder_identity(identity) -> None:
    content = json.dumps(dict(evidence=[dict(evidence_id=identity, raw=dict(path="private"),
        result=dict(exit_code=0, summary="private"))])).encode()
    for project in (review_transport.public_result, review_transport.public_metadata):
        with pytest.raises(ReviewTransportError, match="unsafe public evidence identity"):
            project(content)


TASK = {"id": "TASK-058", "revision": 2}
PINNED_AIOS_RENEW_SHA = "26097405343150dc1b55015b94720528afad50ed"
PINNED_PROVENANCE_PATH = (
    ".agents/skills/aios-worker/requirements-aios-renew.txt"
)


def test_runtime_failure_binding_rejects_candidate_facts_that_cannot_be_repairable(
) -> None:
    failure = {
        "kind": "FAILURE",
        "run_id": "RUN-058-004",
        "task": TASK,
        "executor": "codex",
        "base_sha": "a" * 40,
        "failed_head_sha": "b" * 40,
        "candidate": {
            "transportable": True,
            "repairable": True,
            "dirty": True,
            "descends_from_base": True,
            "changed_files": ["subject.txt"],
            "outside_task_scope": [],
        },
    }

    with pytest.raises(ValueError, match="repair binding"):
        validate_runtime_failure_binding(
            failure,
            run_id="RUN-058-004",
            task_id="TASK-058",
            task_revision=2,
            executor="codex",
            base_sha="a" * 40,
            candidate_sha="b" * 40,
            modification_scope=("subject.txt",),
            actual_descends_from_base=True,
            actual_changed_files={"subject.txt"},
        )


def git(repo: Path, *args: str) -> str:
    if args == ("rev-parse", "HEAD"):
        return read_git_ref(repo)
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def make_repo(
    root: Path, *, downstream_attention: bool = False
) -> tuple[Path, Path]:
    files = {
        ".ai/tasks/TASK-058.yaml": (
            "task_id: TASK-058\nrevision: 2\ngoal: historical contract\n"
        ),
        "subject.txt": "root\n",
    }
    if downstream_attention:
        files[".github/workflows/aios-terminal-attention.yml"] = (
            "run: |\n  python -m pip install --no-input "
            f"-r {PINNED_PROVENANCE_PATH}\n"
        )
        files[".ai/brain-terminal-attention-carriers.yaml"] = (
            "reviewed: downstream policy\n"
        )
        files[PINNED_PROVENANCE_PATH] = (
            "aios-renew @ git+https://github.com/trung-via/AIOS-renew.git@"
            f"{PINNED_AIOS_RENEW_SHA}\n"
        )
    repo, remote, _ = materialize_git_baseline(
        root,
        files=files,
        user_name="Review Transport Test",
        user_email="transport@example.invalid",
        commit_message="root",
    )
    return repo, remote


def test_detached_observation_remote_does_not_widen_transport_resolution(
    tmp_path: Path,
) -> None:
    repo, remote = make_repo(tmp_path)
    assert resolve_transport_remote(repo) == "origin"
    git(repo, "checkout", "--detach")
    assert resolve_detached_observation_remote(repo) == "origin"
    with pytest.raises(ReviewTransportError, match="current branch"):
        resolve_transport_remote(repo)

    git(repo, "remote", "add", "other", str(remote))
    git(repo, "config", "branch.other.remote", "other")
    with pytest.raises(ReviewTransportError, match="ambiguous"):
        resolve_detached_observation_remote(repo)
    with pytest.raises(ReviewTransportError, match="current branch"):
        resolve_transport_remote(repo)

    git(repo, "config", "--unset", "branch.other.remote")
    git(repo, "config", "--unset", "branch.main.remote")
    with pytest.raises(ReviewTransportError, match="missing or ambiguous"):
        resolve_detached_observation_remote(repo)


def test_performance_snapshot_rejects_competing_terminal_refs_before_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = make_repo(tmp_path)
    refs = {
        "refs/heads/aios/artifacts/RUN-058-001": "a" * 40,
        "refs/heads/aios/failure-artifacts/RUN-058-001": "b" * 40,
    }
    monkeypatch.setattr(review_transport, "_exact_remote_refs", lambda *args: refs)
    reads: list[str] = []
    monkeypatch.setattr(
        review_transport,
        "_read_lifecycle_blob",
        lambda *args: reads.append(str(args[-1])),
    )
    with pytest.raises(ReviewTransportError, match="competing RESULT/FAILURE"):
        review_transport.resolve_remote_performance_snapshot(
            repo, task_ids=["TASK-058"]
        )
    assert reads == []


def test_performance_snapshot_binds_terminal_before_observation_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = make_repo(tmp_path)
    ref = "refs/heads/aios/failure-artifacts/RUN-058-001"
    monkeypatch.setattr(
        review_transport, "_exact_remote_refs", lambda *args: {ref: "a" * 40}
    )
    run = json.dumps(
        {
            "run_id": "RUN-058-001",
            "task": {"id": "TASK-058", "revision": 9},
            "executor": "codex",
            "base_sha": "b" * 40,
            "workspace": "bounded",
            "head_sha": None,
            "status": "ACTIVE",
        }
    ).encode()
    malformed_failure = json.dumps(
        {
            "kind": "FAILURE",
            "run_id": "RUN-058-OTHER",
            "task": {"id": "TASK-058", "revision": 9},
            "executor": "codex",
            "base_sha": "b" * 40,
            "failed_head_sha": "c" * 40,
            "candidate": {},
        }
    ).encode()
    requested: list[str] = []

    def read_blob(*args):
        path = str(args[-1])
        requested.append(path)
        if path.endswith("run.json"):
            return run
        if path.endswith("failure.json"):
            return malformed_failure
        raise AssertionError("observation must not be classified before terminal binding")

    monkeypatch.setattr(review_transport, "_read_lifecycle_blob", read_blob)
    with pytest.raises(ReviewTransportError, match="FAILURE identity"):
        review_transport.resolve_remote_performance_snapshot(
            repo, task_ids=["TASK-058"]
        )
    assert requested == [
        ".ai/transport/run.json",
        ".ai/transport/failure.json",
    ]


def test_performance_snapshot_rejects_overflow_without_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = make_repo(tmp_path)
    refs = {
        f"refs/heads/aios/artifacts/RUN-058-{index:03d}": "a" * 40
        for index in range(257)
    }
    monkeypatch.setattr(review_transport, "_exact_remote_refs", lambda *args: refs)
    with pytest.raises(ReviewTransportError, match="maximum bound of 256"):
        review_transport.resolve_remote_performance_snapshot(
            repo, task_ids=["TASK-058"]
        )


def test_unified_lifecycle_snapshot_is_bounded_and_read_only(tmp_path: Path) -> None:
    repo, remote = make_repo(tmp_path)
    before_head = git(repo, "rev-parse", "HEAD")
    before_refs = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")

    snapshot = resolve_remote_task_lifecycle(
        repo, task_id="TASK-058", task_revision=2
    )

    assert snapshot.main_sha == before_head
    assert snapshot.terminals == ()
    assert snapshot.reviews == ()
    assert snapshot.remediation_selectors == ()
    assert snapshot.repair_selectors == ()
    assert git(repo, "rev-parse", "HEAD") == before_head
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs


def commit_candidate(repo: Path, label: str) -> str:
    (repo / "subject.txt").write_text(f"{label}\n", encoding="utf-8")
    return commit_fixture_state(
        repo,
        paths=("subject.txt",),
        message=label,
        user_name="Review Transport Test",
        user_email="transport@example.invalid",
    )


def write_json(path: Path, value: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(content)
    return content


def publish_failure(
    repo: Path,
    files: Path,
    *,
    run_id: str,
    candidate_sha: str,
    root_base_sha: str,
    continuation_of: str | None = None,
    failure_run_id: str | None = None,
    preverification: bytes | None = None,
) -> tuple[bytes, bytes, bytes | None, bytes | None]:
    run = {
        "run_id": run_id,
        "task": TASK,
        "executor": "codex",
        "base_sha": root_base_sha,
        "workspace": "discarded-historical-workspace",
        "head_sha": None,
        "status": "ACTIVE",
    }
    failure = {
        "kind": "FAILURE",
        "run_id": failure_run_id or run_id,
        "task": TASK,
        "executor": "codex",
        "base_sha": root_base_sha,
        "failed_head_sha": candidate_sha,
        "candidate": {"repairable": True, "changed_files": ["subject.txt"]},
    }
    lineage = None
    if continuation_of is not None:
        failure["continuation_of"] = continuation_of
        lineage = {
            "failed_run_id": continuation_of,
            "root_base_sha": root_base_sha,
            "failed_head_sha": candidate_sha,
            "failure": {"run_id": continuation_of, "task": TASK},
            "task": {"task_id": "TASK-058", "revision": 2},
            "repair": {
                "repair_id": f"REPAIR-{run_id}",
                "failed_run_id": continuation_of,
                "task": TASK,
            },
            "run": run,
        }

    directory = files / run_id
    run_path = directory / "run.json"
    failure_path = directory / "failure.json"
    lineage_path = directory / "repair.json"
    preverification_path = directory / "pre-verification.json"
    run_bytes = write_json(run_path, run)
    failure_bytes = write_json(failure_path, failure)
    lineage_bytes = write_json(lineage_path, lineage) if lineage is not None else None
    if preverification is not None:
        preverification_path.parent.mkdir(parents=True, exist_ok=True)
        preverification_path.write_bytes(preverification)
    transport_failure(
        repo,
        run_id=run_id,
        head_sha=candidate_sha,
        run_path=run_path,
        failure_path=failure_path,
        lineage_path=lineage_path if lineage is not None else None,
        preverification_path=(
            preverification_path if preverification is not None else None
        ),
    )
    return run_bytes, failure_bytes, lineage_bytes, preverification


def publish_success(
    repo: Path,
    files: Path,
    *,
    run_id: str,
    head_sha: str,
    root_base_sha: str,
    failed_run_id: str | None = None,
) -> None:
    run = {
        "run_id": run_id,
        "task": TASK,
        "executor": "codex",
        "base_sha": head_sha,
        "workspace": "discarded-workspace",
        "head_sha": None,
        "status": "ACTIVE",
    }
    result = {
        "result": {
            "head_sha": head_sha,
            "claims": [],
            "changed_files": [],
            "unresolved": [],
        },
        "evidence": [],
    }
    directory = files / run_id
    run_path = directory / "run.json"
    result_path = directory / "result.json"
    lineage_path = directory / "repair.json"
    write_json(run_path, run)
    write_json(result_path, result)
    if failed_run_id is not None:
        write_json(
            lineage_path,
            {
                "failed_run_id": failed_run_id,
                "root_base_sha": root_base_sha,
                "failed_head_sha": head_sha,
                "failure": {"run_id": failed_run_id, "task": TASK},
                "task": {"task_id": "TASK-058", "revision": 2},
                "repair": {
                    "repair_id": f"REPAIR-{run_id}",
                    "failed_run_id": failed_run_id,
                    "task": TASK,
                },
                "run": run,
            },
        )
    transport_post_pass(
        repo,
        run_id=run_id,
        head_sha=head_sha,
        run_path=run_path,
        result_path=result_path,
        lineage_path=lineage_path if failed_run_id is not None else None,
    )


@pytest.mark.parametrize("terminal_kind", ["RESULT", "FAILURE"])
def test_terminal_transport_notifies_only_after_canonical_artifact_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_kind: str,
) -> None:
    repo, _ = make_repo(tmp_path)
    files = tmp_path / "attention-files"
    root = git(repo, "rev-parse", "HEAD")
    candidate = commit_candidate(repo, f"{terminal_kind.lower()} candidate")
    calls: list[tuple[str, str, str]] = []

    def observe_attention(
        observed_repo: Path,
        *,
        remote: str,
        run_id: str,
        terminal_kind: str,
        artifact_sha: str,
    ) -> str:
        namespace = "artifacts" if terminal_kind == "RESULT" else "failure-artifacts"
        terminal_ref = f"refs/heads/aios/{namespace}/{run_id}"
        assert git(observed_repo, "ls-remote", "--refs", remote, terminal_ref) == (
            f"{artifact_sha}\t{terminal_ref}"
        )
        calls.append((run_id, terminal_kind, artifact_sha))
        return "PUBLISHED"

    monkeypatch.setattr(review_transport, "publish_terminal_attention", observe_attention)
    if terminal_kind == "RESULT":
        publish_success(
            repo,
            files,
            run_id="RUN-058-090",
            head_sha=candidate,
            root_base_sha=root,
        )
    else:
        publish_failure(
            repo,
            files,
            run_id="RUN-058-090",
            candidate_sha=candidate,
            root_base_sha=root,
        )

    assert len(calls) == 1
    assert calls[0][:2] == ("RUN-058-090", terminal_kind)


@pytest.mark.parametrize("terminal_kind", ["RESULT", "FAILURE"])
def test_terminal_transport_emits_attention_for_exact_pin_downstream_binding(
    tmp_path: Path, terminal_kind: str
) -> None:
    repo, _ = make_repo(tmp_path, downstream_attention=True)
    assert not (repo / "src/aios_renew/terminal_attention.py").exists()
    files = tmp_path / "downstream-attention-files"
    main_sha = git(repo, "rev-parse", "HEAD")
    candidate = commit_candidate(repo, f"downstream {terminal_kind.lower()}")
    run_id = "RUN-058-091"

    if terminal_kind == "RESULT":
        publish_success(
            repo,
            files,
            run_id=run_id,
            head_sha=candidate,
            root_base_sha=main_sha,
        )
        namespace = "artifacts"
    else:
        publish_failure(
            repo,
            files,
            run_id=run_id,
            candidate_sha=candidate,
            root_base_sha=main_sha,
        )
        namespace = "failure-artifacts"

    terminal_ref = f"refs/heads/aios/{namespace}/{run_id}"
    terminal_sha = git(repo, "ls-remote", "--refs", "origin", terminal_ref).split()[0]
    signal_ref = (
        f"refs/heads/aios/terminal-attention/{terminal_kind}/{run_id}/{terminal_sha}"
    )
    assert git(repo, "ls-remote", "--refs", "origin", signal_ref) == (
        f"{main_sha}\t{signal_ref}"
    )


def test_resolves_canonical_failed_correction_chain_with_exact_transported_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, remote = make_repo(tmp_path)
    files = tmp_path / "facts"
    root = git(repo, "rev-parse", "HEAD")
    first_head = commit_candidate(repo, "failed primary")
    first = publish_failure(
        repo,
        files,
        run_id="RUN-058-001",
        candidate_sha=first_head,
        root_base_sha=root,
    )
    second_head = commit_candidate(repo, "failed repair one")
    second = publish_failure(
        repo,
        files,
        run_id="RUN-058-002",
        candidate_sha=second_head,
        root_base_sha=root,
        continuation_of="RUN-058-001",
    )
    third_head = commit_candidate(repo, "failed repair two")
    third = publish_failure(
        repo,
        files,
        run_id="RUN-058-003",
        candidate_sha=third_head,
        root_base_sha=root,
        continuation_of="RUN-058-002",
    )

    original_git_cmd = review_transport._git_cmd
    snapshot_calls: list[tuple[str, ...]] = []

    def track_git_cmd(repo: Path, *args: str, **kwargs: object):
        result = original_git_cmd(repo, *args, **kwargs)
        if args[:2] == ("ls-remote", "--refs"):
            snapshot_calls.append(args)
            code, output, stderr = result
            return code, "\n".join(reversed(output.splitlines())), stderr
        return result

    monkeypatch.setattr(review_transport, "_git_cmd", track_git_cmd)

    recovery = resolve_remote_repair_recovery(
        repo, failed_run_id="RUN-058-003"
    )

    assert tuple(item.run_id for item in recovery.failures) == (
        "RUN-058-003",
        "RUN-058-002",
        "RUN-058-001",
    )
    assert tuple(item.candidate_sha for item in recovery.failures) == (
        third_head,
        second_head,
        first_head,
    )
    assert recovery.remote_run_ids == (
        "RUN-058-001",
        "RUN-058-002",
        "RUN-058-003",
    )
    assert snapshot_calls == [
        (
            "ls-remote",
            "--refs",
            "origin",
            "refs/heads/aios/failure-artifacts/RUN-058-*",
            "refs/heads/aios/artifacts/RUN-058-*",
            "refs/heads/aios/failure/RUN-058-*",
        )
    ]
    by_run = {item.run_id: item for item in recovery.failures}
    for run_id, private in zip(recovery.remote_run_ids, (first, second, third)):
        private_run, private_failure, private_repair, _ = private
        public_run = dict(json.loads(private_run), workspace="protected-runtime-workspace")
        expected_run = json.dumps(public_run, sort_keys=True, separators=(",", ":")).encode()
        recovered = by_run[run_id]
        assert (recovered.run, recovered.failure) == (expected_run, private_failure)
        assert (files / run_id / "run.json").read_bytes() == private_run
        assert json.loads(private_run)["workspace"] == "discarded-historical-workspace"
        if private_repair is None:
            assert recovered.repair is None
        else:
            public_repair = dict(json.loads(private_repair), run=public_run)
            expected_repair = json.dumps(public_repair, sort_keys=True, separators=(",", ":")).encode()
            assert recovered.repair == expected_repair
            assert (files / run_id / "repair.json").read_bytes() == private_repair
    assert git(
        remote,
        "show",
        "refs/heads/aios/failure-artifacts/RUN-058-003:.ai/transport/repair.json",
    ).encode() == by_run["RUN-058-003"].repair


def test_historical_repair_snapshot_failure_has_bounded_operational_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = make_repo(tmp_path)
    snapshot_calls = 0
    original_git_cmd = review_transport._git_cmd

    def fail_snapshot(repo: Path, *args: str, **kwargs: object):
        nonlocal snapshot_calls
        if args[:2] == ("ls-remote", "--refs"):
            snapshot_calls += 1
            return 128, "", "fatal: Authentication failed for credential-bearing URL"
        return original_git_cmd(repo, *args, **kwargs)

    monkeypatch.setattr(review_transport, "_git_cmd", fail_snapshot)

    with pytest.raises(
        ReviewTransportError,
        match=(
            r"^historical REPAIR snapshot acquisition failed: "
            r"exit_status=128 category=AUTH$"
        ),
    ):
        resolve_remote_repair_recovery(repo, failed_run_id="RUN-058-001")

    assert snapshot_calls == 1


def test_successful_snapshot_missing_failed_candidate_is_lineage_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = make_repo(tmp_path)
    root = git(repo, "rev-parse", "HEAD")
    candidate = commit_candidate(repo, "failed candidate")
    publish_failure(
        repo,
        tmp_path / "facts",
        run_id="RUN-058-001",
        candidate_sha=candidate,
        root_base_sha=root,
    )
    original_git_cmd = review_transport._git_cmd
    snapshot_calls = 0

    def omit_candidate(repo: Path, *args: str, **kwargs: object):
        nonlocal snapshot_calls
        result = original_git_cmd(repo, *args, **kwargs)
        if args[:2] != ("ls-remote", "--refs"):
            return result
        snapshot_calls += 1
        code, output, stderr = result
        output = "\n".join(
            line
            for line in output.splitlines()
            if "refs/heads/aios/failure/RUN-058-001" not in line
        )
        return code, output, stderr

    monkeypatch.setattr(review_transport, "_git_cmd", omit_candidate)

    with pytest.raises(
        ReviewTransportError,
        match=r"^canonical failed RUN refs missing for RUN-058-001$",
    ):
        resolve_remote_repair_recovery(repo, failed_run_id="RUN-058-001")

    assert snapshot_calls == 1


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ("not-a-ref-result", "malformed"),
        (
            "a" * 40 + "\trefs/heads/aios/failure/RUN-058-001\n"
            + "b" * 40 + "\trefs/heads/aios/failure/RUN-058-001",
            "ambiguous",
        ),
        ("a" * 40 + "\trefs/heads/aios/review/RUN-058-001", "prefix"),
        ("a" * 40 + "\trefs/heads/aios/failure/RUN-999-001", "identity"),
        ("not-an-object\trefs/heads/aios/failure/RUN-058-001", "identity"),
    ],
)
def test_historical_repair_snapshot_rejects_invalid_ref_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    message: str,
) -> None:
    repo, _ = make_repo(tmp_path)
    snapshot_calls = 0
    original_git_cmd = review_transport._git_cmd

    def invalid_snapshot(repo: Path, *args: str, **kwargs: object):
        nonlocal snapshot_calls
        if args[:2] == ("ls-remote", "--refs"):
            snapshot_calls += 1
            return 0, output, ""
        return original_git_cmd(repo, *args, **kwargs)

    monkeypatch.setattr(review_transport, "_git_cmd", invalid_snapshot)

    with pytest.raises(ReviewTransportError, match=message):
        resolve_remote_repair_recovery(repo, failed_run_id="RUN-058-001")

    assert snapshot_calls == 1


def test_failure_transport_recovers_byte_exact_optional_preverification_candidate(
    tmp_path: Path,
) -> None:
    repo, remote = make_repo(tmp_path)
    root = git(repo, "rev-parse", "HEAD")
    candidate = commit_candidate(repo, "verification failed")
    sidecar = b'{"exact":"pre-verification-candidate","version":1}'

    published = publish_failure(
        repo,
        tmp_path / "facts",
        run_id="RUN-058-010",
        candidate_sha=candidate,
        root_base_sha=root,
        preverification=sidecar,
    )
    recovery = resolve_remote_repair_recovery(
        repo, failed_run_id="RUN-058-010"
    )

    assert published[3] == sidecar
    assert recovery.failures[0].preverification == sidecar
    assert git(
        remote,
        "show",
        "refs/heads/aios/failure-artifacts/RUN-058-010:"
        ".ai/transport/pre-verification-candidate.json",
    ).encode() == sidecar


@pytest.mark.parametrize("corruption", ["candidate-ref", "failure-identity"])
def test_rejects_failed_head_or_failure_identity_mismatch(
    tmp_path: Path, corruption: str
) -> None:
    repo, remote = make_repo(tmp_path)
    root = git(repo, "rev-parse", "HEAD")
    candidate = commit_candidate(repo, "failed candidate")
    publish_failure(
        repo,
        tmp_path / "facts",
        run_id="RUN-058-004",
        candidate_sha=candidate,
        root_base_sha=root,
        failure_run_id=(
            "RUN-058-999" if corruption == "failure-identity" else None
        ),
    )
    if corruption == "candidate-ref":
        git(
            remote,
            "update-ref",
            "refs/heads/aios/failure/RUN-058-004",
            root,
        )

    message = (
        "failed-head ref mismatch"
        if corruption == "candidate-ref"
        else "FAILURE identity mismatch"
    )
    with pytest.raises(ReviewTransportError, match=message):
        resolve_remote_repair_recovery(repo, failed_run_id="RUN-058-004")


def test_rejects_cyclic_failed_run_continuation_lineage(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    files = tmp_path / "facts"
    root = git(repo, "rev-parse", "HEAD")
    first_head = commit_candidate(repo, "cyclic repair one")
    publish_failure(
        repo,
        files,
        run_id="RUN-058-004",
        candidate_sha=first_head,
        root_base_sha=root,
        continuation_of="RUN-058-005",
    )
    second_head = commit_candidate(repo, "cyclic repair two")
    publish_failure(
        repo,
        files,
        run_id="RUN-058-005",
        candidate_sha=second_head,
        root_base_sha=root,
        continuation_of="RUN-058-004",
    )

    with pytest.raises(
        ReviewTransportError, match="cyclic failed RUN continuation lineage"
    ):
        resolve_remote_repair_recovery(repo, failed_run_id="RUN-058-005")


def test_remote_run_namespace_includes_successes_and_rejects_remote_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = make_repo(tmp_path)
    files = tmp_path / "facts"
    root = git(repo, "rev-parse", "HEAD")
    failed_head = commit_candidate(repo, "failed candidate")
    publish_failure(
        repo,
        files,
        run_id="RUN-058-004",
        candidate_sha=failed_head,
        root_base_sha=root,
    )
    publish_success(
        repo,
        files,
        run_id="RUN-058-008",
        head_sha=failed_head,
        root_base_sha=root,
    )

    original_git_cmd = review_transport._git_cmd
    snapshot_calls = 0

    def track_git_cmd(repo: Path, *args: str, **kwargs: object):
        nonlocal snapshot_calls
        if args[:2] == ("ls-remote", "--refs"):
            snapshot_calls += 1
        return original_git_cmd(repo, *args, **kwargs)

    monkeypatch.setattr(review_transport, "_git_cmd", track_git_cmd)

    recovery = resolve_remote_repair_recovery(
        repo, failed_run_id="RUN-058-004"
    )
    assert recovery.remote_run_ids == ("RUN-058-004", "RUN-058-008")
    assert snapshot_calls == 1

    publish_success(
        repo,
        files,
        run_id="RUN-058-009",
        head_sha=failed_head,
        root_base_sha=root,
        failed_run_id="RUN-058-004",
    )
    snapshot_calls = 0
    with pytest.raises(
        ReviewTransportError,
        match="canonical continuation already exists for failed RUN: RUN-058-009",
    ):
        resolve_remote_repair_recovery(repo, failed_run_id="RUN-058-004")
    assert snapshot_calls == 1


def test_historical_repair_terminal_conflict_uses_one_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = make_repo(tmp_path)
    root = git(repo, "rev-parse", "HEAD")
    failed_head = commit_candidate(repo, "conflicting terminal candidate")
    with monkeypatch.context() as conflict_fixture:
        conflict_fixture.setattr(
            review_transport,
            "publish_terminal_attention",
            lambda *_args, **_kwargs: "PUBLISHED",
        )
        publish_failure(
            repo,
            tmp_path / "facts",
            run_id="RUN-058-004",
            candidate_sha=failed_head,
            root_base_sha=root,
        )
        publish_success(
            repo,
            tmp_path / "facts",
            run_id="RUN-058-004",
            head_sha=failed_head,
            root_base_sha=root,
        )
    original_git_cmd = review_transport._git_cmd
    snapshot_calls = 0

    def track_git_cmd(repo: Path, *args: str, **kwargs: object):
        nonlocal snapshot_calls
        if args[:2] == ("ls-remote", "--refs"):
            snapshot_calls += 1
        return original_git_cmd(repo, *args, **kwargs)

    monkeypatch.setattr(review_transport, "_git_cmd", track_git_cmd)

    with pytest.raises(
        ReviewTransportError,
        match="canonical RUN has conflicting terminal artifacts: RUN-058-004",
    ):
        resolve_remote_repair_recovery(repo, failed_run_id="RUN-058-004")

    assert snapshot_calls == 1


def test_reads_exact_historical_task_without_mutating_current_checkout(
    tmp_path: Path,
) -> None:
    repo, _ = make_repo(tmp_path)
    historical_head = git(repo, "rev-parse", "HEAD")
    historical_task = subprocess.run(
        (
            "git",
            "-C",
            str(repo),
            "show",
            f"{historical_head}:.ai/tasks/TASK-058.yaml",
        ),
        capture_output=True,
        check=True,
    ).stdout
    (repo / ".ai" / "tasks" / "TASK-058.yaml").write_text(
        "task_id: TASK-058\nrevision: 99\ngoal: current contract\n",
        encoding="utf-8",
    )
    git(repo, "add", ".ai/tasks/TASK-058.yaml")
    git(repo, "commit", "--quiet", "-m", "new current contract")
    git(repo, "push", "--quiet", "origin", "main")
    current_head = git(repo, "rev-parse", "HEAD")

    assert read_remote_task(
        repo, commit_sha=historical_head, task_id="TASK-058"
    ) == historical_task
    assert git(repo, "rev-parse", "HEAD") == current_head
    assert git(repo, "status", "--porcelain") == ""


@pytest.mark.parametrize("task_id", ["../TASK-058", "TASK/058", "TASK\\058"])
def test_historical_task_reader_rejects_noncanonical_identity(
    tmp_path: Path, task_id: str
) -> None:
    repo, _ = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")

    with pytest.raises(ReviewTransportError, match="invalid TASK id"):
        read_remote_task(repo, commit_sha=head, task_id=task_id)


@pytest.mark.parametrize(
    "commit_sha", ["", "HEAD", "--help", "a" * 39, "g" * 40]
)
def test_historical_task_reader_rejects_noncanonical_commit_sha(
    tmp_path: Path, commit_sha: str
) -> None:
    repo, _ = make_repo(tmp_path)

    with pytest.raises(
        ReviewTransportError, match="invalid historical TASK commit SHA"
    ):
        read_remote_task(repo, commit_sha=commit_sha, task_id="TASK-058")


def publish_remediation_artifacts(
    repo: Path,
    files: Path,
    *,
    run_id: str,
    task_id: str = "TASK-058",
    task_revision: int = 2,
    reviewed_sha: str,
    corrupt_run: bool = False,
) -> None:
    run = {
        "run_id": run_id,
        "task": {"id": task_id, "revision": task_revision},
        "executor": "codex",
        "base_sha": reviewed_sha,
        "workspace": "historical-workspace",
        "head_sha": None,
        "status": "ACTIVE",
    }
    result_data = {
        "result": {
            "head_sha": reviewed_sha,
            "claims": [
                {
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": "historical fix",
                    "evidence": ["E1"],
                }
            ],
            "changed_files": ["subject.txt"],
            "unresolved": [],
        },
        "evidence": [
            {
                "evidence_id": "E1",
                "run_id": run_id,
                "subject_sha": reviewed_sha,
                "type": "TEST",
                "source": {"command": "git status --porcelain"},
                "result": {"exit_code": 0, "summary": "verified"},
                "raw": {"path": ".ai/evidence/E1.log"},
            }
        ],
    }
    directory = files / run_id
    run_path = directory / "run.json"
    result_path = directory / "result.json"
    if corrupt_run:
        run_path.parent.mkdir(parents=True, exist_ok=True)
        run_path.write_bytes(b"not json")
    else:
        write_json(run_path, run)
    write_json(result_path, result_data)
    transport_post_pass(
        repo,
        run_id=run_id,
        head_sha=reviewed_sha,
        run_path=run_path,
        result_path=result_path,
    )


def publish_remediation_ref(
    repo: Path,
    *,
    source_run_id: str,
    finding_id: str,
    reviewed_sha: str,
    extra_reviews: int = 0,
    extra_remediations: int = 0,
) -> str:
    branch_name = f"branch-{source_run_id}-{finding_id}"
    git(repo, "checkout", "--quiet", "-b", branch_name, reviewed_sha)
    reviews_dir = repo / ".ai" / "reviews"
    remediations_dir = repo / ".ai" / "remediations"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    remediations_dir.mkdir(parents=True, exist_ok=True)
    (reviews_dir / f"REVIEW-{source_run_id}.yaml").write_text(
        f"review_id: REVIEW-{source_run_id}\nreviewed_sha: {reviewed_sha}\nmode: PRIMARY\nverdict: CHANGES_REQUIRED\nacceptance:\n  AC1: FAIL\nfindings:\n  - id: {finding_id}\n    basis: AC1\n    action: CODE_FIX\n    location: subject.txt\n    issue: issue\n    expected: expected\n",
        encoding="utf-8",
    )
    for i in range(extra_reviews):
        (reviews_dir / f"REVIEW-{source_run_id}-extra-{i}.yaml").write_text(
            f"review_id: REVIEW-{source_run_id}-extra-{i}\nreviewed_sha: {reviewed_sha}\nmode: PRIMARY\nverdict: CHANGES_REQUIRED\nacceptance:\n  AC1: FAIL\nfindings:\n  - id: {finding_id}\n    basis: AC1\n    action: CODE_FIX\n    location: subject.txt\n    issue: issue\n    expected: expected\n",
            encoding="utf-8",
        )
    (remediations_dir / f"REMEDIATION-{source_run_id}-{finding_id}.yaml").write_text(
        f"finding_id: {finding_id}\naction: CODE_FIX\nreviewed_sha: {reviewed_sha}\nmodification_scope:\n  - subject.txt\naffected_verification:\n  - git status --porcelain\nconstraints:\n  - hard: [c]\n",
        encoding="utf-8",
    )
    for i in range(extra_remediations):
        (remediations_dir / f"REMEDIATION-{source_run_id}-extra-{i}.yaml").write_text(
            f"finding_id: {finding_id}\naction: CODE_FIX\nreviewed_sha: {reviewed_sha}\nmodification_scope:\n  - subject.txt\naffected_verification:\n  - git status --porcelain\nconstraints:\n  - hard: [c]\n",
            encoding="utf-8",
        )
    ref = f"refs/heads/aios/remediation/{source_run_id}-{finding_id}"
    commit_fixture_state(
        repo,
        paths=(".ai",),
        message=f"remediation {source_run_id}-{finding_id}",
        user_name="Review Transport Test",
        user_email="transport@example.invalid",
        remote=repo.parent / "upstream.git",
        remote_ref=ref,
    )
    git(repo, "checkout", "--quiet", "main")
    git(repo, "branch", "--quiet", "-D", branch_name)
    return ref


def test_task_run_prefix_deterministic_derivation() -> None:
    assert task_run_prefix("TASK-066") == "RUN-066-"
    assert task_run_prefix("TASK-101") == "RUN-101-"
    assert task_run_prefix("066") == "RUN-066-"
    for invalid in ["", "TASK-", "TASK/066", "TASK\\066"]:
        with pytest.raises(ReviewTransportError, match="invalid TASK id"):
            task_run_prefix(invalid)


def test_remote_namespace_reports_exact_terminal_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = make_repo(tmp_path)
    files = tmp_path / "facts"
    root = git(repo, "rev-parse", "HEAD")
    candidate = commit_candidate(repo, "successful candidate")
    with monkeypatch.context() as conflict_fixture:
        conflict_fixture.setattr(
            review_transport,
            "publish_terminal_attention",
            lambda *_args, **_kwargs: "PUBLISHED",
        )
        publish_failure(
            repo,
            files,
            run_id="RUN-058-001",
            candidate_sha=root,
            root_base_sha=root,
        )
        publish_success(
            repo,
            files,
            run_id="RUN-058-001",
            head_sha=candidate,
            root_base_sha=root,
        )

    namespace = resolve_remote_run_namespace(
        repo, task_id="TASK-058", task_revision=2
    )
    recovery = resolve_remote_primary_recovery(repo, run_id="RUN-058-001")

    assert namespace.run_ids == ("RUN-058-001",)
    assert namespace.conflicts == ("RUN-058-001",)
    assert recovery.candidate_sha == candidate
    assert recovery.remote_run_ids == ("RUN-058-001",)


def test_remote_namespace_rejects_cross_task_terminal_content(
    tmp_path: Path,
) -> None:
    repo, remote = make_repo(tmp_path)
    commit = git(repo, "rev-parse", "HEAD")
    wrong_run = json.dumps(
        {
            "run_id": "RUN-058-009",
            "task": {"id": "TASK-999", "revision": 2},
            "executor": "codex",
            "base_sha": commit,
            "workspace": "historical",
            "head_sha": None,
            "status": "ACTIVE",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    blob = subprocess.run(
        ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
        input=wrong_run,
        capture_output=True,
        check=True,
    ).stdout.decode().strip()
    tree_input = f"100644 blob {blob}\trun.json\n".encode()
    tree = subprocess.run(
        ("git", "-C", str(repo), "mktree"),
        input=tree_input,
        capture_output=True,
        check=True,
    ).stdout.decode().strip()
    transport_tree = subprocess.run(
        ("git", "-C", str(repo), "mktree"),
        input=f"040000 tree {tree}\ttransport\n".encode(),
        capture_output=True,
        check=True,
    ).stdout.decode().strip()
    root_tree = subprocess.run(
        ("git", "-C", str(repo), "mktree"),
        input=f"040000 tree {transport_tree}\t.ai\n".encode(),
        capture_output=True,
        check=True,
    ).stdout.decode().strip()
    artifact_commit = git(repo, "commit-tree", root_tree, "-m", "wrong task")
    git(
        repo,
        "push",
        "--quiet",
        "origin",
        f"{artifact_commit}:refs/heads/aios/artifacts/RUN-058-009",
    )

    with pytest.raises(ReviewTransportError, match="TASK identity mismatch"):
        resolve_remote_run_namespace(
            repo, task_id="TASK-058", task_revision=2
        )


def test_cross_task_collision_ignored_during_discovery(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    files = tmp_path / "files"
    sha = git(repo, "rev-parse", "HEAD")

    # Unrelated TASK-041 lineage with structurally incompatible review layout (2 reviews)
    publish_remediation_ref(
        repo,
        source_run_id="RUN-041-002",
        finding_id="F1",
        reviewed_sha=sha,
        extra_reviews=1,
    )
    publish_remediation_artifacts(
        repo,
        files,
        run_id="RUN-041-002",
        task_id="TASK-041",
        task_revision=1,
        reviewed_sha=sha,
    )

    # Valid TASK-066 revision 1 lineage
    publish_remediation_ref(
        repo,
        source_run_id="RUN-066-001",
        finding_id="F1",
        reviewed_sha=sha,
    )
    publish_remediation_artifacts(
        repo,
        files,
        run_id="RUN-066-001",
        task_id="TASK-066",
        task_revision=1,
        reviewed_sha=sha,
    )

    lineages = resolve_remote_remediation_lineages(
        repo, finding_id="F1", task_id="TASK-066", task_revision=1
    )
    assert len(lineages) == 1
    assert lineages[0].source_run_id == "RUN-066-001"
    assert lineages[0].ref == "refs/heads/aios/remediation/RUN-066-001-F1"


def test_different_revision_lineage_not_candidate_during_discovery(
    tmp_path: Path,
) -> None:
    repo, _ = make_repo(tmp_path)
    files = tmp_path / "files"
    sha = git(repo, "rev-parse", "HEAD")

    publish_remediation_ref(
        repo, source_run_id="RUN-066-001", finding_id="F1", reviewed_sha=sha
    )
    publish_remediation_artifacts(
        repo,
        files,
        run_id="RUN-066-001",
        task_id="TASK-066",
        task_revision=1,
        reviewed_sha=sha,
    )

    publish_remediation_ref(
        repo, source_run_id="RUN-066-002", finding_id="F1", reviewed_sha=sha
    )
    publish_remediation_artifacts(
        repo,
        files,
        run_id="RUN-066-002",
        task_id="TASK-066",
        task_revision=2,
        reviewed_sha=sha,
    )

    # Resolving for revision 2 ignores revision 1
    lineages_r2 = resolve_remote_remediation_lineages(
        repo, finding_id="F1", task_id="TASK-066", task_revision=2
    )
    assert len(lineages_r2) == 1
    assert lineages_r2[0].source_run_id == "RUN-066-002"

    # Resolving for revision 1 ignores revision 2
    lineages_r1 = resolve_remote_remediation_lineages(
        repo, finding_id="F1", task_id="TASK-066", task_revision=1
    )
    assert len(lineages_r1) == 1
    assert lineages_r1[0].source_run_id == "RUN-066-001"


def test_malformed_lineage_for_exact_task_revision_fails_closed(
    tmp_path: Path,
) -> None:
    repo, remote = make_repo(tmp_path)
    files = tmp_path / "files"
    sha = git(repo, "rev-parse", "HEAD")

    # Attributable to requested exact TASK revision, but has 2 reviews
    ref = publish_remediation_ref(
        repo,
        source_run_id="RUN-066-001",
        finding_id="F1",
        reviewed_sha=sha,
        extra_reviews=1,
    )
    publish_remediation_artifacts(
        repo,
        files,
        run_id="RUN-066-001",
        task_id="TASK-066",
        task_revision=1,
        reviewed_sha=sha,
    )
    observed_sha = git(repo, "ls-remote", "origin", ref).split()[0]
    with pytest.raises(
        ReviewTransportError,
        match="canonical lineage at .* must contain exactly one REVIEW and REMEDIATION",
    ) as raised:
        resolve_remote_remediation_lineages(
            repo, finding_id="F1", task_id="TASK-066", task_revision=1
        )
    git(remote, "update-ref", ref, sha)

    assert raised.value.observed_refs == ((ref, observed_sha),)
    assert git(repo, "ls-remote", "origin", ref).split()[0] == sha
    assert raised.value.observed_refs[0][1] != sha


def test_missing_artifacts_for_exact_task_revision_fails_closed(
    tmp_path: Path,
) -> None:
    repo, _ = make_repo(tmp_path)
    sha = git(repo, "rev-parse", "HEAD")

    # Attributable to requested exact TASK revision, but artifacts are missing
    publish_remediation_ref(
        repo, source_run_id="RUN-066-001", finding_id="F1", reviewed_sha=sha
    )
    with pytest.raises(
        ReviewTransportError,
        match="canonical source artifacts missing or ambiguous for RUN-066-001",
    ):
        resolve_remote_remediation_lineages(
            repo, finding_id="F1", task_id="TASK-066", task_revision=1
        )


def test_two_valid_lineages_for_exact_task_revision_returned(
    tmp_path: Path,
) -> None:
    repo, _ = make_repo(tmp_path)
    files = tmp_path / "files"
    sha = git(repo, "rev-parse", "HEAD")

    publish_remediation_ref(
        repo, source_run_id="RUN-066-001", finding_id="F1", reviewed_sha=sha
    )
    publish_remediation_artifacts(
        repo,
        files,
        run_id="RUN-066-001",
        task_id="TASK-066",
        task_revision=1,
        reviewed_sha=sha,
    )

    publish_remediation_ref(
        repo, source_run_id="RUN-066-003", finding_id="F1", reviewed_sha=sha
    )
    publish_remediation_artifacts(
        repo,
        files,
        run_id="RUN-066-003",
        task_id="TASK-066",
        task_revision=1,
        reviewed_sha=sha,
    )

    lineages = resolve_remote_remediation_lineages(
        repo, finding_id="F1", task_id="TASK-066", task_revision=1
    )
    assert len(lineages) == 2


def test_transport_post_pass_includes_execution_profile(tmp_path: Path) -> None:
    repo, remote = make_repo(tmp_path)
    head_sha = git(repo, "rev-parse", "HEAD")
    run_id = "RUN-058-901"
    files = tmp_path / "files" / run_id
    run_path = files / "run.json"
    result_path = files / "result.json"
    profile_path = files / "execution-profile.json"

    write_json(
        run_path,
        {
            "run_id": run_id,
            "task": TASK,
            "executor": "codex",
            "base_sha": head_sha,
            "workspace": "ws",
            "head_sha": None,
            "status": "ACTIVE",
        },
    )
    write_json(
        result_path,
        {
            "result": {
                "head_sha": head_sha,
                "claims": [],
                "changed_files": [],
                "unresolved": [],
            },
            "evidence": [],
        },
    )
    profile_data = {
        "format": "AIOS_EXECUTION_PROFILE",
        "version": 1,
        "run_id": run_id,
        "executor": "codex",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "model_source": "REPOSITORY_DEFAULT",
        "effort_source": "REPOSITORY_DEFAULT",
    }
    write_json(profile_path, profile_data)

    transport_post_pass(
        repo,
        run_id=run_id,
        head_sha=head_sha,
        run_path=run_path,
        result_path=result_path,
        execution_profile_path=profile_path,
    )

    artifacts_ref = f"refs/heads/aios/artifacts/{run_id}"
    code, sha, _ = review_transport._git_cmd(repo, "ls-remote", "origin", artifacts_ref)
    assert code == 0 and sha
    commit_sha = sha.split()[0]
    remote_profile_bytes = review_transport._read_remote_blob(
        repo, "origin", commit_sha, ".ai/transport/execution-profile.json"
    )
    assert remote_profile_bytes is not None
    assert json.loads(remote_profile_bytes.decode("utf-8")) == profile_data

    # Idempotent re-transport succeeds
    transport_post_pass(
        repo,
        run_id=run_id,
        head_sha=head_sha,
        run_path=run_path,
        result_path=result_path,
        execution_profile_path=profile_path,
    )

    # Conflicting profile fails
    conflict_path = files / "conflict-profile.json"
    conflict_data = dict(profile_data, model="gpt-6-sol")
    write_json(conflict_path, conflict_data)
    with pytest.raises(ReviewTransportError, match="exists with different artifact content"):
        transport_post_pass(
            repo,
            run_id=run_id,
            head_sha=head_sha,
            run_path=run_path,
            result_path=result_path,
            execution_profile_path=conflict_path,
        )


def test_transport_failure_includes_execution_profile(tmp_path: Path) -> None:
    repo, remote = make_repo(tmp_path)
    head_sha = git(repo, "rev-parse", "HEAD")
    run_id = "RUN-058-902"
    files = tmp_path / "files" / run_id
    run_path = files / "run.json"
    failure_path = files / "failure.json"
    profile_path = files / "execution-profile.json"

    write_json(
        run_path,
        {
            "run_id": run_id,
            "task": TASK,
            "executor": "antigravity",
            "base_sha": head_sha,
            "workspace": "ws",
            "head_sha": None,
            "status": "ACTIVE",
        },
    )
    write_json(
        failure_path,
        {
            "kind": "FAILURE",
            "run_id": run_id,
            "task": TASK,
            "executor": "antigravity",
            "base_sha": head_sha,
            "failed_head_sha": head_sha,
            "phase": "EXECUTION",
            "candidate": {
                "transportable": False,
                "repairable": False,
                "dirty": False,
                "descends_from_base": True,
                "changed_files": [],
                "outside_task_scope": [],
            },
        },
    )
    profile_data = {
        "format": "AIOS_EXECUTION_PROFILE",
        "version": 1,
        "run_id": run_id,
        "executor": "antigravity",
        "model": "gemini-3.8-flash",
        "reasoning_effort": "high",
        "model_source": "REPOSITORY_DEFAULT",
        "effort_source": "REPOSITORY_DEFAULT",
    }
    write_json(profile_path, profile_data)

    transport_failure(
        repo,
        run_id=run_id,
        head_sha=head_sha,
        run_path=run_path,
        failure_path=failure_path,
        publish_candidate=False,
        execution_profile_path=profile_path,
    )

    artifacts_ref = f"refs/heads/aios/failure-artifacts/{run_id}"
    code, sha, _ = review_transport._git_cmd(repo, "ls-remote", "origin", artifacts_ref)
    assert code == 0 and sha
    commit_sha = sha.split()[0]
    remote_profile_bytes = review_transport._read_remote_blob(
        repo, "origin", commit_sha, ".ai/transport/execution-profile.json"
    )
    assert remote_profile_bytes is not None
    assert json.loads(remote_profile_bytes.decode("utf-8")) == profile_data

    # Idempotent re-transport succeeds
    transport_failure(
        repo,
        run_id=run_id,
        head_sha=head_sha,
        run_path=run_path,
        failure_path=failure_path,
        publish_candidate=False,
        execution_profile_path=profile_path,
    )

    # Conflicting profile fails
    conflict_path = files / "conflict-profile.json"
    conflict_data = dict(profile_data, reasoning_effort="low")
    write_json(conflict_path, conflict_data)
    with pytest.raises(ReviewTransportError, match="exists with different execution profile content"):
        transport_failure(
            repo,
            run_id=run_id,
            head_sha=head_sha,
            run_path=run_path,
            failure_path=failure_path,
            publish_candidate=False,
            execution_profile_path=conflict_path,
        )


def test_transport_post_pass_missing_remote_profile_fails_when_expected(tmp_path: Path) -> None:
    repo, remote = make_repo(tmp_path)
    head_sha = git(repo, "rev-parse", "HEAD")
    run_id = "RUN-058-903"
    files = tmp_path / "files" / run_id
    run_path = files / "run.json"
    result_path = files / "result.json"
    profile_path = files / "execution-profile.json"

    write_json(
        run_path,
        {
            "run_id": run_id,
            "task": TASK,
            "executor": "codex",
            "base_sha": head_sha,
            "workspace": "ws",
            "head_sha": None,
            "status": "ACTIVE",
        },
    )
    write_json(
        result_path,
        {
            "result": {
                "head_sha": head_sha,
                "claims": [],
                "changed_files": [],
                "unresolved": [],
            },
            "evidence": [],
        },
    )
    # First publish without profile (simulates remote missing profile)
    transport_post_pass(
        repo,
        run_id=run_id,
        head_sha=head_sha,
        run_path=run_path,
        result_path=result_path,
    )

    # Now local has a profile that is expected to be transported
    profile_data = {
        "format": "AIOS_EXECUTION_PROFILE",
        "version": 1,
        "run_id": run_id,
        "executor": "codex",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "model_source": "REPOSITORY_DEFAULT",
        "effort_source": "REPOSITORY_DEFAULT",
    }
    write_json(profile_path, profile_data)

    with pytest.raises(ReviewTransportError, match="exists with different artifact content"):
        transport_post_pass(
            repo,
            run_id=run_id,
            head_sha=head_sha,
            run_path=run_path,
            result_path=result_path,
            execution_profile_path=profile_path,
        )


def test_transport_failure_missing_remote_profile_fails_when_expected(tmp_path: Path) -> None:
    repo, remote = make_repo(tmp_path)
    head_sha = git(repo, "rev-parse", "HEAD")
    run_id = "RUN-058-904"
    files = tmp_path / "files" / run_id
    run_path = files / "run.json"
    failure_path = files / "failure.json"
    profile_path = files / "execution-profile.json"

    write_json(
        run_path,
        {
            "run_id": run_id,
            "task": TASK,
            "executor": "antigravity",
            "base_sha": head_sha,
            "workspace": "ws",
            "head_sha": None,
            "status": "ACTIVE",
        },
    )
    write_json(
        failure_path,
        {
            "kind": "FAILURE",
            "run_id": run_id,
            "task": TASK,
            "executor": "antigravity",
            "base_sha": head_sha,
            "failed_head_sha": head_sha,
            "phase": "EXECUTION",
            "candidate": {
                "transportable": False,
                "repairable": False,
                "dirty": False,
                "descends_from_base": True,
                "changed_files": [],
                "outside_task_scope": [],
            },
        },
    )
    # First publish without profile (simulating remote missing profile)
    transport_failure(
        repo,
        run_id=run_id,
        head_sha=head_sha,
        run_path=run_path,
        failure_path=failure_path,
        publish_candidate=False,
    )

    profile_data = {
        "format": "AIOS_EXECUTION_PROFILE",
        "version": 1,
        "run_id": run_id,
        "executor": "antigravity",
        "model": "gemini-3.8-flash",
        "reasoning_effort": "high",
        "model_source": "REPOSITORY_DEFAULT",
        "effort_source": "REPOSITORY_DEFAULT",
    }
    write_json(profile_path, profile_data)

    with pytest.raises(ReviewTransportError, match="exists with different execution profile content"):
        transport_failure(
            repo,
            run_id=run_id,
            head_sha=head_sha,
            run_path=run_path,
            failure_path=failure_path,
            publish_candidate=False,
            execution_profile_path=profile_path,
        )



# TASK-242 delivery aliases remain subordinate to immutable Admission Failure content.
def _delivery_diagnostic(
    root: Path, family: str = "PRIMARY", delivery_id: str = "task241-r1-primary-codex61-001",
    **changes,
) -> tuple[Path, str, dict]:
    import hashlib

    field = {
        "PRIMARY": "dispatch_id", "REMEDIATION": "correction_dispatch_id",
        "REPAIR": "repair_dispatch_id",
    }[family]
    payload = {
        "format": "AIOS_ADMISSION_FAILURE", "version": 2, "kind": "ADMISSION_FAILURE",
        "operation": family, field: delivery_id, "executor_invoked": False,
        "phase": "PRIMARY_SYNCHRONIZATION" if family == "PRIMARY" else "REPOSITORY_ADMISSION",
        "reason_code": "PRIMARY_SYNCHRONIZATION_REJECTED" if family == "PRIMARY" else "REPOSITORY_STATE_REJECTED",
        "error": {"type": "OperatorError", "message": "local branch has diverged from upstream"},
    }
    payload.update(changes)
    content = json.dumps(payload, sort_keys=True).encode()
    identity = hashlib.sha256(content).hexdigest()
    path = root / f"{identity}.json"
    path.write_bytes(content)
    return path, identity, payload


def _reader_snapshot(repo: Path) -> dict[str, bytes]:
    return {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*") if p.is_file()}


@pytest.mark.parametrize("family", ["PRIMARY", "REMEDIATION", "REPAIR"])
def test_delivery_alias_publish_replay_and_read_only_resolution(
    tmp_path: Path, family: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    repo, remote = make_repo(tmp_path)
    delivery = "task241-r1-primary-codex61-001"
    path, identity, payload = _delivery_diagnostic(tmp_path, family, delivery)
    alias = review_transport.admission_failure_delivery_ref(family, delivery)
    assert alias == (
        f"refs/heads/aios/admission-failure-delivery/{family.lower()}/"
        + hashlib.sha256(delivery.encode("ascii")).hexdigest()
    )
    content_ref = f"refs/heads/aios/admission-failure/{identity}"
    review_transport.transport_admission_failure(repo, identity=identity, diagnostic_path=path)
    commit = git(remote, "rev-parse", content_ref)
    assert git(remote, "rev-parse", alias) == commit
    assert json.loads(git(remote, "show", f"{commit}:.ai/transport/admission-failure.json")) == payload
    remote_before = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    calls = []
    real_git = review_transport._git_cmd

    def record_git(repo_path, *args, **kwargs):
        calls.append(args)
        return real_git(repo_path, *args, **kwargs)

    monkeypatch.setattr(review_transport, "_git_cmd", record_git)
    review_transport.transport_admission_failure(repo, identity=identity, diagnostic_path=path)
    assert not any(args[0] == "push" for args in calls)
    before = _reader_snapshot(repo)
    calls.clear()
    cause = review_transport.resolve_admission_failure_delivery(
        repo, family=family, delivery_id=delivery,
    )
    assert cause == {
        "authority": "AIOS_ADMISSION_FAILURE", "phase": payload["phase"],
        "reason_code": payload["reason_code"],
    }
    assert _reader_snapshot(repo) == before
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == remote_before
    queries = [args for args in calls if args[0] == "ls-remote"]
    assert queries == [
        ("ls-remote", "--refs", "canonical", alias),
        ("ls-remote", "--refs", "canonical", content_ref, alias),
    ]
    assert not any("run_created" in key or "run_id" == key for key in cause)


def test_delivery_alias_backfills_exact_legacy_content_commit(tmp_path: Path) -> None:
    repo, remote = make_repo(tmp_path)
    path, identity, _ = _delivery_diagnostic(tmp_path)
    ref = f"refs/heads/aios/admission-failure/{identity}"
    commit = review_transport._create_admission_failure_commit(repo, identity=identity, diagnostic_path=path)
    git(repo, "push", "--quiet", "origin", f"{commit}:{ref}")
    review_transport.transport_admission_failure(repo, identity=identity, diagnostic_path=path)
    assert git(remote, "rev-parse", ref) == commit
    alias = review_transport.admission_failure_delivery_ref("PRIMARY", "task241-r1-primary-codex61-001")
    assert git(remote, "rev-parse", alias) == commit

    old_path, old_identity, _ = _delivery_diagnostic(
        tmp_path, version=1, dispatch_id=None,
    )
    review_transport.transport_admission_failure(repo, identity=old_identity, diagnostic_path=old_path)
    legacy_commit = git(remote, "rev-parse", f"refs/heads/aios/admission-failure/{old_identity}")
    review_transport.transport_admission_failure(repo, identity=old_identity, diagnostic_path=old_path)
    assert git(remote, "rev-parse", f"refs/heads/aios/admission-failure/{old_identity}") == legacy_commit
    assert git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/aios/admission-failure-delivery/") == alias


@pytest.mark.parametrize("family", ["PRIMARY", "REMEDIATION", "REPAIR"])
def test_delivery_alias_rejects_changed_content_without_publishing_either_ref(
    tmp_path: Path, family: str,
) -> None:
    repo, remote = make_repo(tmp_path)
    path, identity, _ = _delivery_diagnostic(tmp_path, family)
    review_transport.transport_admission_failure(repo, identity=identity, diagnostic_path=path)
    before = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    different_path, different_identity, _ = _delivery_diagnostic(
        tmp_path, family, reason_code="DIFFERENT_REJECTION",
    )
    with pytest.raises(ReviewTransportError, match="different content"):
        review_transport.transport_admission_failure(repo, identity=different_identity, diagnostic_path=different_path)
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == before


def test_delivery_alias_rejects_foreign_delivery_collision(tmp_path: Path) -> None:
    repo, remote = make_repo(tmp_path)
    foreign, foreign_identity, _ = _delivery_diagnostic(tmp_path, delivery_id="other-delivery")
    commit = review_transport._create_admission_failure_commit(repo, identity=foreign_identity, diagnostic_path=foreign)
    alias = review_transport.admission_failure_delivery_ref("PRIMARY", "task241-r1-primary-codex61-001")
    git(repo, "push", "--quiet", "origin", f"{commit}:{alias}")
    before = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    path, identity, _ = _delivery_diagnostic(tmp_path)
    with pytest.raises(ReviewTransportError, match="different content"):
        review_transport.transport_admission_failure(repo, identity=identity, diagnostic_path=path)
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == before


def test_delivery_alias_atomic_creation_rejects_concurrent_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, remote = make_repo(tmp_path)
    foreign, foreign_identity, _ = _delivery_diagnostic(tmp_path, delivery_id="rival-delivery")
    rival = review_transport._create_admission_failure_commit(repo, identity=foreign_identity, diagnostic_path=foreign)
    path, identity, _ = _delivery_diagnostic(tmp_path)
    alias = review_transport.admission_failure_delivery_ref("PRIMARY", "task241-r1-primary-codex61-001")
    real_git = review_transport._git_cmd

    def race(repo_path, *args, **kwargs):
        if args[0] == "push":
            git(repo, "push", "--quiet", "origin", f"{rival}:{alias}")
        return real_git(repo_path, *args, **kwargs)

    monkeypatch.setattr(review_transport, "_git_cmd", race)
    with pytest.raises(ReviewTransportError, match="failed to push"):
        review_transport.transport_admission_failure(repo, identity=identity, diagnostic_path=path)
    assert git(remote, "rev-parse", alias) == rival
    assert git(remote, "for-each-ref", "--format=%(refname)", f"refs/heads/aios/admission-failure/{identity}") == ""


@pytest.mark.parametrize("invalid", [
    "family", "delivery", "field", "format", "version", "kind", "executor",
    "run", "verification", "review", "publication", "phase", "reason", "unknown",
    "missing-content-ref", "wrong-content-ref", "malformed-json", "duplicate-key",
])
def test_delivery_resolver_fails_closed_on_invalid_remote_diagnostic(
    tmp_path: Path, invalid: str,
) -> None:
    import hashlib

    repo, remote = make_repo(tmp_path)
    path, identity, payload = _delivery_diagnostic(tmp_path)
    changes = {
        "family": {"operation": "REPAIR"}, "delivery": {"dispatch_id": "other"},
        "field": {"repair_dispatch_id": payload["dispatch_id"]},
        "format": {"format": "FAILURE"}, "version": {"version": True},
        "kind": {"kind": "FAILURE"}, "executor": {"executor_invoked": True},
        "run": {"run_created": True}, "verification": {"verification": "PASS"},
        "review": {"review": "PASS"}, "publication": {"publication": "main"},
        "phase": {"phase": "X" * 65}, "reason": {"reason_code": "X" * 129},
        "unknown": {"success": True},
    }
    payload.update(changes.get(invalid, {}))
    content = json.dumps(payload).encode()
    if invalid == "malformed-json":
        content = b"{"
    elif invalid == "duplicate-key":
        content = content[:-1] + b', "executor_invoked": false}'
    path.write_bytes(content)
    identity = hashlib.sha256(content).hexdigest()
    commit = review_transport._create_admission_failure_commit(repo, identity=identity, diagnostic_path=path)
    alias = review_transport.admission_failure_delivery_ref("PRIMARY", "task241-r1-primary-codex61-001")
    specs = [f"{commit}:{alias}"]
    if invalid != "missing-content-ref":
        ref_identity = "a" * 64 if invalid == "wrong-content-ref" else identity
        specs.append(f"{commit}:refs/heads/aios/admission-failure/{ref_identity}")
    git(repo, "push", "--quiet", "origin", *specs)
    before = _reader_snapshot(repo)
    remote_before = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    with pytest.raises(ReviewTransportError):
        review_transport.resolve_admission_failure_delivery(
            repo, family="PRIMARY", delivery_id="task241-r1-primary-codex61-001",
        )
    assert _reader_snapshot(repo) == before
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == remote_before


def test_delivery_resolver_absence_does_not_scan_unrelated_admissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = make_repo(tmp_path)
    path, identity, _ = _delivery_diagnostic(tmp_path, delivery_id="unrelated")
    review_transport.transport_admission_failure(repo, identity=identity, diagnostic_path=path)
    calls = []
    real_git = review_transport._git_cmd

    def record(repo_path, *args, **kwargs):
        calls.append(args)
        return real_git(repo_path, *args, **kwargs)

    monkeypatch.setattr(review_transport, "_git_cmd", record)
    before = _reader_snapshot(repo)
    assert review_transport.resolve_admission_failure_delivery(
        repo, family="PRIMARY", delivery_id="absent",
    ) is None
    assert _reader_snapshot(repo) == before
    assert [args for args in calls if args[0] == "ls-remote"] == [
        ("ls-remote", "--refs", "canonical",
         review_transport.admission_failure_delivery_ref("PRIMARY", "absent")),
    ]
    assert len({review_transport.admission_failure_delivery_ref(family, "same-delivery")
                for family in ("PRIMARY", "REMEDIATION", "REPAIR")}) == 3

def test_delivery_alias_preserves_remote_query_failure_diagnostic(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    path, identity, payload = _delivery_diagnostic(
        tmp_path, reason_code="REMOTE_TRANSPORT_UNAVAILABLE",
        remote_query={"operation": "LS_REMOTE", "outcome": "UNAVAILABLE", "category": "DNS"},
        observed_ref_count=1, observed_refs=[{"ref": "refs/heads/main", "sha": "a" * 40}],
    )
    review_transport.transport_admission_failure(repo, identity=identity, diagnostic_path=path)
    assert review_transport.resolve_admission_failure_delivery(
        repo, family="PRIMARY", delivery_id=payload["dispatch_id"],
    ) == {
        "authority": "AIOS_ADMISSION_FAILURE", "phase": payload["phase"],
        "reason_code": "REMOTE_TRANSPORT_UNAVAILABLE",
    }


def test_delivery_alias_rejects_identical_bytes_at_conflicting_commits(tmp_path: Path) -> None:
    repo, remote = make_repo(tmp_path)
    path, identity, payload = _delivery_diagnostic(tmp_path)
    review_transport.transport_admission_failure(repo, identity=identity, diagnostic_path=path)
    content_ref = f"refs/heads/aios/admission-failure/{identity}"
    original = git(remote, "rev-parse", content_ref)
    # A child with the same tree is a distinct commit despite identical diagnostic bytes.
    tree = git(repo, "rev-parse", f"{original}^{{tree}}")
    different = git(repo, "commit-tree", tree, "-p", original, "-m", "conflicting alias commit")
    alias = review_transport.admission_failure_delivery_ref("PRIMARY", payload["dispatch_id"])
    git(repo, "push", "--quiet", "--force", "origin", f"{different}:{alias}")
    before = git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    with pytest.raises(ReviewTransportError, match="commit conflict"):
        review_transport.transport_admission_failure(repo, identity=identity, diagnostic_path=path)
    with pytest.raises(ReviewTransportError, match="identity conflict"):
        review_transport.resolve_admission_failure_delivery(
            repo, family="PRIMARY", delivery_id=payload["dispatch_id"],
        )
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == before
