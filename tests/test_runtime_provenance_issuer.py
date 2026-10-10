"""Owner integration with an isolated service fixture; no live origin claims.

TLS/OS setup is not simulated as production evidence. The fixture substitutes
approved peer delivery only, leaving owner, object and immutable-source checks real.
All refs are in temporary repositories. Runtime runs these tests, not Executor.
"""
import copy
import base64
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aios_renew import operator as op
from aios_renew import runtime_provenance_issuer as issuer
from aios_renew.artifacts import ResultPackage, validate_structural_result
from aios_renew.authoring_ingress import IngressResult, execute_ingress
from aios_renew.runtime import RuntimeCompletion
from aios_renew.runtime_provenance_owner import join_authenticated_provenance
from aios_renew.runtime_writer_broker import BrokerError, BrokerService, decode, encoded, digest
from test_runtime_provenance_owner import primary_repository, primary, review_envelope, git


class PeerFixture:
    def __init__(self, service, peer):
        self.service, self.peer = service, peer
    def request(self, request):
        return self.service.handle(self.peer, request)


@pytest.fixture
def terminal(tmp_path, monkeypatch):
    repo, remote = primary_repository(tmp_path)
    state = op.runtime_paths(repo)
    service = BrokerService(repo=repo, journal=tmp_path / "source.sqlite", key=b"fixture receipt secret" * 2,
                            peers={"runtime-peer": "RUNTIME", "control-peer": "CONTROL", "reader-peer": "READER"},
                            writers={}, raw_root=state.verification)
    owners = []
    initialize = RuntimeCompletion.__init__
    def capture(owner, **kwargs):
        initialize(owner, **kwargs)
        owners.append(owner)
    monkeypatch.setattr(RuntimeCompletion, "__init__", capture)
    def factory(**kwargs):
        def dispatch_primary(**inputs):
            (repo / "src").mkdir(exist_ok=True)
            (repo / "src/sample.py").write_text("# prospective fixture candidate\n", encoding="utf-8")
            git(repo, "add", "src/sample.py")
            git(repo, "commit", "-qm", "fixture candidate")
            result = validate_structural_result({"head_sha": git(repo, "rev-parse", "HEAD"),
                "claims": [{"id": "C1", "satisfies": ["AC1"], "claim": "Fixture implementation.", "evidence": []}],
                "changed_files": ["src/sample.py"], "unresolved": []})
            return ResultPackage(result, ())
        return SimpleNamespace(dispatch_primary=dispatch_primary)
    monkeypatch.setattr(op, "primary_dispatcher", factory)
    monkeypatch.setattr(issuer, "protected_client", lambda: PeerFixture(service, "runtime-peer"))
    def fixture_verification(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=b"fixture verification raw\n", stderr=b"")
    primary(repo, verification_runner=fixture_verification)
    owner = owners[0]
    assert owner.source_provenance.issuer_authenticated
    return repo, remote, owner, service


def submit(terminal, monkeypatch, verdict="PASS"):
    repo, _, owner, service = terminal
    monkeypatch.setattr(issuer, "protected_client", lambda: PeerFixture(service, "control-peer"))
    decision = execute_ingress(review_envelope({"run_id": owner.run.run_id,
                                               "candidate_sha": dict(owner.source_provenance.bindings)["candidate_sha"]}, verdict), repo=repo)
    assert decision.source_provenance.reviewer_origin_authenticated
    return decision


@pytest.mark.parametrize("verdict", ["PASS", "CHANGES_REQUIRED"])
def test_real_owner_completion_and_independent_ingress_join(terminal, monkeypatch, verdict):
    decision = submit(terminal, monkeypatch, verdict)
    repo, remote, owner, service = terminal
    monkeypatch.setattr(issuer, "protected_client", lambda: PeerFixture(service, "reader-peer"))
    before = git(remote, "rev-parse", "main")
    result = join_authenticated_provenance(owner, decision)
    assert result.issuer_authenticated and result.reviewer_origin_authenticated
    assert result.status == "UNKNOWN" and not result.reviewer_authority
    assert dict(result.bindings)["recorded_verdict"] == verdict
    assert result.raw_state == "RAW_UNAVAILABLE"
    assert "INDEPENDENT_CURRENTNESS_UNAVAILABLE" in result.gaps
    assert git(remote, "rev-parse", "main") == before
    # Exact bytes remain local; export carries opaque locators, no protected paths/raw.
    pins = dict(owner.source_provenance.bindings)
    export = git(repo, "show", pins["artifact_sha"] + ":.ai/transport/result.json")
    assert "fixture verification raw" not in export and str(repo) not in export
    assert "protected-runtime-raw/" in export
    original = decode((owner.state.results / (owner.run.run_id + ".json")).read_bytes())
    paths = [{"evidence_id": item["evidence_id"], "path": str((repo / item["raw"]["path"]).resolve())}
             for item in original["evidence"]]
    matched = issuer.inspect_sources(dict(decision.source_provenance.bindings), raw_paths=paths)
    assert matched.raw_state == "MATCHED" and matched.status == "UNKNOWN"
    assert not matched.reviewer_authority


def test_public_constructors_equal_copies_and_helpers_never_enroll(terminal, monkeypatch):
    decision = submit(terminal, monkeypatch)
    _, _, owner, _ = terminal
    copied = replace(decision)
    assert copied == decision
    assert not copied.source_provenance.reviewer_origin_authenticated
    issuer._issue_review(copied)
    issuer._finish_terminal(owner, object(), "a" * 40)
    assert not copied.source_provenance.reviewer_origin_authenticated
    assert join_authenticated_provenance(owner, copied).status == "UNKNOWN"
    assert join_authenticated_provenance(issuer.SourceFacts(issuer_authenticated=True), decision).status == "BLOCK"


