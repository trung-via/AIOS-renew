"""Synthetic exact-source regressions. No live delivery or lifecycle operations."""

from fnmatch import fnmatchcase
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from aios_renew import brain_attention as brain
from aios_renew import local_chat_wake as wake
from aios_renew.operational_receipt import workflow_failure_receipt


IDENTITY = dict(run_id="RUN-fixture-001", artifact_sha="a" * 40,
                decision_sha="b" * 40, reviewed_sha="c" * 40)
BODY = "a rejected immutable carrier"


class Task372CanonicalSources:
    """Immutable canonical RUN/FAILURE documents through the real attention reader."""
    def __init__(self, *, run_id="RUN-369-001", task_id="TASK-369"):
        from tests.test_review_transport import _native_failure_fixture
        from aios_renew.review_transport import public_failure
        run, private = _native_failure_fixture()
        self.artifact_sha = "e" * 40
        self.run_id = run_id
        run.update(run_id=run_id, task=dict(id=task_id, revision=1), workspace="protected-runtime-workspace")
        private.update(run_id=run_id, task=run["task"], phase="VERIFICATION")
        private["error"] = dict(type="RuntimeVerificationError", message="Runtime verification failed",
            verification=[dict(command="python -m pytest -q tests/test_fixture.py", exit_code=1,
                summary="========== short test summary info ==========\nFAILED tests/test_fixture.py::test_parse[credential=synthetic-secret] - TypeError: malformed fixture\n1 failed")])
        self.run, self.failure = run, json.loads(public_failure(json.dumps(private).encode()))
        self.values = {"refs/heads/aios/failure-artifacts/" + run_id: self.artifact_sha,
                       "refs/heads/aios/failure/" + run_id: private["failed_head_sha"]}
        self.moved, self.reads = False, 0

    def refs(self, *patterns):
        self.reads += 1
        result = {k: v for k, v in self.values.items() if any(fnmatchcase(k, pattern) for pattern in patterns)}
        if self.moved and self.reads >= 3:
            result["refs/heads/aios/failure-artifacts/" + self.run_id] = "f" * 40
        return result

    def document(self, sha, name):
        if sha != self.artifact_sha:
            raise brain.AttentionError("FAILURE_SOURCE_IDENTITY_MISMATCH")
        if name == ".ai/transport/run.json": return copy.deepcopy(self.run)
        if name == ".ai/transport/failure.json": return copy.deepcopy(self.failure)
        raise OSError("protected source absent")


def test_task372_attention_reads_canonical_failure_and_exact_operational_job():
    from tests.test_operational_receipt import Task372Actions
    canonical = Task372CanonicalSources()
    item = brain.attention(brain.FAILURE, dict(run_id=canonical.run_id, artifact_sha=canonical.artifact_sha))
    context = brain.terminal_failure_context(item, canonical)
    assert context["safe_detail"]["verification"][0]["failed_nodes"][0]["error_types"] == ["TypeError"]
    assert "synthetic-secret" not in json.dumps(context)
    backend = Task372Actions()
    source = brain.source_document([brain.operational_observation(backend.receipt)])
    pointer = dict(workflow_run_id=backend.run_id, run_attempt=1, artifact_id=22, source_digest=brain.digest(source))
    event = brain.project_source(source, pointer)[0]
    observed = brain.operational_context(source, event, pointer, backend)
    assert observed["workflow_context"]["attempt"]["jobs"][0]["job_id"] == backend.job_id
    assert observed["workflow_context"]["run_created"] is False


def test_task372_attention_rejects_moved_canonical_failure():
    sources = Task372CanonicalSources()
    sources.moved, sources.reads = True, 1
    item = brain.attention(brain.FAILURE, dict(run_id=sources.run_id, artifact_sha=sources.artifact_sha))
    with pytest.raises(brain.AttentionError, match="FAILURE_SOURCE_MOVED"):
        brain.terminal_failure_context(item, sources)


def source_event(observation):
    source = brain.source_document([observation])
    pointer = dict(workflow_run_id=100, run_attempt=1, artifact_id=200, source_digest=brain.digest(source))
    return brain.project_source(source, pointer)[0], source, pointer


def rejection(operation="UNKNOWN", carrier="INGRESS"):
    return dict(boundary="CARRIER_REJECTED", carrier=carrier, operation=operation,
                subject_id="TASK-fixture" if operation == "AUTHOR_TASK" else "NONE" if operation == "UNKNOWN" else "RUN-fixture-001",
                subject_sha="0" * 40 if operation == "UNKNOWN" else "c" * 40,
                finding_id="F1" if operation == "AUTHOR_REMEDIATION" else "NONE", issue_id=10, issue_number=2,
                body_digest=hashlib.sha256(BODY.encode()).hexdigest(), run_created=False)


def delivery(boundary="DISPATCH_REJECTED", operation="PRIMARY"):
    return dict(boundary=boundary, operation=operation, delivery_id="delivery-fixture", run_created=False,
                subject_id="TASK-fixture" if operation == "PRIMARY" else "RUN-fixture-001",
                subject_sha="d" * 40, finding_id="NONE" if operation != "REMEDIATION" else "F1")


def examples():
    observations = [rejection(), rejection("SUBMIT_REVIEW"), rejection("AUTHOR_REMEDIATION"),
                    delivery(), delivery("PRE_AIOS_FAILED"),
                    dict(boundary="REVIEW_FOLLOWUP", **IDENTITY),
                    dict(boundary="PUBLICATION_FAILED", **IDENTITY, stage="EXECUTION"),
                    dict(boundary="PUBLICATION_PROVEN", **IDENTITY, published_sha=IDENTITY["reviewed_sha"]),
                    dict(boundary="CANONICAL_CONFLICT", prepared_sha="c" * 40, prepared_digest="e" * 64,
                         predecessor_ref="refs/heads/main", predecessor_sha="d" * 40, observed_sha="f" * 40)]
    items = [source_event(obs)[0] for obs in observations]
    items += [brain.attention(brain.RESULT, dict(run_id=IDENTITY["run_id"], artifact_sha=IDENTITY["artifact_sha"])),
              brain.attention(brain.FAILURE, dict(run_id=IDENTITY["run_id"], artifact_sha=IDENTITY["artifact_sha"]))]
    items += [brain.attention(brain.RECOVERY, dict(original_event_id=items[0].event_id))]
    return items


def test_registry_covers_exact_h4a5_matrix_and_preserves_terminal_identity():
    expected = {
        "INGRESS_OR_CARRIER_REJECTION", "PRIMARY_REMEDIATION_REPAIR_DISPATCH_REJECTION",
        "PRE_AIOS_OPERATIONAL_FAILURE_REQUIRING_DIAGNOSIS", "RUN_RESULT_REQUIRING_SEMANTIC_REVIEW",
        "RUN_FAILURE_REQUIRING_CORRECTION_REASONING", "REVIEW_INGRESS_REJECTION",
        "REVIEW_CHANGES_REQUIRED_OR_BLOCKED", "REMEDIATION_OR_REPAIR_AUTHORING_REJECTION",
        "PUBLICATION_DISPATCH_OR_EXECUTION_FAILURE", "PUBLICATION_SUCCESS_REQUIRING_HUMAN_BRAIN_PLANNING",
        "CANONICAL_CONFLICT_OR_STALENESS", "WAKE_DELIVERY_RECOVERY_FOR_UNRESOLVED_ATTENTION",
    }
    registry = brain.validate_registry(Path(__file__).resolve().parents[1] / ".ai/brain-attention-families.yaml")
    assert set(registry["families"]) == expected == {item.family for item in examples()}
    for item in examples():
        assert brain.parse_body(item.render()) == item
        assert brain.parse_event_id(item.event_id) == item
        assert wake.EVENT_PATTERN.fullmatch(item.event_id)
        assert brain.attention(item.family, dict(reversed(list(item.selectors.items())))).event_id == item.event_id
    assert examples()[-3].event_id == "terminal:RESULT:RUN-fixture-001:" + "a" * 40
    assert examples()[-2].event_id == "terminal:FAILURE:RUN-fixture-001:" + "a" * 40


@pytest.mark.parametrize("field", ["next_action", "correction_strategy", "correction_action", "action",
                                  "verdict", "review_verdict", "roadmap_successor", "executor", "model",
                                  "reasoning_effort", "effort", "command", "cmd", "script", "chat_url",
                                  "cdp_endpoint", "state_path", "account", "assistant_output", "ack"])
def test_privacy_and_semantic_authority_cannot_enter_any_envelope_or_selector(field):
    for item in examples():
        root = item.as_dict()
        root[field] = "private or executable text"
        with pytest.raises(brain.AttentionError):
            brain.parse_body(json.dumps(root))
        root = item.as_dict()
        root["selectors"][field] = "private or executable text"
        with pytest.raises(brain.AttentionError):
            brain.parse_body(json.dumps(root))
        for selector in item.selectors:
            fields = dict(item.selectors)
            fields[selector] = {field: "nested substitution"}
            with pytest.raises(brain.AttentionError):
                brain.attention(item.family, fields)


def test_unknown_malformed_substituted_family_selector_and_noncanonical_serialization_fail_closed():
    item = source_event(delivery())[0]
    root = item.as_dict()
    root["family"] = brain.PRE_AIOS
    with pytest.raises(brain.AttentionError):
        brain.parse_body(json.dumps(root))
    for family in ("UNKNOWN", "primary", 1, None):
        with pytest.raises(brain.AttentionError):
            brain.attention(family, dict(item.selectors))
    for raw in ("{}", '{"format":1,"format":2}', "x" * 8193, b"\xff", "[]"):
        with pytest.raises(brain.AttentionError):
            brain.parse_body(raw)
    for invalid in (item.event_id + "=", item.event_id.replace("v1", "v2"), "terminal:PASS:RUN-1:" + "a" * 40):
        with pytest.raises(brain.AttentionError):
            brain.parse_event_id(invalid)
    fields = dict(item.selectors, run_created=True)
    with pytest.raises(brain.AttentionError):
        brain.attention(item.family, fields)
    for key in ("workflow_run_id", "artifact_id", "source_boundary", "family", "command"):
        with pytest.raises(brain.AttentionError):
            source_event({**delivery(), key: "substituted"})
    fields = dict(item.selectors, subject_id="RUN-substituted-001")
    with pytest.raises(brain.AttentionError):
        brain.attention(item.family, fields)


@pytest.mark.parametrize("operation,family", [
    ("UNKNOWN", brain.INGRESS), ("AUTHOR_TASK", brain.INGRESS),
    ("SUBMIT_REVIEW", brain.REVIEW_INGRESS), ("AUTHOR_REMEDIATION", brain.AUTHORING), ("AUTHOR_REPAIR", brain.AUTHORING),
])
def test_each_carrier_or_authoring_rejection_has_one_exact_identity(operation, family):
    item, source, pointer = source_event(rejection(operation))
    assert item.family == family and item.selectors["run_created"] is False
    assert len(brain.project_source(source, pointer)) == 1
    assert brain.project_source(source, pointer)[0].event_id == item.event_id
    changed = {**pointer, "artifact_id": 201}
    assert brain.project_source(source, changed)[0].event_id != item.event_id
    with pytest.raises(brain.AttentionError):
        brain.project_source(source, {**pointer, "source_digest": "0" * 64})
    doubled = brain.source_document(source["observations"] * 2)
    with pytest.raises(brain.AttentionError):
        brain.project_source(doubled, {**pointer, "source_digest": brain.digest(doubled)})


@pytest.mark.parametrize("boundary", ["CARRIER_ADMITTED", "DISPATCH_ACCEPTED", "DISPATCH_REQUEST_ACCEPTED",
                                      "RUNNER_STARTED", "AIOS_INVOKED", "ADMISSION_ACCEPTED", "RUN_ATTRIBUTED",
                                      "TERMINAL_POINTER", "SELF_HOST_COMPLETED", "EXECUTOR_IN_PROGRESS",
                                      "VERIFICATION_IN_PROGRESS", "AUTO_PUBLICATION_STARTED"])
def test_entire_progress_matrix_produces_no_attention(boundary):
    source = brain.source_document([dict(boundary=boundary)])
    pointer = dict(workflow_run_id=1, run_attempt=1, artifact_id=2, source_digest=brain.digest(source))
    assert brain.project_source(source, pointer) == []
    with pytest.raises(brain.AttentionError):
        brain.project_observation(dict(boundary=boundary, next_action="RUN"), pointer)


@pytest.mark.parametrize("operation", ["PRIMARY", "REMEDIATION", "REPAIR"])
def test_pre_aios_operational_receipts_are_subordinate_selector_only_and_do_not_fabricate_run(operation):
    receipt = workflow_failure_receipt(operation, "delivery-fixture", "CONFIGURED_REPOSITORY_UNAVAILABLE",
                                       selectors={"model": "private model", "reasoning_effort": "private effort", "executor": "codex"})
    observation = receipt.attention_observation()
    item = source_event(observation)[0]
    assert item.family == brain.PRE_AIOS
    assert item.selectors["run_created"] is False
    assert "run_id" not in item.selectors and "terminal_kind" not in item.selectors
    assert "private" not in item.render() and "model" not in item.render()
    for boundary in ("RUNNER_STARTED", "DISPATCH_REQUEST_ACCEPTED", "AIOS_INVOKED"):
        progress = receipt.as_dict()
        progress["boundary"] = boundary
        assert brain.operational_observation(progress) == {"boundary": boundary}
    forged = receipt.as_dict()
    forged["run_created"] = True
    forged["run_id"] = "RUN-fabricated-001"
    with pytest.raises(brain.AttentionError):
        brain.operational_observation(forged)
    forged = receipt.as_dict()
    forged["cause"]["phase"] = "DOWNSTREAM_WORKFLOW"
    with pytest.raises(brain.AttentionError):
        brain.operational_observation(forged)


