"""Synthetic exact-source regressions. No live delivery or lifecycle operations."""

from fnmatch import fnmatchcase
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from aios_renew import brain_attention as brain
from aios_renew import local_chat_wake as wake
from aios_renew.operational_receipt import workflow_failure_receipt


IDENTITY = dict(run_id="RUN-fixture-001", artifact_sha="a" * 40,
                decision_sha="b" * 40, reviewed_sha="c" * 40)
BODY = "a rejected immutable carrier"


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


class Sources:
    def __init__(self, verdict="CHANGES_REQUIRED"):
        self.identity = dict(IDENTITY)
        self.decision = dict(review_id="REVIEW-fixture-001", verdict=verdict, findings=[{"id": "F1"}, {"id": "F2"}])
        self.values = {"refs/heads/main": "d" * 40}
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

    def optional_document(self, sha, name):
        return self.docs.get((sha, name))

    def included(self, sha, main):
        assert sha == IDENTITY["reviewed_sha"]
        return self.is_included


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


def test_review_followup_requires_all_exact_finding_successors_and_pass_never_becomes_correction():
    item = source_event(dict(boundary="REVIEW_FOLLOWUP", **IDENTITY))[0]
    sources = Sources()
    assert brain.freshness(item, sources, Artifacts()) == "UNRESOLVED"
    for number in (1, 2):
        sha = str(number) * 40
        subject = IDENTITY["run_id"] + f"-F{number}"
        sources.values["refs/heads/aios/remediation/" + subject] = sha
        sources.docs[sha, f".ai/remediations/REMEDIATION-{subject}.yaml"] = dict(source_run_id=IDENTITY["run_id"], reviewed_sha=IDENTITY["reviewed_sha"], finding_id=f"F{number}")
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
    review = dict(review_id="REVIEW-fixture-001", reviewed_sha="c" * 40, mode="FULL",
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
            return ".ai/reviews/REVIEW-fixture-001.yaml"
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


def test_shared_projector_has_no_generic_semantic_router_or_downstream_activation():
    source = Path(brain.__file__).read_text(encoding="utf-8")
    for forbidden in ("observe_unified_state", "brain_sync", "createWorkflowDispatch",
                      "aios run", "aios remediate", "aios repair", "Python Agent"):
        assert forbidden not in source
    assert '.get("next_action")' not in source and '["next_action"]' not in source
    assert "parse_envelope" in source  # Structural source selectors, not a flow reducer.
