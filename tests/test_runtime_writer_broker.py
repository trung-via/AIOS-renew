"""Adversarial broker core checks: temporary objects/refs, never production refs."""
import copy
import subprocess

import pytest

from aios_renew import runtime_writer_broker as broker
from aios_renew.runtime_writer_broker import BrokerError, BrokerService, validate_mutation, RUNTIME_APP, CONTROL_APP


def update(family="artifacts", run="RUN-105-001", old=None, new="a" * 40):
    return {"ref": "refs/heads/aios/" + family + "/" + run, "old": old, "new": new}


@pytest.mark.parametrize("role,operation,updates", [
    ("CONTROL", "TERMINAL_RESULT", [update()]),
    ("RUNTIME", "SUBMIT_REVIEW", [update("review-decision")]),
    ("RUNTIME", "TERMINAL_RESULT", [update("review-decision")]),
    ("RUNTIME", "TERMINAL_RESULT", [update(old="b" * 40)]),
    ("RUNTIME", "TERMINAL_RESULT", [update(new=None)]),
    ("RUNTIME", "TERMINAL_RESULT", [update(), update(run="RUN-105-999")]),
    ("RUNTIME", "TERMINAL_RESULT", [update(run="../../main")]),
    ("RUNTIME", "PUBLISH_REVIEWED", [{"ref": "refs/heads/main", "old": None, "new": "a" * 40}]),
])
def test_wrong_writer_namespace_RUN_or_CAS_is_rejected(role, operation, updates):
    with pytest.raises(BrokerError):
        validate_mutation(role, operation, updates)


@pytest.mark.parametrize("role,operation", [("CONTROL", "RESERVATION_CONTROL"), ("RUNTIME", "RESERVATION_RUNTIME")])
def test_shared_reservation_has_two_owner_roles_with_exact_create_delete_CAS(role, operation):
    ref = "refs/heads/aios/publication-reservation/main"
    validate_mutation(role, operation, [{"ref": ref, "old": None, "new": "a" * 40}])
    validate_mutation(role, operation, [{"ref": ref, "old": "a" * 40, "new": None}])
    with pytest.raises(BrokerError):
        validate_mutation(role, operation, [{"ref": ref, "old": "a" * 40, "new": "b" * 40}])


@pytest.fixture
def core(tmp_path):
    repo = tmp_path / "service.git"
    subprocess.run(["git", "init", "--bare", str(repo)], capture_output=True, check=True)
    return BrokerService(repo=repo, journal=tmp_path / "journal.sqlite", key=b"fixture signing secret" * 2,
        peers={"runtime": "RUNTIME", "control": "CONTROL", "reader": "READER"}, writers={}, raw_root=tmp_path)


def request():
    return {"action": "mutate", "operation": "TERMINAL_RESULT", "updates": [update()], "nonce": "d" * 64}


def test_untrusted_TRUNG_payload_role_and_unknown_peer_are_not_authentication(core):
    payload = {**request(), "role": "RUNTIME", "username": ".\\TRUNG", "issuer_authenticated": True}
    with pytest.raises(BrokerError, match="unauthenticated IPC"):
        core.handle("untrusted", payload)
    with pytest.raises(BrokerError, match="unauthorized writer"):
        core.handle("control", payload)
    assert not core.db.execute("SELECT * FROM operations").fetchall()


def test_missing_or_control_App_runtime_credential_rejected(core):
    with pytest.raises(BrokerError, match="wrong or missing writer"):
        core.handle("runtime", request())
    core.writers["RUNTIME"] = type("WrongWriter", (), {"app_id": CONTROL_APP})()
    with pytest.raises(BrokerError, match="wrong or missing writer"):
        core.handle("runtime", request())
    assert not core.db.execute("SELECT * FROM operations").fetchall()