def test_bp2_cause_remains_source_bound_and_old_observations_do_not_invent_cause():
    receipt = workflow_failure_receipt("PRIMARY", "delivery-synthetic", "CONFIGURED_REPOSITORY_UNAVAILABLE")
    observation = brain.operational_observation(receipt.as_dict())
    item, source, pointer = source_event(observation)
    context = brain.operational_context(source, item, pointer)
    assert context["cause"] == receipt.as_dict()["cause"]
    assert context["source"] == pointer and context["run_created"] is False
    assert all(key not in context for key in ("run_id", "failure", "correction", "repair_id"))
    historical = dict(observation); historical.pop("operational_cause")
    old, source, pointer = source_event(historical)
    assert brain.operational_context(source, old, pointer)["cause_availability"] == "UNAVAILABLE"
    hostile = dict(observation, operational_cause=dict(authority="WORKFLOW", phase="PRE_AIOS",
                                                     reason_code="credential=synthetic-secret"))
    with pytest.raises(brain.AttentionError):
        source_event(hostile)


class Sources:
    def __init__(self, verdict="CHANGES_REQUIRED"):
        self.identity = dict(IDENTITY)
        self.decision = dict(review_id="REVIEW-fixture-001", verdict=verdict,
                             findings=[{"id": "F1", "action": "CODE_FIX"}, {"id": "F2", "action": "EVIDENCE_ONLY"}])
        self.values = {"refs/heads/main": "d" * 40}
        self.values.update({f"refs/heads/aios/{namespace}/{IDENTITY['run_id']}": IDENTITY[key] for namespace, key in
                           (("artifacts", "artifact_sha"), ("review", "reviewed_sha"), ("review-decision", "decision_sha"))})
        self.docs = {("d" * 40, ".ai/roadmap-state.yaml"): {"version": 1, "sequence": []}}
        self.is_included = False

    def review(self, run_id, decision_sha=None):
        assert run_id == IDENTITY["run_id"]
        if decision_sha is not None and decision_sha != self.identity["decision_sha"]:
            raise brain.AttentionError("UNPROVEN_REVIEW_IDENTITY")
        return self.identity, self.decision

    def refs(self, *patterns):
        return {ref: sha for ref, sha in self.values.items() if any(fnmatchcase(ref, pattern) for pattern in patterns)}

    def document(self, sha, name):
        return self.docs[sha, name]

    def frozen(self, refs):
        observation = copy.copy(self)
        observation.values = dict(refs)
        return observation

    def remediation(self, sha, subject, decision):
        return self.document(sha, f".ai/remediations/REMEDIATION-{subject}.yaml")

    def optional_document(self, sha, name):
        return self.docs.get((sha, name))

    def included(self, sha, main):
        assert sha == IDENTITY["reviewed_sha"]
        return self.is_included


def test_publication_awaiting_recovered_review_is_canonical_progress_not_publication(tmp_path, monkeypatch):
    from tests.test_publication import recover_publication_fixture
    from aios_renew import operator, publication
    from dataclasses import asdict
    lineage, main, calls, runner = recover_publication_fixture(tmp_path, monkeypatch)
    recovered = operator.recover_publication_source(lineage["run_id"], decision_sha=lineage["decision_sha"],
        expected_main_sha=main, repo=lineage["repo"], verification_runner=runner)
    report = publication.PublicationReport(lineage["run_id"], lineage["candidate_sha"], main,
        "AWAITING_REVIEW", "canonical new RESULT; no verdict")
    report_path, source_path = tmp_path / "report.json", tmp_path / "source.json"
    report_path.write_text(json.dumps({**asdict(report), "classification": report.classification}), encoding="utf-8")
    source = brain.capture_publication(report_path, source_path, lineage["run_id"], lineage["decision_sha"], repo=lineage["repo"])
    assert source["observations"] == [] and len(calls) == 1
    assert brain.publication_event(source_path, 100, 1, 200, brain.digest(source)) == ""
    from tests.test_publication import git
    git(lineage["remote"], "update-ref", "-d", f"refs/heads/aios/artifacts/{recovered.run_id}")
    with pytest.raises(brain.AttentionError, match="UNPROVEN_PUBLICATION_CONTINUATION"):
        brain.capture_publication(report_path, source_path, lineage["run_id"], lineage["decision_sha"], repo=lineage["repo"])


def test_publication_classification_and_lineage_cannot_forge_publication_success(tmp_path, monkeypatch):
    from aios_renew.publication import PublicationReport, PublicationLineage
    from dataclasses import asdict
    sources = Sources("PASS")
    monkeypatch.setattr(brain, "GitSources", lambda *_args: sources)
    report = PublicationReport(IDENTITY["run_id"], IDENTITY["reviewed_sha"], "d" * 40,
        "FAILED", "runner accepted", lineage=PublicationLineage(IDENTITY["decision_sha"], IDENTITY["artifact_sha"]))
    path, output = tmp_path / "report.json", tmp_path / "source.json"
    path.write_text(json.dumps({**asdict(report), "classification": "PUBLISHABLE"}), encoding="utf-8")
    with pytest.raises(brain.AttentionError, match="UNPROVEN_PUBLICATION_CLASSIFICATION"):
        brain.capture_publication(path, output, IDENTITY["run_id"], IDENTITY["decision_sha"])
    altered = asdict(report)
    altered["lineage"]["artifacts_sha"] = "0" * 40
    path.write_text(json.dumps({**altered, "classification": report.classification}), encoding="utf-8")
    with pytest.raises(brain.AttentionError, match="UNPROVEN_PUBLICATION_LINEAGE"):
        brain.capture_publication(path, output, IDENTITY["run_id"], IDENTITY["decision_sha"])


class Artifacts:
    def __init__(self, source=None):
        self.source = source
        self.successor = None
        self.body = BODY

    def artifact(self, pointer):
        return self.source

    def request(self, path):
        assert path == "/issues/2"
        return dict(id=10, body=self.body, state="closed")

    def delivery_successor(self, selectors):
        return self.successor