def test_owner_change_blocks_and_returned_metadata_cannot_mutate_storage(terminal):
    owner = terminal[2]
    view = dict(owner.source_provenance.bindings)
    view["raw"][0]["sha256"] = "a" * 64
    assert dict(owner.source_provenance.bindings)["raw"][0]["sha256"] != "a" * 64
    owner.run = replace(owner.run, executor="antigravity")
    assert owner.source_provenance.status == "BLOCK"


def test_review_replay_only_reads_original_source(terminal, monkeypatch):
    first = submit(terminal, monkeypatch)
    repo, _, owner, service = terminal
    before = service.db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    replay = execute_ingress(review_envelope({"run_id": owner.run.run_id,
        "candidate_sha": dict(owner.source_provenance.bindings)["candidate_sha"]}), repo=repo)
    assert replay.replayed and replay.source_provenance.reviewer_origin_authenticated
    assert dict(replay.source_provenance.bindings) == dict(first.source_provenance.bindings)
    assert service.db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == before
    service.db.execute("DELETE FROM sources WHERE identity LIKE 'REVIEW_INGRESS:%'")
    service.db.commit()
    absent = execute_ingress(review_envelope({"run_id": owner.run.run_id,
        "candidate_sha": dict(owner.source_provenance.bindings)["candidate_sha"]}), repo=repo)
    assert absent.replayed and not absent.source_provenance.reviewer_origin_authenticated
    assert service.db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == before - 1


@pytest.mark.parametrize("field", ["run_id", "task_blob_sha", "candidate_sha", "artifact_sha", "decision_sha", "result", "evidence", "profile"])
def test_consumer_rejects_wrong_exact_pins(terminal, monkeypatch, field):
    decision = submit(terminal, monkeypatch)
    service = terminal[3]
    monkeypatch.setattr(issuer, "protected_client", lambda: PeerFixture(service, "reader-peer"))
    expected = copy.deepcopy(dict(decision.source_provenance.bindings))
    if field == "run_id":
        expected[field] = "RUN-105-999"
    elif isinstance(expected[field], dict):
        expected[field]["sha256"] = "a" * 64
    else:
        expected[field] = "a" * 40
    result = issuer.inspect_sources(expected)
    assert result.status in {"BLOCK", "UNKNOWN"}
    assert not result.issuer_authenticated and not result.reviewer_origin_authenticated


def test_raw_swap_and_unapproved_raw_reader(terminal, monkeypatch, tmp_path):
    decision = submit(terminal, monkeypatch)
    repo, _, owner, service = terminal
    original = decode((owner.state.results / (owner.run.run_id + ".json")).read_bytes())
    paths = [{"evidence_id": item["evidence_id"], "path": str((repo / item["raw"]["path"]).resolve())}
             for item in original["evidence"]]
    request = dict(decision.source_provenance.bindings)
    monkeypatch.setattr(issuer, "protected_client", lambda: PeerFixture(service, "control-peer"))
    denied = issuer.inspect_sources(request, raw_paths=paths)
    assert denied.raw_state == "RAW_UNAVAILABLE" and not denied.issuer_authenticated
    monkeypatch.setattr(issuer, "protected_client", lambda: PeerFixture(service, "reader-peer"))
    for item in paths:
        from pathlib import Path
        Path(item["path"]).write_text("swapped raw", encoding="utf-8")
    assert issuer.inspect_sources(request, raw_paths=paths).status == "BLOCK"


def test_signed_source_swap_and_torn_journal_rejected(terminal, monkeypatch):
    decision = submit(terminal, monkeypatch)
    service = terminal[3]
    record = service._source_record("REVIEW_INGRESS:" + terminal[2].run.run_id)
    record["facts"]["decision_sha"] = "a" * 40
    service.db.execute("UPDATE sources SET record=? WHERE identity LIKE 'REVIEW_INGRESS:%'", (encoded(record),))
    service.db.commit()
    with pytest.raises(BrokerError, match="authentication"):
        service.handle("reader-peer", {"action": "read", "identity": "REVIEW_INGRESS:" + terminal[2].run.run_id})


@pytest.mark.parametrize("field", ["task_blob_sha", "candidate_sha", "artifact_sha", "profile", "raw"])
def test_service_independently_rejects_swapped_objects_or_original_raw(terminal, field):
    repo, _, owner, service = terminal
    facts = copy.deepcopy(dict(owner.source_provenance.bindings))
    if field == "raw":
        facts["raw"][0]["sha256"] = "a" * 64
    elif field == "profile":
        facts["profile"]["sha256"] = "a" * 64
    else:
        facts[field] = "a" * 40
    original_path = owner.state.results / (owner.run.run_id + ".json")
    original = original_path.read_bytes()
    paths = [{"evidence_id": item["evidence_id"], "path": str((repo / item["raw"]["path"]).resolve())}
             for item in decode(original)["evidence"]]
    with pytest.raises(BrokerError):
        service.handle("runtime-peer", {"action": "issue", "kind": "RUNTIME_TERMINAL", "facts": facts,
            "paths": paths, "original_result": base64.b64encode(original).decode(),
            "original_run": base64.b64encode(owner.run_path.read_bytes()).decode()})


def test_unprovisioned_service_and_historical_fixture_stay_unknown(monkeypatch):
    def absent():
        raise BrokerError("protected setup unavailable")
    monkeypatch.setattr(issuer, "protected_client", absent)
    fake = IngressResult(operation="SUBMIT_REVIEW", canonical_sha="a" * 40)
    assert not fake.source_provenance.reviewer_origin_authenticated
    assert issuer.inspect_sources({}).status == "BLOCK"
    assert issuer.read_source_provenance(issuer.SourceFacts(issuer_authenticated=True)).status == "UNKNOWN"