def test_failed_operation_burns_nonce_before_object_import_and_never_replays(core):
    core.writers["RUNTIME"] = type("Writer", (), {"app_id": RUNTIME_APP})()
    with pytest.raises(BrokerError, match="protected mutation failed"):
        core.handle("runtime", request())
    assert core.db.execute("SELECT state FROM operations").fetchone()[0] == "FAILED"
    with pytest.raises(BrokerError, match="replay"):
        core.handle("runtime", request())
    changed = {**request(), "updates": [update(new="c" * 40)]}
    with pytest.raises(BrokerError, match="replay"):
        core.handle("runtime", changed)


def test_stale_refs_and_main_without_exact_reservation_block_before_push(core, monkeypatch):
    monkeypatch.setattr(core, "_remote_ref", lambda ref: "b" * 40)
    with pytest.raises(BrokerError, match="stale writer CAS"):
        core._mutation_objects("RUNTIME", "TERMINAL_RESULT", [update()], {})
    main = {"ref": "refs/heads/main", "old": "b" * 40, "new": "a" * 40}
    with pytest.raises(BrokerError, match="reservation required"):
        core._mutation_objects("RUNTIME", "PUBLISH_REVIEWED", [main], {})


def test_issuer_role_not_git_content_or_App_identity(core):
    with pytest.raises(BrokerError, match="source issuer role"):
        core.handle("control", {"action": "issue", "kind": "RUNTIME_TERMINAL", "facts": {}})
    with pytest.raises(BrokerError, match="source issuer role"):
        core.handle("runtime", {"action": "issue", "kind": "REVIEW_INGRESS", "facts": {}})


def test_missing_PEM_does_not_mint_or_return_installation_token(tmp_path):
    writer = broker.AppWriter(app_id=RUNTIME_APP, installation_id=1, pem=tmp_path / "missing.pem", repository_id=1)
    with pytest.raises(BrokerError, match="credential unavailable"):
        writer._token()


def test_wrong_installation_permissions_and_unprovisioned_workflow_permission(tmp_path, monkeypatch):
    pem = tmp_path / "fixture.pem"
    pem.write_text("fixture only", encoding="utf-8")
    monkeypatch.setattr(broker, "_protected_file", lambda *args, **kwargs: pem)
    monkeypatch.setattr(broker.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, b"fixture signature", b""))
    class Reply:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, maximum):
            return broker.encoded({"token": "fixture only", "permissions": {"contents": "read", "metadata": "read"},
                "repositories": [{"id": 1, "full_name": broker.REPOSITORY}]})
    class API:
        def open(self, request, **kwargs):
            assert broker.decode(request.data) == {"repository_ids": [1], "permissions": {"contents": "write"}}
            return Reply()
    monkeypatch.setattr(broker.urllib.request, "build_opener", lambda *args: API())
    writer = broker.AppWriter(app_id=RUNTIME_APP, installation_id=1, pem=pem, repository_id=1)
    with pytest.raises(BrokerError, match="permission unavailable"):
        writer._token()
    with pytest.raises(BrokerError, match="workflow permission"):
        writer._token(workflows=True)


def test_runner_owned_or_non_Windows_setup_cannot_be_trust_root(tmp_path):
    config = tmp_path / "client.json"
    config.write_text("{}", encoding="utf-8")
    with pytest.raises((BrokerError, FileNotFoundError)):
        broker._protected_file(config)


def test_TLS_client_pins_service_before_sending_owner_payload(monkeypatch):
    events = []
    class Socket:
        def getpeercert(self, **kwargs):
            return b"wrong service certificate"
    class Connection:
        sock = Socket()
        def __init__(self, *args, **kwargs):
            pass
        def connect(self):
            events.append("connect")
        def request(self, *args):
            events.append("payload leaked")
        def close(self):
            pass
    monkeypatch.setattr(broker.http.client, "HTTPSConnection", Connection)
    client = object.__new__(broker.BrokerClient)
    client.config, client.context = {"port": 12345, "service_certificate_sha256": "a" * 64}, None
    with pytest.raises(BrokerError, match="identity mismatch"):
        client.request({"action": "issue", "protected": "payload"})
    assert events == ["connect"]