def publication_artifacts(source):
    """Exercise the real bounded artifact reader with a synthetic completed run."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("source.json", brain.canonical(source))
    name = "aios-attention-source-v1-publication-attempt-2"
    reader = brain.ArtifactSources(brain.REPOSITORY)

    def request(path, binary=False):
        if path == "/actions/runs/100/attempts/2":
            return dict(id=100, run_attempt=2, status="completed", event="push",
                        path=".github/workflows/aios-auto-publish.yml",
                        head_repository=dict(full_name=brain.REPOSITORY))
        if path == "/actions/runs/100/artifacts?per_page=100":
            return dict(total_count=1, artifacts=[dict(id=200, name=name)])
        if path == "/actions/artifacts/200":
            return dict(id=200, name=name, expired=False, size_in_bytes=len(archive.getvalue()),
                        workflow_run=dict(id=100))
        assert path == "/actions/artifacts/200/zip" and binary is True
        return archive.getvalue()

    reader.request = request
    return reader


@pytest.mark.parametrize("outcome,family", [
    ("PUBLISHED", brain.PUBLICATION_SUCCESS), ("ALREADY_PUBLISHED", brain.PUBLICATION_SUCCESS),
    ("ALREADY_INCLUDED", brain.PUBLICATION_SUCCESS), ("FAILED", brain.PUBLICATION_FAILURE),
    ("RECOVERY_BLOCKED", brain.CONFLICT), ("RECOVERY_REVIEW_REQUIRED", brain.CONFLICT),
])
def test_direct_publication_and_artifact_collection_share_exact_identity(monkeypatch, tmp_path, outcome, family):
    sources = Sources("PASS")
    sources.is_included = family == brain.PUBLICATION_SUCCESS
    monkeypatch.setattr(brain, "GitSources", lambda *args: sources)
    report_path, source_path, output_path = [tmp_path / name for name in ("report.json", "source.json", "capture.out")]
    report = dict(source_run=IDENTITY["run_id"], reviewed_sha=IDENTITY["reviewed_sha"],
        prior_main_sha="e" * 40, outcome=outcome, detail="private publisher diagnostic")
    if outcome in {"RECOVERY_BLOCKED", "RECOVERY_REVIEW_REQUIRED"}:
        report.update(prior_main_sha="d" * 40, cause="MERGE_CONFLICT" if outcome == "RECOVERY_BLOCKED"
                      else "FRESH_EXACT_REVIEW_REQUIRED", blocker=None,
                      recovery=None if outcome == "RECOVERY_BLOCKED" else dict(
                          merge_base_sha="e" * 40, tree_sha="f" * 40, publication_eligible=False))
    report_path.write_text(json.dumps(report), encoding="utf-8")
    assert brain.main(["publication", "--report", str(report_path), "--source", str(source_path),
        "--run-id", IDENTITY["run_id"], "--decision-sha", IDENTITY["decision_sha"], "--output", str(output_path)]) == 0
    source = brain.load_json(source_path.read_bytes(), brain.MAX_SOURCE_BYTES)
    source_digest = brain.digest(source)
    assert output_path.read_text(encoding="utf-8") == "source_digest=" + source_digest + "\n"
    event_id = brain.publication_event(source_path, 100, 2, 200, source_digest)
    assert brain.collect(100, 2, artifacts=publication_artifacts(source)) == [event_id]
    item = brain.parse_event_id(event_id)
    assert item.family == family
    assert "private" not in item.render() and "detail" not in item.selectors
    pointer = dict(workflow_run_id=100, run_attempt=2, artifact_id=200, source_digest=source_digest)
    if family == brain.PUBLICATION_SUCCESS:
        assert set(item.selectors).isdisjoint(pointer)
        assert brain.publication_event(source_path, 101, 3, 201, source_digest) == event_id
    else:
        assert {key: item.selectors[key] for key in pointer} == pointer
        for changed in ((101, 2, 200), (100, 3, 200), (100, 2, 201)):
            assert brain.publication_event(source_path, *changed, source_digest) != event_id
    with pytest.raises(brain.AttentionError):
        brain.publication_event(source_path, 100, 2, 200, "0" * 64)
    direct_output = tmp_path / "direct.out"
    assert brain.main(["publication-event", "--source", str(source_path), "--workflow-run-id", "100",
        "--attempt", "2", "--artifact-id", "200", "--source-digest", source_digest,
        "--repository", brain.REPOSITORY, "--output", str(direct_output)]) == 0
    assert direct_output.read_text(encoding="utf-8") == "event_id=" + event_id + "\n"


@pytest.mark.parametrize("boundary", sorted(brain.NON_WAKE) + [None])
def test_direct_publication_progress_and_empty_source_emit_no_identity(tmp_path, boundary):
    source = brain.source_document([] if boundary is None else [dict(boundary=boundary)])
    path = tmp_path / "source.json"
    path.write_bytes(brain.canonical(source))
    assert brain.publication_event(path, 100, 2, 200, brain.digest(source)) == ""
    assert brain.collect(100, 2, artifacts=publication_artifacts(source)) == []


def test_intermediate_integration_required_never_rings_brain(monkeypatch, tmp_path):
    sources = Sources("PASS")
    monkeypatch.setattr(brain, "GitSources", lambda *args: sources)
    report_path, source_path = tmp_path / "report.json", tmp_path / "source.json"
    report_path.write_text(json.dumps(dict(source_run=IDENTITY["run_id"],
        reviewed_sha=IDENTITY["reviewed_sha"], prior_main_sha="d" * 40,
        outcome="INTEGRATION_REQUIRED", detail="historical intermediate classification")), encoding="utf-8")
    source = brain.capture_publication(report_path, source_path, IDENTITY["run_id"], IDENTITY["decision_sha"])
    assert source["observations"] == []
    assert brain.publication_event(source_path, 100, 2, 200, brain.digest(source)) == ""
    assert brain.collect(100, 2, artifacts=publication_artifacts(source)) == []


@pytest.mark.parametrize("cause,wake", [("RESERVATION_CONTENDED", False),
                                      ("RESERVATION_STALE", True),
                                      ("RESERVATION_INVALID", True),
                                      ("RESERVATION_CAS_FAILED", True),
                                      ("WRITER_SCOPE_GAP", True)])
def test_reservation_attention_wakes_only_for_actionable_boundary_failure(monkeypatch, tmp_path, cause, wake):
    sources = Sources("PASS")
    monkeypatch.setattr(brain, "GitSources", lambda *args: sources)
    report_path, source_path = tmp_path / "report.json", tmp_path / "source.json"
    report_path.write_text(json.dumps(dict(source_run=IDENTITY["run_id"],
        reviewed_sha=IDENTITY["reviewed_sha"], prior_main_sha="d" * 40,
        outcome="FAILED", cause=cause, detail="bounded coordination failure",
        recovery=None, blocker=None)), encoding="utf-8")
    source = brain.capture_publication(report_path, source_path, IDENTITY["run_id"], IDENTITY["decision_sha"])
    event = brain.publication_event(source_path, 100, 2, 200, brain.digest(source))
    if wake:
        assert source["observations"][0]["publication_attempt"]["cause"] == cause
        assert brain.parse_event_id(event).family == brain.PUBLICATION_FAILURE
    else:
        assert source["observations"] == []
        assert event == ""


def test_typed_ingress_contention_does_not_wake_brain_to_invent_publication_procedure(monkeypatch, tmp_path):
    from aios_renew.authoring_ingress import AuthoringIngressError
    from aios_renew.publication import _failed
    source_path = tmp_path / "source.json"
    monkeypatch.setenv("AIOS_ATTENTION_SOURCE_PATH", str(source_path))
    try:
        try:
            raise _failed(IDENTITY["run_id"], "exact boundary occupied", cause="RESERVATION_CONTENDED")
        except Exception as cause:
            raise AuthoringIngressError("bounded ingress contention") from cause
    except AuthoringIngressError:
        brain.record_carrier_rejection(tmp_path / "unused-event.json", "INGRESS")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    assert source["observations"] == []
    assert brain.publication_event(source_path, 100, 2, 200, brain.digest(source)) == ""


def recovery_observation(cause="MERGE_CONFLICT"):
    attempt = dict(IDENTITY, sampled_main_sha="d" * 40, outcome="RECOVERY_BLOCKED",
                   cause=cause, recovery=None, blocker=None)
    return dict(boundary="CANONICAL_CONFLICT", prepared_sha=IDENTITY["reviewed_sha"],
                prepared_digest=brain.digest(attempt), predecessor_ref="refs/heads/main",
                predecessor_sha="d" * 40, observed_sha="d" * 40, publication_attempt=attempt)


@pytest.mark.parametrize("cause", ["MERGE_CONFLICT", "AMBIGUOUS_ANCESTRY", "RECOVERY_SCOPE_ESCAPE",
                                   "MERGE_CALCULATION_FAILED"])
def test_typed_recovery_reuses_existing_conflict_family_and_digest_binds_exact_identities(cause):
    observation = recovery_observation(cause)
    item, source, pointer = source_event(observation)
    assert item.family == brain.CONFLICT
    attempt = source["observations"][0]["publication_attempt"]
    assert {name: attempt[name] for name in IDENTITY} == IDENTITY
    assert attempt["sampled_main_sha"] == item.selectors["predecessor_sha"]
    assert item.selectors["prepared_digest"] == brain.digest(attempt)
    assert item.selectors["source_digest"] == brain.digest(source)
    assert "detail" not in json.dumps(source)
    changed = recovery_observation("MERGE_CONFLICT" if cause != "MERGE_CONFLICT" else "RECOVERY_SCOPE_ESCAPE")
    assert source_event(changed)[0].event_id != item.event_id
    assert brain.project_source(source, pointer) == [item]


@pytest.mark.parametrize("defect", ["unknown_cause", "candidate_authority", "semantic_choice", "wrong_main",
                                   "wrong_reviewed", "wrong_digest", "wrong_boundary", "extra_identity"])
def test_publication_context_cannot_upgrade_authority_or_escape_its_exact_binding(defect):
    observation = recovery_observation()
    attempt = observation["publication_attempt"]
    if defect == "unknown_cause":
        attempt["cause"] = "CHOOSE_A_GIT_STRATEGY"
    elif defect == "candidate_authority":
        attempt.update(outcome="RECOVERY_REVIEW_REQUIRED", cause="FRESH_EXACT_REVIEW_REQUIRED",
                       recovery=dict(merge_base_sha="e" * 40, tree_sha="f" * 40, publication_eligible=True))
    elif defect == "semantic_choice":
        attempt["executor"] = "codex"
    elif defect == "wrong_main":
        observation["predecessor_sha"] = "e" * 40
    elif defect == "wrong_reviewed":
        observation["prepared_sha"] = "e" * 40
    elif defect == "wrong_digest":
        observation["prepared_digest"] = "0" * 64
    elif defect == "wrong_boundary":
        observation["boundary"] = "PUBLICATION_PROVEN"
    else:
        attempt["candidate_sha"] = "e" * 40
    with pytest.raises(brain.AttentionError):
        source_event(observation)


def test_recovery_capture_rejects_main_movement_before_attention(monkeypatch, tmp_path):
    sources = Sources("PASS")
    monkeypatch.setattr(brain, "GitSources", lambda *args: sources)
    report_path, source_path = tmp_path / "report.json", tmp_path / "source.json"
    report_path.write_text(json.dumps(dict(source_run=IDENTITY["run_id"],
        reviewed_sha=IDENTITY["reviewed_sha"], prior_main_sha="e" * 40,
        outcome="RECOVERY_BLOCKED", cause="MERGE_CONFLICT", detail="private", recovery=None, blocker=None)),
        encoding="utf-8")
    with pytest.raises(brain.AttentionError, match="STALE_PUBLICATION_RECOVERY"):
        brain.capture_publication(report_path, source_path, IDENTITY["run_id"], IDENTITY["decision_sha"])
    assert not source_path.exists()


@pytest.mark.parametrize("field,value", [("artifact_sha", "e" * 40), ("reviewed_sha", "e" * 40),
                                        ("decision_sha", "e" * 40)])
def test_recovery_freshness_reproves_exact_review_and_artifact_identity(field, value):
    item, source, _ = source_event(recovery_observation())
    sources = Sources("PASS")
    assert brain.freshness(item, sources, Artifacts(source)) == "UNRESOLVED"
    sources.identity = dict(IDENTITY, **{field: value})
    assert brain.freshness(item, sources, Artifacts(source)) == "UNKNOWN"


def test_recovery_successor_resolution_requires_inclusion_of_the_original_reviewed_source():
    item, source, _ = source_event(recovery_observation())
    sources = Sources("PASS")
    sources.is_included = True
    assert brain.freshness(item, sources, Artifacts(source)) == "RESOLVED"
    sources.is_included = False
    sources.values["refs/heads/main"] = "e" * 40
    assert brain.freshness(item, sources, Artifacts(source)) == "UNKNOWN"


@pytest.mark.parametrize("case", ["digest", "run", "attempt", "artifact", "repository", "missing",
    "size", "duplicate_key", "schema", "selector", "semantic_field", "multiple"])
def test_direct_publication_rejects_unproven_or_malformed_source_without_output(tmp_path, capsys, case):
    source = brain.source_document([dict(boundary="PUBLICATION_PROVEN", **IDENTITY,
                                        published_sha=IDENTITY["reviewed_sha"])])
    path, output = tmp_path / "source.json", tmp_path / "direct.out"
    if case == "schema":
        source["version"] = 2
    elif case == "selector":
        source["observations"][0]["published_sha"] = "f" * 40
    elif case == "semantic_field":
        source["observations"][0]["next_action"] = "private strategy"
    elif case == "multiple":
        source["observations"].append(dict(boundary="PUBLICATION_FAILED", **IDENTITY, stage="EXECUTION"))
    raw = brain.canonical(source)
    if case == "size":
        raw = b" " * (brain.MAX_SOURCE_BYTES + 1)
    elif case == "duplicate_key":
        raw = raw[:-1] + b',"version":1}'
    if case != "missing":
        path.write_bytes(raw)
    args = ["publication-event", "--source", str(path), "--workflow-run-id", "0" if case == "run" else "100",
        "--attempt", "0" if case == "attempt" else "2", "--artifact-id", "0" if case == "artifact" else "200",
        "--source-digest", "0" * 64 if case == "digest" else brain.digest(source),
        "--repository", "other/repository" if case == "repository" else brain.REPOSITORY, "--output", str(output)]
    assert brain.main(args) == 1
    assert not output.exists()
    assert capsys.readouterr().err == "attention source rejected: SOURCE_UNPROVEN\n"


@pytest.mark.parametrize("case", ["not_included", "no_main", "wrong_run", "wrong_candidate",
                                 "unknown_outcome", "missing_report", "unproven_review"])
def test_publication_capture_cannot_export_identity_without_exact_canonical_proof(monkeypatch, tmp_path, case):
    sources = Sources("PASS")
    sources.is_included = case != "not_included"
    if case == "no_main":
        sources.values.clear()
    elif case == "unproven_review":
        sources.identity = dict(IDENTITY, decision_sha="f" * 40)
    monkeypatch.setattr(brain, "GitSources", lambda *args: sources)
    report = dict(source_run="RUN-wrong-001" if case == "wrong_run" else IDENTITY["run_id"],
        reviewed_sha="f" * 40 if case == "wrong_candidate" else IDENTITY["reviewed_sha"],
        prior_main_sha="e" * 40, outcome="UNKNOWN" if case == "unknown_outcome" else "PUBLISHED", detail="private")
    report_path, source_path, output = [tmp_path / name for name in ("report.json", "source.json", "capture.out")]
    if case != "missing_report":
        report_path.write_text(json.dumps(report), encoding="utf-8")
    assert brain.main(["publication", "--report", str(report_path), "--source", str(source_path),
        "--run-id", IDENTITY["run_id"], "--decision-sha", IDENTITY["decision_sha"], "--output", str(output)]) == 1
    assert not source_path.exists() and not output.exists()


@pytest.mark.parametrize("ambiguous", [False, True])
def test_direct_and_fan_in_publication_share_durable_dedupe_and_no_resend(tmp_path, ambiguous):
    source = brain.source_document([dict(boundary="PUBLICATION_PROVEN", **IDENTITY,
                                        published_sha=IDENTITY["reviewed_sha"])])
    path = tmp_path / "source.json"
    path.write_bytes(brain.canonical(source))
    direct = brain.publication_event(path, 100, 2, 200, brain.digest(source))
    collected = brain.collect(100, 2, artifacts=publication_artifacts(source))[0]
    binding = wake.Binding("https://chatgpt.com/c/11111111-1111-1111-1111-111111111111",
                           "http://127.0.0.1:9222", tmp_path / "lane.json", 1)
    clicks = []

    class Adapter:
        def __init__(self, binding):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def check(self, text):
            pass

        def submit(self, text, before_insert, before_click):
            before_insert()
            before_click()
            clicks.append(text)

        def prove(self, text):
            if ambiguous:
                raise wake.WakeBlocked("SUBMISSION_UNPROVEN")

    projection = SimpleNamespace(observe=lambda event_id: "UNRESOLVED")
    first = wake.deliver(direct, brain.REPOSITORY, binding, Adapter, projection)
    second = wake.deliver(collected, brain.REPOSITORY, binding, Adapter, projection)
    assert first["status"] == ("BLOCKED" if ambiguous else "SUBMITTED")
    assert second["status"] == ("BLOCKED" if ambiguous else "NOOP")
    assert clicks == [wake.doorbell(direct, brain.REPOSITORY)]
    state = wake.read_json(binding.state_path)
    assert list(state["events"]) == [direct]
    assert state["events"][direct]["status"] == ("AMBIGUOUS" if ambiguous else "SUBMITTED")


@pytest.mark.parametrize("family", [brain.PUBLICATION_FAILURE, brain.CONFLICT])
def test_direct_pointer_outcome_unknown_reaches_browser_guard_and_completed_source_rechecks(tmp_path, family):
    observation = (dict(boundary="PUBLICATION_FAILED", **IDENTITY, stage="EXECUTION")
        if family == brain.PUBLICATION_FAILURE else dict(boundary="CANONICAL_CONFLICT",
            prepared_sha=IDENTITY["reviewed_sha"], prepared_digest=brain.digest(IDENTITY),
            predecessor_ref="refs/heads/main", predecessor_sha="e" * 40, observed_sha="d" * 40))
    path = tmp_path / "source.json"
    source = brain.write_source(path, [observation])
    event_id = brain.publication_event(path, 100, 2, 200, brain.digest(source))
    reader = publication_artifacts(source)
    request, completed = reader.request, [False]

    def current_run_request(api_path, binary=False):
        value = request(api_path, binary)
        if api_path == "/actions/runs/100/attempts/2" and not completed[0]:
            value["status"] = "in_progress"
        return value

    reader.request = current_run_request
    sources = Sources("PASS")

    def observe(identity):
        try:
            return brain.freshness(brain.parse_event_id(identity), sources, reader)
        except brain.AttentionError:
            return "UNKNOWN"

    checked = []

    class DraftAdapter:
        def __init__(self, binding):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def check(self, text):
            checked.append(text)
            raise wake.WakeBlocked("DRAFT_PRESENT")

    binding = wake.Binding("https://chatgpt.com/c/11111111-1111-1111-1111-111111111111",
                           "http://127.0.0.1:9222", tmp_path / "lane.json", 1)
    from aios_renew.return_affinity import LEGACY
    projection = SimpleNamespace(observe=observe, affinity=lambda _: LEGACY)
    first = wake.deliver(event_id, brain.REPOSITORY, binding, DraftAdapter, projection)
    assert observe(event_id) == "UNKNOWN"
    assert first["status"] == "DEFERRED" and first["reason"] == "DRAFT_PRESENT"
    assert checked == [wake.doorbell(event_id, brain.REPOSITORY)]
    completed[0] = True
    assert brain.collect(100, 2, artifacts=reader) == [event_id]
    receipts = wake.operate(brain.REPOSITORY, binding, adapter_factory=DraftAdapter, projection=projection)
    assert receipts == [dict(event_id=event_id, status="DEFERRED", reason="DRAFT_PRESENT")]
    assert checked == [wake.doorbell(event_id, brain.REPOSITORY)] * 2
    assert list(wake.read_json(binding.state_path)["events"]) == [event_id]


def test_review_followup_requires_all_exact_finding_successors_and_pass_never_becomes_correction():
    item = source_event(dict(boundary="REVIEW_FOLLOWUP", **IDENTITY))[0]
    sources = Sources()
    assert brain.freshness(item, sources, Artifacts()) == "UNRESOLVED"
    for number in (1, 2):
        sha = str(number) * 40
        subject = IDENTITY["run_id"] + f"-F{number}"
        sources.values["refs/heads/aios/remediation/" + subject] = sha
        sources.docs[sha, f".ai/remediations/REMEDIATION-{subject}.yaml"] = dict(
            reviewed_sha=IDENTITY["reviewed_sha"], finding_id=f"F{number}",
            action="CODE_FIX" if number == 1 else "EVIDENCE_ONLY")
        assert brain.freshness(item, sources, Artifacts()) == ("UNRESOLVED" if number == 1 else "RESOLVED")
    sources.decision["verdict"] = "PASS"
    assert brain.freshness(item, sources, Artifacts()) == "UNKNOWN"
    sources.decision["verdict"] = "BLOCKED"
    sources.values = {"refs/heads/main": "d" * 40}
    assert brain.freshness(item, sources, Artifacts()) == "UNRESOLVED"
    sources.identity["artifact_sha"] = "f" * 40
    assert brain.freshness(item, sources, Artifacts()) == "UNKNOWN"


def test_publication_success_is_planning_only_and_exact_canonical_bookmark_resolves_it():
    item = source_event(dict(boundary="PUBLICATION_PROVEN", **IDENTITY, published_sha=IDENTITY["reviewed_sha"]))[0]
    sources = Sources("PASS")
    assert brain.freshness(item, sources, Artifacts()) == "UNKNOWN"
    sources.is_included = True
    assert brain.freshness(item, sources, Artifacts()) == "UNRESOLVED"
    completed = dict(run_id=IDENTITY["run_id"], artifacts_sha=IDENTITY["artifact_sha"], review_id="REVIEW-fixture-001",
                     review_decision_sha=IDENTITY["decision_sha"], reviewed_sha=IDENTITY["reviewed_sha"], published_sha=IDENTITY["reviewed_sha"])
    sources.docs["d" * 40, ".ai/roadmap-state.yaml"] = {"version": 1, "sequence": [dict(status="DONE", completed_by=completed)]}
    assert brain.freshness(item, sources, Artifacts()) == "RESOLVED"
    completed["review_decision_sha"] = "e" * 40
    assert brain.freshness(item, sources, Artifacts()) == "UNKNOWN"
    assert "verdict" not in item.render() and "next_action" not in item.render()


def test_publication_failure_binds_attempt_and_review_and_resolves_only_by_canonical_inclusion():
    item, source, _ = source_event(dict(boundary="PUBLICATION_FAILED", **IDENTITY, stage="DISPATCH"))
    artifacts = Artifacts(source)
    sources = Sources("PASS")
    assert brain.freshness(item, sources, artifacts) == "UNRESOLVED"
    sources.is_included = True
    assert brain.freshness(item, sources, artifacts) == "RESOLVED"
    artifacts.source = brain.source_document([])
    with pytest.raises(brain.AttentionError):
        brain.freshness(item, sources, artifacts)


def test_rejected_source_closed_issue_is_not_canonical_resolution_and_body_substitution_holds():
    item, source, _ = source_event(rejection())
    artifacts = Artifacts(source)
    assert brain.freshness(item, Sources(), artifacts) == "UNRESOLVED"
    artifacts.body = "changed source"
    assert brain.freshness(item, Sources(), artifacts) == "UNKNOWN"
    artifacts.source = {"format": "UNKNOWN"}
    with pytest.raises(brain.AttentionError):
        brain.freshness(item, Sources(), artifacts)


def test_conflict_exact_predecessor_is_unresolved_third_identity_is_unknown_no_replacement_choice():
    observation = dict(boundary="CANONICAL_CONFLICT", prepared_sha="c" * 40, prepared_digest="e" * 64,
                       predecessor_ref="refs/heads/main", predecessor_sha="d" * 40, observed_sha="f" * 40)
    item, source, _ = source_event(observation)
    sources = Sources()
    sources.values["refs/heads/main"] = "f" * 40
    assert brain.freshness(item, sources, Artifacts(source)) == "UNRESOLVED"
    sources.values["refs/heads/main"] = "e" * 40
    assert brain.freshness(item, sources, Artifacts(source)) == "UNKNOWN"
    sources.values.clear()
    assert brain.freshness(item, sources, Artifacts(source)) == "UNKNOWN"


def test_dispatch_rejection_resolution_requires_exact_attributed_canonical_run_not_workflow_success():
    item, source, _ = source_event(delivery())
    artifacts, sources = Artifacts(source), Sources()
    assert brain.freshness(item, sources, artifacts) == "UNRESOLVED"
    receipt = dict(selectors=dict(task_id="TASK-fixture", task_revision=1))
    artifacts.successor = ("RUN-successor-001", receipt)
    root = "refs/heads/aios/"
    sources.values.update({root + "artifacts/RUN-successor-001": "e" * 40, root + "review/RUN-successor-001": "f" * 40})
    sources.docs["e" * 40, ".ai/transport/run.json"] = dict(run_id="RUN-successor-001", task=dict(id="TASK-fixture", revision=1), head_sha=None)
    sources.docs["e" * 40, ".ai/transport/result.json"] = dict(result=dict(head_sha="f" * 40))
    assert brain.freshness(item, sources, artifacts) == "RESOLVED"
    sources.docs["e" * 40, ".ai/transport/run.json"]["task"]["id"] = "TASK-substituted"
    assert brain.freshness(item, sources, artifacts) == "UNKNOWN"


def test_recursive_recovery_cannot_construct_a_second_semantic_subject():
    original = examples()[0]
    recovery = brain.attention(brain.RECOVERY, dict(original_event_id=original.event_id))
    assert brain.parse_event_id(recovery.event_id).selectors["original_event_id"] == original.event_id
    with pytest.raises(brain.AttentionError):
        brain.attention(brain.RECOVERY, dict(original_event_id=recovery.event_id))


def test_artifact_reader_rejects_duplicate_zip_members_and_substituted_workflow_attempt():
    source = brain.source_document([delivery()])
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("source.json", brain.canonical(source))
        zipped.writestr("extra.json", "{}")
    reader = brain.ArtifactSources(brain.REPOSITORY)
    reader.request = lambda path, binary=False: (archive.getvalue() if binary else
        dict(id=200, expired=False, name="aios-attention-source-v1-primary-attempt-1", size_in_bytes=100,
             workflow_run=dict(id=100)))
    reader.run = lambda run_id, attempt: dict(id=run_id)
    with pytest.raises(brain.AttentionError):
        reader.artifact(dict(workflow_run_id=100, run_attempt=1, artifact_id=200, source_digest=brain.digest(source)))
    real = brain.ArtifactSources(brain.REPOSITORY)
    real.request = lambda path: dict(id=100, run_attempt=2, path=".github/workflows/aios-self-hosted-wakeup.yml",
                                     status="completed", event="workflow_dispatch", head_branch="main",
                                     head_repository=dict(full_name=brain.REPOSITORY))
    with pytest.raises(brain.AttentionError):
        real.run(100, 1)


def test_review_ingress_hook_pass_and_nonpass_read_exact_canonical_decision(monkeypatch, tmp_path):
    target = tmp_path / "source.json"
    monkeypatch.setenv("AIOS_ATTENTION_SOURCE_PATH", str(target))
    verdict = ["CHANGES_REQUIRED"]
    monkeypatch.setattr(brain.GitSources, "review", lambda *args: (IDENTITY, {"verdict": verdict[0]}))
    delivery_result = SimpleNamespace(ingress_result=SimpleNamespace(operation="SUBMIT_REVIEW", status="CANONICALIZED",
        canonical_destination="refs/heads/aios/review-decision/RUN-fixture-001", canonical_sha=IDENTITY["decision_sha"]))
    brain.record_ingress_success(delivery_result, tmp_path)
    source = brain.load_json(target.read_bytes())
    pointer = dict(workflow_run_id=1, run_attempt=1, artifact_id=2, source_digest=brain.digest(source))
    assert brain.project_source(source, pointer)[0].family == brain.REVIEW
    verdict[0] = "PASS"
    brain.record_ingress_success(delivery_result, tmp_path)
    assert brain.load_json(target.read_bytes())["observations"] == []


def test_canonical_review_reader_binds_run_result_candidate_and_decision_and_rechecks_refs(tmp_path):
    root = "refs/heads/aios/"
    refs = {root + name + "/RUN-fixture-001": sha for name, sha in
            (("artifacts", "a" * 40), ("review", "c" * 40), ("review-decision", "b" * 40))}
    run = dict(run_id="RUN-fixture-001", task=dict(id="TASK-fixture", revision=1), head_sha=None)
    review = dict(review_id="REVIEW-fixture-001", reviewed_sha="c" * 40, mode="PRIMARY",
                  verdict="BLOCKED", acceptance={}, findings=[])
    blobs = {("a" * 40, ".ai/transport/run.json"): run,
             ("a" * 40, ".ai/transport/result.json"): dict(result=dict(head_sha="c" * 40)),
             ("b" * 40, ".ai/reviews/REVIEW-fixture-001.yaml"): review}
    def git(path, *args):
        if args[0] == "ls-remote":
            return "\n".join(sha + "\t" + ref for ref, sha in refs.items() if any(fnmatchcase(ref, pattern) for pattern in args[3:]))
        if args[0] == "show":
            sha, name = args[1].split(":", 1)
            return json.dumps(blobs[sha, name])
        if args[0] == "ls-tree":
            name = ".ai/reviews/REVIEW-fixture-001.yaml"
            return name if "--name-only" in args else "100644 blob " + "e" * 40 + "\t" + name
        if args[0] == "rev-list":
            return "b" * 40 + " " + "c" * 40
        if args[0] == "diff-tree":
            return "A\t.ai/reviews/REVIEW-fixture-001.yaml"
        assert args[0] == "fetch"
        return ""
    sources = brain.GitSources(tmp_path, git, "fixture")
    assert sources.review("RUN-fixture-001", "b" * 40)[0] == IDENTITY
    run["run_id"] = "RUN-substituted-001"
    with pytest.raises(brain.AttentionError):
        sources.review("RUN-fixture-001", "b" * 40)
    run["run_id"] = "RUN-fixture-001"
    review["reviewed_sha"] = "f" * 40
    with pytest.raises(brain.AttentionError):
        sources.review("RUN-fixture-001", "b" * 40)
    review["reviewed_sha"] = "c" * 40
    refs[root + "failure-artifacts/RUN-fixture-001"] = "e" * 40
    with pytest.raises(brain.AttentionError):
        sources.review("RUN-fixture-001", "b" * 40)


@pytest.fixture
def independent_review_repo(tmp_path):
    """Local Git objects/refs model the naming shape, never historical delivery."""
    origin, reader = tmp_path / "origin", tmp_path / "reader"
    origin.mkdir()
    reader.mkdir()

    def git(*args, repo=origin, data=None):
        return subprocess.run(["git", "-C", str(repo), "-c", "core.autocrlf=false",
            "-c", "user.name=Synthetic fixture", "-c", "user.email=fixture@example.invalid", *args],
            input=data, text=True, encoding="utf-8", capture_output=True, check=True, timeout=15).stdout.strip()

    git("init", "--quiet")
    git("init", "--quiet", repo=reader)
    git("remote", "add", "origin", str(origin), repo=reader)

    def commit(files, parents=()):
        git("read-tree", parents[0] if parents else "--empty")
        for name, value in files.items():
            mode, raw = value if isinstance(value, tuple) else ("100644", value)
            blob = git("hash-object", "-w", "--stdin", data=raw if isinstance(raw, str) else json.dumps(raw))
            git("update-index", "--add", "--cacheinfo", f"{mode},{blob},{name}")
        tree = git("write-tree")
        return git("commit-tree", tree, *[arg for parent in parents for arg in ("-p", parent)], data="Synthetic fixture\n")

    run_id, name = "RUN-361-003", ".ai/reviews/REVIEW-361-001.yaml"
    base = commit({"src/sample.py": "before\n"})
    head = commit({"src/sample.py": "after\n"}, (base,))
    run = dict(run_id=run_id, task=dict(id="TASK-361", revision=1), head_sha=None)
    result = dict(result=dict(head_sha=head))
    artifact = commit({".ai/transport/run.json": run, ".ai/transport/result.json": result}, (head,))
    review = dict(review_id="REVIEW-361-001", reviewed_sha=head, mode="PRIMARY",
                  verdict="PASS", acceptance={"AC1": "PASS"}, findings=[])
    decision = commit({name: review}, (head,))
    identity = dict(run_id=run_id, artifact_sha=artifact, decision_sha=decision, reviewed_sha=head)

    def set_ref(namespace, sha):
        git("update-ref", "refs/heads/aios/" + namespace + "/" + run_id, sha)

    for namespace, sha in (("artifacts", artifact), ("review", head), ("review-decision", decision)):
        set_ref(namespace, sha)
    git("update-ref", "refs/heads/main", head)
    report = dict(source_run=run_id, reviewed_sha=head, prior_main_sha=base,
                  outcome="PUBLISHED", detail="private synthetic diagnostic",
                  lineage=dict(decision_sha=decision, artifacts_sha=artifact, reservation_sha=None))
    report_path, source_path = tmp_path / "report.json", tmp_path / "source.json"

    def capture():
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return brain.capture_publication(report_path, source_path, run_id, identity["decision_sha"], repo=reader)

    return SimpleNamespace(repo=reader, origin=origin, git=git, commit=commit, set_ref=set_ref,
        identity=identity, base=base, review=review, name=name, run=run, result=result,
        report=report, capture=capture, source_path=source_path)


@pytest.mark.parametrize("review_id,extension", [
    ("REVIEW-361-001", "yaml"), ("REVIEW-361-001", "yml"),
    ("REVIEW-361-003", "yaml"), ("REVIEW-independent.9_test-027", "yaml"),
])
def test_independent_review_id_resolves_exact_canonical_git_identity(independent_review_repo, review_id, extension):
    fixture = independent_review_repo
    review = dict(fixture.review, review_id=review_id)
    name = f".ai/reviews/{review_id}.{extension}"
    decision = fixture.commit({name: review}, (fixture.identity["reviewed_sha"],))
    fixture.set_ref("review-decision", decision)
    fixture.identity["decision_sha"] = decision
    sources = brain.GitSources(fixture.repo, brain._git, "origin")
    assert sources.review(fixture.identity["run_id"], decision) == (fixture.identity, review)


@pytest.mark.parametrize("outcome", ["PUBLISHED", "ALREADY_PUBLISHED", "ALREADY_INCLUDED"])
def test_independent_review_id_publication_uses_inclusion_and_bounded_selectors(independent_review_repo, outcome):
    fixture = independent_review_repo
    if outcome == "ALREADY_INCLUDED":
        main = fixture.commit({"docs/later.md": "Later source\n"}, (fixture.identity["reviewed_sha"],))
        fixture.git("update-ref", "refs/heads/main", main)
    fixture.report["outcome"] = outcome
    before = fixture.git("for-each-ref", "--format=%(refname) %(objectname)")
    source = fixture.capture()
    assert source["observations"] == [dict(boundary="PUBLICATION_PROVEN", **fixture.identity,
                                           published_sha=fixture.identity["reviewed_sha"])]
    assert brain.load_json(fixture.source_path.read_bytes()) == source
    item, _, _ = source_event(source["observations"][0])
    assert item.family == brain.PUBLICATION_SUCCESS
    assert set(item.selectors) == set(fixture.identity) | {"published_sha", "source_boundary"}
    assert "private" not in item.render() and "REVIEW-361-001" not in item.render()
    assert fixture.git("for-each-ref", "--format=%(refname) %(objectname)") == before


def test_publication_attention_carries_fresh_read_only_next_without_changing_wake_identity(
        independent_review_repo, monkeypatch):
    from aios_renew import brain_sync
    fixture = independent_review_repo
    planning = fixture.repo / ".ai/roadmap-state.yaml"
    planning.parent.mkdir(parents=True, exist_ok=True)
    planning.write_text("version: 1\nsequence:\n  - {status: NEXT, task_id: TASK-361, task_revision: 1}\n",
                        encoding="utf-8")
    main = fixture.identity["reviewed_sha"]
    monkeypatch.setattr(brain_sync, "observe_brain_sync", lambda repo: SimpleNamespace(
        main_sha=main, selection_status="SELECTED", selected_task=dict(id="TASK-415", revision=1)))
    fixture.report["outcome"] = "PUBLISHED"
    fixture.capture()
    plain = brain.load_json(fixture.source_path.read_bytes(), brain.MAX_SOURCE_BYTES)
    annotated = brain.capture_publication(
        fixture.source_path.parent / "report.json", fixture.source_path,
        fixture.identity["run_id"], fixture.identity["decision_sha"],
        repo=fixture.repo, observe_next=True)
    assert annotated["observations"][0]["effective_next"] == dict(
        main_sha=main, selection_status="SELECTED", task_id="TASK-415", task_revision=1)
    pointer = dict(workflow_run_id=100, run_attempt=1, artifact_id=200, source_digest=brain.digest(annotated))
    earlier = dict(pointer, source_digest=brain.digest(plain))
    event = brain.project_source(annotated, pointer)[0]
    assert event.event_id == brain.project_source(plain, earlier)[0].event_id
    assert "effective_next" not in event.selectors
    assert "fresh_brain_sync_required: true" in wake.doorbell(event.event_id, wake.REPOSITORY)
    monkeypatch.setattr(brain_sync, "observe_brain_sync", lambda repo: SimpleNamespace(
        main_sha=main, selection_status="HUMAN_BRAIN_NEXT_REQUIRED", selected_task=None))
    awaiting_human = brain.capture_publication(
        fixture.source_path.parent / "report.json", fixture.source_path,
        fixture.identity["run_id"], fixture.identity["decision_sha"],
        repo=fixture.repo, observe_next=True)
    assert awaiting_human["observations"][0]["effective_next"] == dict(
        main_sha=main, selection_status="HUMAN_BRAIN_NEXT_REQUIRED", task_id=None, task_revision=None)
    planning.write_text("version: 1\nsequence:\n  - {status: NEXT, task_id: TASK-413, task_revision: 2}\n",
                        encoding="utf-8")
    unrelated = brain.capture_publication(
        fixture.source_path.parent / "report.json", fixture.source_path,
        fixture.identity["run_id"], fixture.identity["decision_sha"],
        repo=fixture.repo, observe_next=True)
    assert "effective_next" not in unrelated["observations"][0]


@pytest.mark.parametrize("verdict", ["PASS", "CHANGES_REQUIRED", "BLOCKED"])
def test_independent_review_id_ingress_uses_same_canonical_reader(independent_review_repo, monkeypatch, verdict):
    fixture = independent_review_repo
    review = dict(fixture.review, verdict=verdict)
    if verdict == "CHANGES_REQUIRED":
        review.update(acceptance={"AC1": "FAIL"}, findings=[dict(id="F1", basis="AC1", action="CODE_FIX",
            location="src/sample.py", issue="Synthetic finding", expected="Synthetic correction")])
    decision = fixture.commit({fixture.name: review}, (fixture.identity["reviewed_sha"],))
    fixture.set_ref("review-decision", decision)
    fixture.identity["decision_sha"] = decision
    monkeypatch.setenv("AIOS_ATTENTION_SOURCE_PATH", str(fixture.source_path))
    delivery_result = SimpleNamespace(ingress_result=SimpleNamespace(operation="SUBMIT_REVIEW", status="CANONICALIZED",
        canonical_destination="refs/heads/aios/review-decision/" + fixture.identity["run_id"], canonical_sha=decision))
    before = fixture.git("for-each-ref", "--format=%(refname) %(objectname)")
    brain.record_ingress_success(delivery_result, fixture.repo)
    source = brain.load_json(fixture.source_path.read_bytes())
    assert source["observations"] == ([] if verdict == "PASS" else [dict(boundary="REVIEW_FOLLOWUP", **fixture.identity)])
    if verdict != "PASS":
        assert source_event(source["observations"][0])[0].family == brain.REVIEW
    assert fixture.git("for-each-ref", "--format=%(refname) %(objectname)") == before


@pytest.mark.parametrize("fault", [
    "missing_review", "extra_review", "ambiguous_extension", "nested_path", "wrong_extension", "wrong_prefix", "id_mismatch", "unsafe_id",
    "wrong_reviewed_sha", "malformed", "duplicate_key", "forged_pass", "invalid_verdict",
    "bad_parent", "merge_parent", "unrelated_delta", "executable_mode", "symlink", "replaced_review",
])
def test_independent_review_id_rejects_unbound_metadata(independent_review_repo, fault):
    fixture = independent_review_repo
    review, name = dict(fixture.review), fixture.name
    parents, extra, mode = (fixture.identity["reviewed_sha"],), {}, "100644"
    if fault == "nested_path":
        name = ".ai/reviews/nested/REVIEW-361-001.yaml"
    elif fault == "wrong_extension":
        name = ".ai/reviews/REVIEW-361-001.json"
    elif fault == "wrong_prefix":
        name = ".ai/reviews/RUN-361-003.yaml"
    elif fault == "id_mismatch":
        review["review_id"] = "REVIEW-361-003"
    elif fault == "unsafe_id":
        review["review_id"] = "REVIEW-../361-001"
    elif fault == "wrong_reviewed_sha":
        review["reviewed_sha"] = fixture.base
    elif fault == "malformed":
        review = "review_id: [unterminated"
    elif fault == "duplicate_key":
        review = json.dumps(review)[:-1] + ',"review_id":"REVIEW-361-003"}'
    elif fault == "forged_pass":
        review["findings"] = [dict(id="F1", basis="AC1", action="CODE_FIX", location="src/sample.py",
                                  issue="Unresolved finding", expected="Correction")]
    elif fault == "invalid_verdict":
        review["verdict"] = "APPROVED"
    elif fault == "extra_review":
        extra[".ai/reviews/REVIEW-361-003.yaml"] = dict(review, review_id="REVIEW-361-003")
    elif fault == "ambiguous_extension":
        extra[".ai/reviews/REVIEW-361-001.yml"] = review
    elif fault == "bad_parent":
        parents = (fixture.base,)
    elif fault == "merge_parent":
        parents += (fixture.base,)
    elif fault == "unrelated_delta":
        extra["src/sample.py"] = "Substituted source\n"
    elif fault == "executable_mode":
        mode = "100755"
    elif fault == "symlink":
        mode = "120000"
    elif fault == "replaced_review":
        head = fixture.commit({name: "Prior review metadata\n"}, parents)
        fixture.identity["reviewed_sha"] = review["reviewed_sha"] = head
        fixture.set_ref("review", head)
        fixture.result["result"]["head_sha"] = head
        artifact = fixture.commit({".ai/transport/run.json": fixture.run,
                                   ".ai/transport/result.json": fixture.result}, (head,))
        fixture.identity["artifact_sha"] = artifact
        fixture.set_ref("artifacts", artifact)
        parents = (head,)
    files = {} if fault == "missing_review" else {name: (mode, review), **extra}
    decision = fixture.commit(files, parents)
    fixture.identity["decision_sha"] = decision
    fixture.set_ref("review-decision", decision)
    fixture.report.update(reviewed_sha=fixture.identity["reviewed_sha"], lineage=dict(decision_sha=decision,
                          artifacts_sha=fixture.identity["artifact_sha"], reservation_sha=None))
    with pytest.raises(brain.AttentionError, match="^UNPROVEN_REVIEW_IDENTITY$"):
        brain.GitSources(fixture.repo, brain._git, "origin").review(fixture.identity["run_id"], decision)
    with pytest.raises(brain.AttentionError, match="^UNPROVEN_REVIEW_IDENTITY$"):
        fixture.capture()
    assert not fixture.source_path.exists()


@pytest.mark.parametrize("fault", ["wrong_run", "wrong_result", "wrong_candidate", "wrong_decision",
                                  "missing_artifact", "failure", "failure-artifacts", "not_included", "wrong_lineage"])
def test_independent_review_id_publication_rejects_substitution_and_missing_proof(independent_review_repo, fault):
    fixture = independent_review_repo
    if fault in {"wrong_run", "wrong_result"}:
        if fault == "wrong_run":
            fixture.run["run_id"] = "RUN-361-001"
        else:
            fixture.result["result"]["head_sha"] = fixture.base
        artifact = fixture.commit({".ai/transport/run.json": fixture.run,
                                   ".ai/transport/result.json": fixture.result}, (fixture.identity["reviewed_sha"],))
        fixture.set_ref("artifacts", artifact)
    elif fault == "wrong_candidate":
        fixture.set_ref("review", fixture.base)
    elif fault == "wrong_decision":
        replacement = fixture.commit({fixture.name: dict(fixture.review, verdict="BLOCKED")},
                                     (fixture.identity["reviewed_sha"],))
        fixture.set_ref("review-decision", replacement)
    elif fault == "missing_artifact":
        fixture.git("update-ref", "-d", "refs/heads/aios/artifacts/" + fixture.identity["run_id"])
    elif fault in {"failure", "failure-artifacts"}:
        fixture.set_ref(fault, fixture.base)
    elif fault == "not_included":
        fixture.git("update-ref", "refs/heads/main", fixture.base)
    elif fault == "wrong_lineage":
        fixture.report["lineage"]["decision_sha"] = fixture.base
    with pytest.raises(brain.AttentionError):
        fixture.capture()
    assert not fixture.source_path.exists()


@pytest.mark.parametrize("snapshot,namespace", [
    (snapshot, namespace) for snapshot in (1, 2, 3)
    for namespace in ("artifacts", "review", "review-decision", "failure")
] + [(3, "main")])
def test_independent_review_id_publication_rejects_ref_drift(independent_review_repo, monkeypatch, snapshot, namespace):
    fixture = independent_review_repo
    original_git, reads = brain._git, []

    def git(path, *args):
        output = original_git(path, *args)
        if args[0] == "ls-remote":
            reads.append(args)
            if len(reads) == snapshot:
                # Move after returning a snapshot, including after review proof
                # or after main was sampled for the inclusion proof.
                if namespace == "main":
                    fixture.git("update-ref", "refs/heads/main", fixture.base)
                else:
                    fixture.set_ref(namespace, fixture.base)
        return output

    monkeypatch.setattr(brain, "_git", git)
    with pytest.raises(brain.AttentionError):
        fixture.capture()
    assert len(reads) > snapshot and not fixture.source_path.exists()


def test_task409_explicit_experience_read_preserves_attention_review_and_affinity(tmp_path):
    from tests.test_caa_experience import EpisodeGit, values
    from tests.operator_test_support import git
    from aios_renew.caa_experience import reconstruct_episodes
    fixture = EpisodeGit(tmp_path).result().review()
    sources = brain.GitSources(fixture.repo, git, "origin")
    identity, review = sources.review(fixture.run_id)
    event = brain.attention(brain.RESULT, dict(run_id=fixture.run_id, artifact_sha=fixture.artifact_sha))
    affinity = brain.resolve_return_affinity(event.event_id, sources)
    refs = git(fixture.repo, "show-ref")
    view = reconstruct_episodes(fixture.repo, cut=fixture.cut())
    assert values(view, "review")[0]["reviewed_sha"] == identity["reviewed_sha"]
    assert values(view, "review")[0]["verdict"] == review["verdict"]
    assert sources.review(fixture.run_id) == (identity, review)
    assert brain.resolve_return_affinity(event.event_id, sources) == affinity
    assert git(fixture.repo, "show-ref") == refs
    assert view["authority"] == "NONE" and "next_action" not in view
    assert "CAA" not in brain.SCHEMAS


def test_shared_projector_has_no_generic_semantic_router_or_downstream_activation():
    source = Path(brain.__file__).read_text(encoding="utf-8")
    for forbidden in ("observe_unified_state", "brain_sync", "createWorkflowDispatch",
                      "aios run", "aios remediate", "aios repair", "Python Agent"):
        assert forbidden not in source
    assert '.get("next_action")' not in source and '["next_action"]' not in source
    assert "parse_envelope" in source  # Structural source selectors, not a flow reducer.


@pytest.fixture
def publication_reader():
    """A complete exact publication observation with mutable remote snapshots."""
    root = "refs/heads/aios/"
    refs = {root + name + "/" + IDENTITY["run_id"]: IDENTITY[key] for name, key in
            (("artifacts", "artifact_sha"), ("review", "reviewed_sha"), ("review-decision", "decision_sha"))}
    refs["refs/heads/main"] = "d" * 40
    blobs = {
        (IDENTITY["artifact_sha"], ".ai/transport/run.json"):
            dict(run_id=IDENTITY["run_id"], task=dict(id="TASK-fixture", revision=1), head_sha=None),
        (IDENTITY["artifact_sha"], ".ai/transport/result.json"):
            dict(result=dict(head_sha=IDENTITY["reviewed_sha"])),
        (IDENTITY["decision_sha"], ".ai/reviews/REVIEW-fixture-001.yaml"):
            dict(review_id="REVIEW-fixture-001", reviewed_sha=IDENTITY["reviewed_sha"],
                 mode="PRIMARY", verdict="PASS", acceptance={"AC1": "PASS"}, findings=[]),
        ("d" * 40, ".ai/roadmap-state.yaml"): dict(version=1, sequence=[]),
    }
    calls, snapshots = [], []
    parents = {IDENTITY["decision_sha"]: [IDENTITY["reviewed_sha"]]}
    changed = {IDENTITY["decision_sha"]: [".ai/reviews/REVIEW-fixture-001.yaml"]}
    change = [None]

    def git(path, *args):
        calls.append(args)
        if args[0] == "ls-remote":
            snapshot = {ref: sha for ref, sha in refs.items()
                        if any(fnmatchcase(ref, pattern) for pattern in args[3:])}
            snapshots.append(args[3:])
            if change[0]:
                change[0](snapshot, snapshots)
            return "\n".join(sha + "\t" + ref for ref, sha in snapshot.items())
        if args[0] == "show":
            sha, name = args[1].split(":", 1)
            if (sha, name) not in blobs:
                raise brain.AttentionError("CANONICAL_UNKNOWN")
            blob = blobs[sha, name]
            return blob if isinstance(blob, str) else json.dumps(blob)
        if args[0] == "ls-tree":
            name = args[-1] if args[-1] != ".ai/reviews" else ".ai/reviews/REVIEW-fixture-001.yaml"
            return name if "--name-only" in args else "100644 blob " + "e" * 40 + "\t" + name
        if args[0] == "rev-list" and "--parents" in args:
            return " ".join([args[-1], *parents[args[-1]]])
        if args[0] == "diff-tree":
            return "\n".join(("A\t" if "--name-status" in args else "") + name for name in changed[args[-1]])
        assert args[0] in {"init", "fetch", "rev-list", "cat-file"}
        return ""

    event = brain.project_observation(dict(boundary="PUBLICATION_PROVEN", **IDENTITY,
                                           published_sha=IDENTITY["reviewed_sha"]), {}).event_id
    return SimpleNamespace(git=git, calls=calls, snapshots=snapshots, refs=refs,
                           blobs=blobs, change=change, event=event, parents=parents, changed=changed)


def test_one_generic_observation_reuses_exact_sha_fetches_and_rereads_all_ref_barriers(monkeypatch, publication_reader):
    fixture = publication_reader
    monkeypatch.setattr(wake.CanonicalFreshness, "git", lambda self, path, *args: fixture.git(path, *args))
    projection = wake.CanonicalFreshness(wake.REPOSITORY)
    for observation in range(2):
        fixture.calls.clear()
        fixture.snapshots.clear()
        assert projection.observe(fixture.event) == "UNRESOLVED"
        phases = [args for args in fixture.calls if args[0] == "fetch"]
        assert len(phases) == 1
        fetched = phases[0][6:]
        assert len(fetched) == len(set(fetched)) == 4
        assert set(fetched) == {IDENTITY["artifact_sha"], IDENTITY["decision_sha"],
                                IDENTITY["reviewed_sha"], "d" * 40}
        # Both independent observations reconstruct their own immutable store.
        assert len(fixture.snapshots) == 2
        assert fixture.snapshots[0] == fixture.snapshots[1]
        assert "refs/heads/main" in fixture.snapshots[0]


@pytest.mark.parametrize("fault", ["review-moved", "main-moved-after-inclusion", "main-moved-after-roadmap",
                                  "conflicting", "substituted", "unavailable", "malformed"])
def test_immutable_fetch_reuse_cannot_hide_moved_conflicting_or_substituted_lineage(monkeypatch, publication_reader, fault):
    fixture = publication_reader
    def change(snapshot, snapshots):
        if fault == "review-moved" and len(snapshots) == 2:
            snapshot["refs/heads/aios/review/" + IDENTITY["run_id"]] = "e" * 40
        if fault.startswith("main-moved") and len(snapshots) == 2:
            snapshot["refs/heads/main"] = "e" * 40
        if fault == "conflicting":
            snapshot["refs/heads/aios/failure-artifacts/" + IDENTITY["run_id"]] = "e" * 40
    fixture.change[0] = change
    if fault == "substituted":
        fixture.blobs[IDENTITY["artifact_sha"], ".ai/transport/result.json"]["result"]["head_sha"] = "e" * 40
    def git(self, path, *args):
        if fault == "unavailable" and args[0] == "fetch":
            raise brain.AttentionError("CANONICAL_UNKNOWN")
        if fault == "malformed" and args[0] == "show":
            return '{"duplicate":1,"duplicate":2}'
        return fixture.git(path, *args)
    monkeypatch.setattr(wake.CanonicalFreshness, "git", git)
    assert wake.CanonicalFreshness(wake.REPOSITORY).observe(fixture.event) == "UNKNOWN"


def test_optional_document_fetches_once_and_failed_fetch_is_never_remembered(tmp_path):
    calls, unavailable = [], [True]
    def git(path, *args):
        calls.append(args)
        if args[0] == "fetch" and unavailable[0]:
            raise brain.AttentionError("CANONICAL_UNKNOWN")
        return ".ai/identity.yaml" if args[0] == "ls-tree" else '{"version":1}' if args[0] == "show" else ""
    sources = brain.GitSources(tmp_path, git, "fixture")
    with pytest.raises(brain.AttentionError):
        sources.optional_document("a" * 40, ".ai/identity.yaml")
    unavailable[0] = False
    assert sources.optional_document("a" * 40, ".ai/identity.yaml") == {"version": 1}
    assert len([args for args in calls if args[0] == "fetch"]) == 2  # Failure, then one successful fetch.


def remediation_observation(fixture, family):
    """Canonical authoring shape: provenance is the ref, never a document field."""
    from aios_renew.review import parse_remediation
    subject = IDENTITY["run_id"] + "-F1"
    ref, sha = "refs/heads/aios/remediation/" + subject, "e" * 40
    name = f".ai/remediations/REMEDIATION-{subject}.yaml"
    correction = dict(finding_id="F1", action="CODE_FIX", reviewed_sha=IDENTITY["reviewed_sha"],
                      scope=dict(modify=["src/sample.py"]), verification=dict(affected=["sample-check"]),
                      constraints=dict(hard=["Preserve scope"]))
    assert parse_remediation(json.dumps(correction)).finding_id == "F1"
    assert "source_run_id" not in correction
    review = fixture.blobs[IDENTITY["decision_sha"], ".ai/reviews/REVIEW-fixture-001.yaml"]
    review.update(verdict="CHANGES_REQUIRED", acceptance={"AC1": "FAIL"}, findings=[dict(
        id="F1", basis="AC1", action="CODE_FIX", location="src/sample.py",
        issue="fixture finding", expected="fixture correction")])
    fixture.refs[ref] = sha
    fixture.blobs[sha, name] = correction
    fixture.parents[sha] = [IDENTITY["decision_sha"]]
    fixture.changed[sha] = [name]
    observation = (dict(boundary="REVIEW_FOLLOWUP", **IDENTITY) if family == brain.REVIEW
                   else rejection("AUTHOR_REMEDIATION"))
    item, source, _ = source_event(observation)
    return SimpleNamespace(item=item, source=source, ref=ref, sha=sha, name=name,
                           correction=correction, review=review)


@pytest.mark.parametrize("family", [brain.REVIEW, brain.AUTHORING])
def test_canonical_remediation_resolves_with_one_acquisition_and_two_complete_snapshots(
        monkeypatch, publication_reader, family):
    fixture = publication_reader
    correction = remediation_observation(fixture, family)
    monkeypatch.setattr(wake.CanonicalFreshness, "git", lambda self, path, *args: fixture.git(path, *args))
    monkeypatch.setattr(brain.ArtifactSources, "artifact", lambda self, pointer: correction.source)
    projection = wake.CanonicalFreshness(wake.REPOSITORY)
    for _ in range(2):
        fixture.calls.clear()
        fixture.snapshots.clear()
        assert projection.observe(correction.item.event_id) == "RESOLVED"
        phases = [args for args in fixture.calls if args[0] == "fetch"]
        assert len(phases) == 1
        assert set(phases[0][6:]) == {IDENTITY[key] for key in ("artifact_sha", "decision_sha", "reviewed_sha")} | {correction.sha}
        assert len(phases[0][6:]) == 4
        assert len(fixture.snapshots) == 2
        assert fixture.snapshots[0] == fixture.snapshots[1]
        assert "refs/heads/main" not in fixture.snapshots[0]
        assert correction.ref in fixture.snapshots[0] or correction.ref.rsplit("-", 1)[0] + "-*" in fixture.snapshots[0]


@pytest.mark.parametrize("family", [brain.REVIEW, brain.AUTHORING])
@pytest.mark.parametrize("fault", ["subject", "finding", "reviewed_sha", "action", "invalid_action",
    "missing_action", "review_id", "review_candidate", "review_schema", "result", "run", "review_parent", "merge_parent",
    "extra_path", "missing_blob", "missing_object", "malformed", "duplicate_key", "moved", "new_ref",
    "decision_moved", "artifact_moved", "candidate_moved", "failure_artifact", "failure_ref", "duplicate_ref"])
def test_reduced_remediation_proof_fails_closed_on_binding_lineage_object_and_snapshot_faults(
        monkeypatch, publication_reader, family, fault):
    fixture = publication_reader
    successor = remediation_observation(fixture, family)
    root = "refs/heads/aios/"
    if fault in {"finding", "reviewed_sha", "action", "invalid_action"}:
        key, value = {"finding": ("finding_id", "F-other"), "reviewed_sha": ("reviewed_sha", "f" * 40),
                      "action": ("action", "EVIDENCE_ONLY"), "invalid_action": ("action", "SELECT_STRATEGY")}[fault]
        successor.correction[key] = value
    elif fault == "missing_action":
        del successor.correction["action"]
    elif fault == "review_id":
        successor.review["review_id"] = "REVIEW-other-001"
    elif fault == "review_candidate":
        successor.review["reviewed_sha"] = "f" * 40
    elif fault == "review_schema":
        successor.review["mode"] = "SUBSTITUTED"
    elif fault == "result":
        fixture.blobs[IDENTITY["artifact_sha"], ".ai/transport/result.json"]["result"]["head_sha"] = "f" * 40
    elif fault == "run":
        fixture.blobs[IDENTITY["artifact_sha"], ".ai/transport/run.json"]["run_id"] = "RUN-other-001"
    elif fault in {"review_parent", "merge_parent"}:
        fixture.parents[successor.sha] = (["f" * 40] if fault == "review_parent"
                                        else [IDENTITY["decision_sha"], "f" * 40])
    elif fault == "extra_path":
        fixture.changed[successor.sha].append("src/substituted.py")
    elif fault == "missing_blob":
        del fixture.blobs[successor.sha, successor.name]
    elif fault in {"malformed", "duplicate_key"}:
        fixture.blobs[successor.sha, successor.name] = ("finding_id: [" if fault == "malformed"
            else json.dumps(successor.correction)[:-1] + ',"action":"CODE_FIX"}')
    elif fault == "subject":
        # Same object under the selected ref, but only a different RUN's document.
        fixture.blobs[successor.sha, successor.name.replace(IDENTITY["run_id"], "RUN-other-001")] = fixture.blobs.pop((successor.sha, successor.name))
    elif fault in {"failure_artifact", "failure_ref"}:
        fixture.refs[root + ("failure-artifacts" if fault == "failure_artifact" else "failure") + "/" + IDENTITY["run_id"]] = "f" * 40

    def change(snapshot, snapshots):
        if len(snapshots) != 2:
            return
        if fault == "moved":
            snapshot[successor.ref] = "f" * 40
        elif fault == "new_ref":
            # Appearance of an initially absent canonical binding is a race.
            snapshot[root + "failure/" + IDENTITY["run_id"]] = "f" * 40
        elif fault in {"decision_moved", "artifact_moved", "candidate_moved"}:
            namespace = {"decision_moved": "review-decision", "artifact_moved": "artifacts", "candidate_moved": "review"}[fault]
            snapshot[root + namespace + "/" + IDENTITY["run_id"]] = "f" * 40
    fixture.change[0] = change

    def git(self, path, *args):
        if fault == "missing_object" and args[0] == "fetch":
            raise brain.AttentionError("CANONICAL_UNKNOWN")
        output = fixture.git(path, *args)
        if fault == "duplicate_ref" and args[0] == "ls-remote":
            output += "\n" + successor.sha + "\t" + successor.ref
        return output
    monkeypatch.setattr(wake.CanonicalFreshness, "git", git)
    monkeypatch.setattr(brain.ArtifactSources, "artifact", lambda self, pointer: successor.source)
    assert wake.CanonicalFreshness(wake.REPOSITORY).observe(successor.item.event_id) == "UNKNOWN"


@pytest.mark.parametrize("family", [brain.REVIEW, brain.AUTHORING])
def test_absent_remediation_successor_is_unresolved_only_with_stable_refs(monkeypatch, publication_reader, family):
    fixture = publication_reader
    successor = remediation_observation(fixture, family)
    del fixture.refs[successor.ref]
    monkeypatch.setattr(wake.CanonicalFreshness, "git", lambda self, path, *args: fixture.git(path, *args))
    monkeypatch.setattr(brain.ArtifactSources, "artifact", lambda self, pointer: successor.source)
    monkeypatch.setattr(brain.ArtifactSources, "request", lambda self, path: dict(id=10, body=BODY))
    projection = wake.CanonicalFreshness(wake.REPOSITORY)
    assert projection.observe(successor.item.event_id) == "UNRESOLVED"
    fixture.snapshots.clear()
    fixture.change[0] = lambda snapshot, snapshots: snapshot.update({successor.ref: successor.sha}) if len(snapshots) == 2 else None
    assert projection.observe(successor.item.event_id) == "UNKNOWN"


def test_author_remediation_rejected_subject_sha_must_match_canonical_review(monkeypatch, publication_reader):
    fixture = publication_reader
    successor = remediation_observation(fixture, brain.AUTHORING)
    observation = rejection("AUTHOR_REMEDIATION")
    observation["subject_sha"] = "f" * 40
    item, source, _ = source_event(observation)
    monkeypatch.setattr(wake.CanonicalFreshness, "git", lambda self, path, *args: fixture.git(path, *args))
    monkeypatch.setattr(brain.ArtifactSources, "artifact", lambda self, pointer: source)
    assert wake.CanonicalFreshness(wake.REPOSITORY).observe(item.event_id) == "UNKNOWN"


def test_repaired_repository_roadmap_uses_unique_keys_and_exact_planning_bookmarks(monkeypatch, publication_reader):
    fixture = publication_reader
    raw = (Path(__file__).resolve().parents[1] / ".ai/roadmap-state.yaml").read_text(encoding="utf-8")
    roadmap = brain._load_yaml(raw, "repaired repository roadmap")
    fixture.blobs["d" * 40, ".ai/roadmap-state.yaml"] = raw
    monkeypatch.setattr(wake.CanonicalFreshness, "git", lambda self, path, *args: fixture.git(path, *args))
    projection = wake.CanonicalFreshness(wake.REPOSITORY)
    assert projection.observe(fixture.event) == "UNRESOLVED"
    completed = dict(run_id=IDENTITY["run_id"], artifacts_sha=IDENTITY["artifact_sha"],
                     review_id="REVIEW-fixture-001", review_decision_sha=IDENTITY["decision_sha"],
                     reviewed_sha=IDENTITY["reviewed_sha"], published_sha=IDENTITY["reviewed_sha"])
    roadmap["sequence"].append(dict(status="DONE", completed_by=completed))
    fixture.blobs["d" * 40, ".ai/roadmap-state.yaml"] = roadmap
    assert projection.observe(fixture.event) == "RESOLVED"
    for key in completed:
        if key == "run_id":
            continue
        original = completed[key]
        completed[key] = "REVIEW-other" if key == "review_id" else "f" * 40
        assert projection.observe(fixture.event) == "UNKNOWN"
        completed[key] = original
    roadmap["sequence"][-1]["status"] = "ACTIVE"
    assert projection.observe(fixture.event) == "UNRESOLVED"
    # An unrelated historical duplicate still rejects the entire document.
    fixture.blobs["d" * 40, ".ai/roadmap-state.yaml"] = json.dumps(roadmap)[:-1] + ',"historical":{"previous_state":"old","previous_state":"new"}}'
    assert projection.observe(fixture.event) == "UNKNOWN"


@pytest.mark.parametrize("family", [brain.PUBLICATION_SUCCESS, brain.REVIEW, brain.AUTHORING])
def test_reduced_proof_completes_under_unchanged_fake_clock_and_operation_ceilings(
        monkeypatch, publication_reader, family):
    fixture = publication_reader
    source = None
    if family != brain.PUBLICATION_SUCCESS:
        successor = remediation_observation(fixture, family)
        event, source = successor.item.event_id, successor.source
    else:
        event = fixture.event
    now, git_timeouts, remote_calls = [100.0], [], []
    monkeypatch.setattr(wake.time, "monotonic", lambda: now[0])
    if source:
        def artifact(self, pointer):
            assert self.deadline - now[0] <= 30
            now[0] += 3  # Bounded source API reconstruction also consumes this window.
            return source
        monkeypatch.setattr(brain.ArtifactSources, "artifact", artifact)

    def run(command, **kwargs):
        args = command[3:]
        git_timeouts.append(kwargs["timeout"])
        assert 0 < kwargs["timeout"] <= 15
        if args[0] in {"ls-remote", "fetch"}:
            remote_calls.append(args[0])
            now[0] += 6  # Ordinary latency; no sleep or timeout inflation.
        else:
            now[0] += 0.02
        return SimpleNamespace(returncode=0, stdout=fixture.git(Path(command[2]), *args).encode())
    monkeypatch.setattr(wake.subprocess, "run", run)
    projection = wake.CanonicalFreshness(wake.REPOSITORY)
    for _ in range(2):
        started = now[0]
        remote_calls.clear()
        expected = "UNRESOLVED" if family == brain.PUBLICATION_SUCCESS else "RESOLVED"
        assert projection.observe(event) == expected
        assert projection.deadline == started + 30
        assert now[0] - started < 30
        assert remote_calls == ["ls-remote", "fetch", "ls-remote"]
    assert max(git_timeouts) == 15


def test_api_ceiling_and_remaining_subject_budget_are_preserved_with_fake_clock(monkeypatch):
    import urllib.request
    now, timeouts = [100.0], []
    monkeypatch.setattr(brain.time, "monotonic", lambda: now[0])

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            assert limit == 1048577
            return b"{}"

    def open_request(request, timeout):
        assert request.full_url.endswith("/issues/2")
        timeouts.append(timeout)
        now[0] += 0.5
        return Response()
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_request))
    sources = brain.ArtifactSources(brain.REPOSITORY)
    assert sources.deadline == 130
    assert sources.request("/issues/2") == {}
    now[0] = 128
    assert sources.request("/issues/2") == {}
    assert timeouts == [10, 2]
    now[0] = 130
    with pytest.raises(brain.AttentionError):
        sources.request("/issues/2")
    assert timeouts == [10, 2]

class AffinitySources:
    """Self-contained exact refs/blobs with no lifecycle reducer or network."""
    def __init__(self, kind="RESULT", selector=None):
        from aios_renew.return_affinity import LEGACY
        from dataclasses import asdict
        self.selector = asdict(LEGACY) if selector is None else selector
        self.task = dict(task_id="TASK-fixture", revision=1, goal="Affinity", problem="Lineage",
                        assumptions=[], scope=dict(inspect=[], modify=[]), non_goals=[], constraints=dict(hard=[]),
                        acceptance=[dict(id="AC1", condition="Selector")], verification=dict(required=["git diff --check"]),
                        return_affinity=self.selector)
        self.values, self.docs, self.reads, self.drift = {}, {}, 0, False
        self.kind = kind
        self.add_run(IDENTITY["run_id"], IDENTITY["artifact_sha"], IDENTITY["reviewed_sha"], "1" * 40, kind)
        self.values["refs/heads/main"] = "d" * 40
        self.values["refs/heads/aios/review-decision/" + IDENTITY["run_id"]] = IDENTITY["decision_sha"]
        self.docs[("d" * 40, ".ai/tasks/TASK-fixture.yaml")] = copy.deepcopy(self.task)

    def add_run(self, run_id, artifact, head, base, kind="RESULT"):
        root = "refs/heads/aios/"
        self.values[root + ("artifacts/" if kind == "RESULT" else "failure-artifacts/") + run_id] = artifact
        self.values[root + ("review/" if kind == "RESULT" else "failure/") + run_id] = head
        run = dict(run_id=run_id, task={"id": "TASK-fixture", "revision": 1}, executor="codex",
                   base_sha=base, head_sha=None, workspace="synthetic", status="ACTIVE", return_affinity=copy.deepcopy(self.selector))
        self.docs[artifact, ".ai/transport/run.json"] = run
        self.docs[artifact, ".ai/transport/result.json"] = {"result": {"head_sha": head}, "evidence": []}
        self.docs[artifact, ".ai/transport/failure.json"] = dict(run_id=run_id, task=run["task"], failed_head_sha=head)
        for sha in (base, head):
            self.docs[sha, ".ai/tasks/TASK-fixture.yaml"] = copy.deepcopy(self.task)
        return run

    def refs(self, *patterns):
        self.reads += 1
        values = {ref: sha for ref, sha in self.values.items() if any(fnmatchcase(ref, pattern) for pattern in patterns)}
        if self.drift and self.reads > 1 and values:
            values[next(iter(values))] = "e" * 40
        return values

    def document(self, sha, name):
        return self.docs[sha, name]

    def optional_document(self, sha, name):
        return self.docs.get((sha, name))

    def included(self, sha, head):
        return True

    def review(self, run_id, decision_sha=None):
        if run_id == IDENTITY["run_id"]:
            return dict(IDENTITY), {"verdict": self.verdict}
        return {"reviewed_sha": "3" * 40}, dict(review_id="REVIEW-source-001", verdict="CHANGES_REQUIRED",
                                              findings=[dict(id="F1", action="CODE_FIX")])


def canonical_execution_failure(run, head, *, dirty=True, phase="EXECUTION"):
    from aios_renew.review_transport import public_failure
    from tests.test_review_transport import _native_failure_fixture

    _, private = _native_failure_fixture(dirty=dirty)
    private.update(run_id=run["run_id"], task=copy.deepcopy(run["task"]), executor=run["executor"],
                   base_sha=run["base_sha"], failed_head_sha=head)
    if phase != "EXECUTION":
        private.update(phase=phase, error=dict(type="RuntimeVerificationError", message="Runtime verification failed"))
    return json.loads(public_failure(json.dumps(private).encode()))


class ArtifactOnlyFailureSources(AffinitySources):
    """RUN-401-002-shaped public artifact with an independently readable HEAD."""
    def __init__(self, *, candidate_present=False, selector=None, dirty=True, phase="EXECUTION"):
        super().__init__("FAILURE", selector)
        self.run_id, self.artifact, self.head = (IDENTITY[key] for key in ("run_id", "artifact_sha", "reviewed_sha"))
        self.run = self.docs[self.artifact, ".ai/transport/run.json"]
        self.failure = canonical_execution_failure(self.run, self.head, dirty=dirty, phase=phase)
        self.docs[self.artifact, ".ai/transport/failure.json"] = self.failure
        self.candidate_ref = "refs/heads/aios/failure/" + self.run_id
        self.terminal_ref = "refs/heads/aios/failure-artifacts/" + self.run_id
        if not candidate_present:
            del self.values[self.candidate_ref]
        del self.values["refs/heads/aios/review-decision/" + self.run_id]
        self.event = brain.attention(brain.FAILURE, dict(run_id=self.run_id, artifact_sha=self.artifact)).event_id


@pytest.mark.parametrize("candidate_present", [False, True])
@pytest.mark.parametrize("selector", [dict(kind="LEGACY_REPOSITORY_DEFAULT_ROUTE"),
    dict(kind="ORIGIN_AFFINE", route_handle="page-origin-v1:" + "a" * 64, generation=3)])
def test_task402_artifact_only_failure_preserves_exact_original_affinity(candidate_present, selector):
    from aios_renew.return_affinity import parse_affinity
    sources = ArtifactOnlyFailureSources(candidate_present=candidate_present, selector=selector)
    before = copy.deepcopy((sources.values, sources.docs))
    assert sources.failure["phase"] == "EXECUTION"
    assert sources.failure["candidate"]["dirty"] is True
    assert sources.failure["candidate"]["transportable"] is False
    assert brain.resolve_return_affinity(sources.event, sources) == parse_affinity(selector)
    recovery = brain.attention(brain.RECOVERY, dict(original_event_id=sources.event))
    assert brain.resolve_return_affinity(recovery.event_id, sources) == parse_affinity(selector)
    assert (sources.values, sources.docs) == before


@pytest.mark.parametrize("defect", ["run", "task", "run-head", "base", "base-task", "head-task", "run-route",
    "head-route", "ancestry", "unreadable-head", "partial", "integrity", "transportable", "phase",
    "opposite", "opposite-candidate", "opposite-decision", "divergent-ref", "malformed-ref", "null-ref", "race"])
def test_task402_artifact_only_affinity_rejects_unproved_or_contradictory_source(defect):
    selector = dict(kind="ORIGIN_AFFINE", route_handle="page-origin-v1:" + "a" * 64, generation=3)
    sources = ArtifactOnlyFailureSources(selector=selector)
    path = ".ai/tasks/TASK-fixture.yaml"
    if defect == "run":
        sources.run["run_id"] = "RUN-other-001"
    elif defect == "task":
        sources.run["task"] = dict(id="TASK-other", revision=1)
    elif defect == "run-head":
        sources.run["head_sha"] = "f" * 40
    elif defect == "base":
        sources.run["base_sha"] = "f" * 40
    elif defect in {"base-task", "head-task"}:
        sha = sources.run["base_sha"] if defect == "base-task" else sources.head
        sources.docs[sha, path]["revision"] = 2
    elif defect in {"run-route", "head-route"}:
        document = sources.run if defect == "run-route" else sources.docs[sources.head, path]
        document["return_affinity"] = dict(kind="LEGACY_REPOSITORY_DEFAULT_ROUTE")
    elif defect == "ancestry":
        sources.included = lambda *_: False
    elif defect == "unreadable-head":
        del sources.docs[sources.head, path]
    elif defect == "partial":
        sources.docs[sources.artifact, ".ai/transport/failure.json"] = dict(run_id=sources.run_id,
            task=sources.run["task"], failed_head_sha=sources.head)
    elif defect in {"integrity", "transportable", "phase"}:
        sources.failure["phase"] = "VERIFICATION" if defect == "phase" else sources.failure["phase"]
        if defect == "transportable":
            sources.failure["candidate"]["transportable"] = True
        elif defect == "integrity":
            sources.failure["public_projection"]["projection_digest"] = "0" * 64
    elif defect.startswith("opposite"):
        namespace = {"opposite": "artifacts", "opposite-candidate": "review", "opposite-decision": "review-decision"}[defect]
        sources.values["refs/heads/aios/" + namespace + "/" + sources.run_id] = "f" * 40
    elif defect in {"divergent-ref", "malformed-ref", "null-ref"}:
        sources.values[sources.candidate_ref] = {"divergent-ref": "f" * 40, "malformed-ref": "bad", "null-ref": None}[defect]
    else:
        sources.drift = True
    assert brain.resolve_return_affinity(sources.event, sources) is None


@pytest.mark.parametrize("defect", ["integrity", "review", "review-decision"])
def test_task402_present_candidate_cannot_hide_canonical_failure_corruption(defect):
    sources = ArtifactOnlyFailureSources(candidate_present=True)
    if defect == "integrity":
        sources.failure["public_projection"]["projection_digest"] = "0" * 64
    else:
        sources.values["refs/heads/aios/" + defect + "/" + sources.run_id] = "f" * 40
    assert brain.resolve_return_affinity(sources.event, sources) is None


@pytest.mark.parametrize("candidate_present", [False, True])
@pytest.mark.parametrize("dirty, phase", [(False, "EXECUTION"), (True, "VERIFICATION")])
def test_task402_missing_candidate_permission_requires_nontransportable_execution(candidate_present, dirty, phase):
    from aios_renew.return_affinity import LEGACY
    sources = ArtifactOnlyFailureSources(candidate_present=candidate_present, dirty=dirty, phase=phase)
    assert brain.resolve_return_affinity(sources.event, sources) == (LEGACY if candidate_present else None)


def test_task402_result_still_requires_candidate_ref():
    sources = AffinitySources()
    del sources.values["refs/heads/aios/review/" + IDENTITY["run_id"]]
    item = brain.attention(brain.RESULT, dict(run_id=IDENTITY["run_id"], artifact_sha=IDENTITY["artifact_sha"]))
    assert brain.resolve_return_affinity(item.event_id, sources) is None


@pytest.mark.parametrize("family", [brain.RESULT, brain.FAILURE, brain.REVIEW,
                                    brain.PUBLICATION_SUCCESS, brain.PUBLICATION_FAILURE])
def test_h4c1_exact_terminal_review_publication_lineage_and_recovery(family):
    from aios_renew.return_affinity import OriginAffinity
    from dataclasses import asdict
    affinity = OriginAffinity("page-origin-v1:" + "a" * 64, 3)
    sources = AffinitySources("FAILURE" if family == brain.FAILURE else "RESULT", asdict(affinity))
    sources.verdict = "CHANGES_REQUIRED" if family == brain.REVIEW else "PASS"
    artifact = None
    if family in {brain.RESULT, brain.FAILURE}:
        item = brain.attention(family, dict(run_id=IDENTITY["run_id"], artifact_sha=IDENTITY["artifact_sha"]))
    elif family == brain.PUBLICATION_FAILURE:
        item, source, _ = source_event(dict(boundary="PUBLICATION_FAILED", **IDENTITY, stage="EXECUTION"))
        artifact = Artifacts(source)
    else:
        item = brain.attention(family, dict(IDENTITY, source_boundary="REVIEW_FOLLOWUP" if family == brain.REVIEW else "PUBLICATION_PROVEN",
            **({"published_sha": IDENTITY["reviewed_sha"]} if family == brain.PUBLICATION_SUCCESS else {})))
    event_id = item.event_id
    assert brain.resolve_return_affinity(event_id, sources, artifact) == affinity
    recovery = brain.attention(brain.RECOVERY, {"original_event_id": event_id})
    assert brain.resolve_return_affinity(recovery.event_id, sources, artifact) == affinity
    assert item.event_id == event_id and "page-origin" not in item.render()


def test_typed_publication_recovery_preserves_only_the_original_run_authored_affinity():
    from aios_renew.return_affinity import OriginAffinity
    from dataclasses import asdict
    affinity = OriginAffinity("page-origin-v1:" + "a" * 64, 3)
    sources = AffinitySources(selector=asdict(affinity))
    sources.verdict = "PASS"
    item, source, _ = source_event(recovery_observation())
    assert brain.resolve_return_affinity(item.event_id, sources, Artifacts(source)) == affinity
    sources.drift = True
    assert brain.resolve_return_affinity(item.event_id, sources, Artifacts(source)) is None


@pytest.mark.parametrize("defect", ["missing_task", "missing_run_affinity", "candidate_drift", "run_drift", "ref_drift", "opposite_terminal", "wrong_artifact"])
def test_h4c1_unproved_or_conflicting_terminal_lineage_has_no_affinity(defect):
    selector = dict(kind="ORIGIN_AFFINE", route_handle="page-origin-v1:" + "a" * 64, generation=1)
    sources = AffinitySources(selector=selector)
    run = sources.docs[IDENTITY["artifact_sha"], ".ai/transport/run.json"]
    if defect == "missing_task":
        del sources.docs[run["base_sha"], ".ai/tasks/TASK-fixture.yaml"]
    elif defect == "missing_run_affinity":
        del run["return_affinity"]
    elif defect == "candidate_drift":
        sources.docs[IDENTITY["reviewed_sha"], ".ai/tasks/TASK-fixture.yaml"]["return_affinity"] = {"kind": "LEGACY_REPOSITORY_DEFAULT_ROUTE"}
    elif defect == "run_drift":
        run["return_affinity"]["generation"] = 2
    elif defect == "ref_drift":
        sources.drift = True
    elif defect == "opposite_terminal":
        sources.values["refs/heads/aios/failure-artifacts/" + IDENTITY["run_id"]] = "e" * 40
    else:
        sources.values["refs/heads/aios/artifacts/" + IDENTITY["run_id"]] = "e" * 40
    item = brain.attention(brain.RESULT, {"run_id": IDENTITY["run_id"], "artifact_sha": IDENTITY["artifact_sha"]})
    assert brain.resolve_return_affinity(item.event_id, sources) is None


def test_h4c1_historical_missing_fields_resolve_only_as_legacy():
    from aios_renew.return_affinity import LEGACY
    sources = AffinitySources()
    for doc in sources.docs.values():
        if isinstance(doc, dict):
            doc.pop("return_affinity", None)
    item = brain.attention(brain.RESULT, {"run_id": IDENTITY["run_id"], "artifact_sha": IDENTITY["artifact_sha"]})
    assert brain.resolve_return_affinity(item.event_id, sources) == LEGACY


def test_h4c1_precanonical_author_task_uses_only_exact_admitted_payload():
    affinity = dict(kind="ORIGIN_AFFINE", route_handle="page-origin-v1:" + "a" * 64, generation=1)
    sources = AffinitySources(selector=affinity)
    sources.docs.pop(("d" * 40, ".ai/tasks/TASK-fixture.yaml"))
    body = json.dumps(dict(format="AIOS_INGRESS_ENVELOPE", version=1, operation="AUTHOR_TASK",
        identity={"task_id": "TASK-fixture"}, expected_state={"expected_main_sha": "d" * 40}, payload=sources.task))
    obs = rejection("AUTHOR_TASK")
    obs.update(body_digest=hashlib.sha256(body.encode()).hexdigest(), subject_sha="0" * 40)
    item, source, _ = source_event(obs)
    artifacts = Artifacts(source)
    artifacts.body = body
    from aios_renew.return_affinity import parse_affinity
    assert brain.resolve_return_affinity(item.event_id, sources, artifacts) == parse_affinity(affinity)
    artifacts.body = body + " "
    assert brain.resolve_return_affinity(item.event_id, sources, artifacts) is None


@pytest.mark.parametrize("operation", ["PRIMARY", "REMEDIATION", "REPAIR"])
def test_h4c1_dispatch_resolves_exact_task_or_source_run(operation):
    from aios_renew.return_affinity import OriginAffinity
    from dataclasses import asdict
    selector = OriginAffinity("page-origin-v1:" + "a" * 64, 1)
    sources = AffinitySources("FAILURE" if operation == "REPAIR" else "RESULT", asdict(selector))
    observation = delivery(operation=operation)
    if operation == "PRIMARY":
        observation.update(subject_id="TASK-fixture", subject_sha="d" * 40)
    elif operation == "REPAIR":
        observation.update(subject_id=IDENTITY["run_id"], subject_sha="f" * 40)
        sources.values["refs/heads/aios/repair/" + IDENTITY["run_id"]] = "f" * 40
        sources.docs["f" * 40, ".ai/transport/repair.json"] = dict(failed_run_id=IDENTITY["run_id"], failed_head_sha=IDENTITY["reviewed_sha"])
    else:
        observation.update(subject_id=IDENTITY["run_id"], subject_sha=IDENTITY["reviewed_sha"])
    item, source, _ = source_event(observation)
    artifacts = Artifacts(source)
    assert brain.resolve_return_affinity(item.event_id, sources, artifacts) == selector
    artifacts.source = brain.source_document([])
    assert brain.resolve_return_affinity(item.event_id, sources, artifacts) is None


@pytest.mark.parametrize("family", ["REMEDIATION", "REPAIR"])
def test_h4c1_descendant_affinity_must_match_exact_original_run(family):
    from aios_renew.return_affinity import OriginAffinity
    from dataclasses import asdict
    selector = OriginAffinity("page-origin-v1:" + "a" * 64, 1)
    sources = AffinitySources(selector=asdict(selector))
    artifact = IDENTITY["artifact_sha"]
    child = sources.docs[artifact, ".ai/transport/run.json"]
    source_run_id, source_sha, source_head = "RUN-source-001", "2" * 40, "3" * 40
    source = sources.add_run(source_run_id, source_sha, source_head, "4" * 40,
                             "FAILURE" if family == "REPAIR" else "RESULT")
    child["base_sha"] = source_head
    if family == "REMEDIATION":
        sources.docs[artifact, ".ai/transport/run.json"] = dict(kind=family, execution=dict(
            run=child, review_id="REVIEW-source-001", finding=dict(id="F1"),
            remediation=dict(finding_id="F1", action="CODE_FIX", reviewed_sha=source_head,
                             modification_scope=[], affected_verification=[])))
    else:
        sources.docs[artifact, ".ai/transport/repair.json"] = dict(run=child, task=sources.task,
            failed_run_id=source_run_id, failed_head_sha=source_head)
    item = brain.attention(brain.RESULT, {"run_id": IDENTITY["run_id"], "artifact_sha": artifact})
    assert brain.resolve_return_affinity(item.event_id, sources) == selector
    source["return_affinity"]["generation"] = 2
    assert brain.resolve_return_affinity(item.event_id, sources) is None
