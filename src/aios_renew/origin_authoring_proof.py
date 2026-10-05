"""Bounded operational H4C0 proof admission, with no lifecycle authority.

Only the page bootstrap issues proofs. The self-hosted carrier consumes them
under the same registry lock, and the hosted carrier authenticates a signed
receipt. Neither a TASK selector nor a receipt supplied in an Issue is proof.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from .origin_bootstrap import OriginRegistry, NONCE, registry_path
from .return_affinity import OriginAffinity, parse_affinity

PROOF_PREFIX = "origin-authoring-v1:"
PROOF = re.compile(r"origin-authoring-v1:[0-9a-f]{64}")
FORMAT = "AIOS_ORIGIN_AUTHORING_ADMISSION_V1"
MAX_PROOFS = 256
MAX_BYTES = 262144
TTL_SECONDS = 3600
SHA = re.compile(r"[0-9a-f]{40}")
TASK_ID = re.compile(r"TASK-[A-Za-z0-9_-]{1,96}")
ATTEMPT = re.compile(
    r"github-issue:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[1-9][0-9]*"
    r"@[A-Za-z0-9-]+/run:[1-9][0-9]*/attempt:[1-9][0-9]*"
)
BINDING_KEYS = {"carrier_attempt", "task_id", "expected_main_sha", "route_handle",
                "generation", "proof", "envelope_sha256"}
RECEIPT_KEYS = BINDING_KEYS | {"format", "issued_at", "expires_at", "signature"}


class OriginProofError(ValueError):
    """Fixed, content-free failures; no sensitive state in diagnostics."""


def _blocked():
    raise OriginProofError("origin authoring proof admission failed closed")


def _clock():
    return int(time.time())


def _fresh(issued, expires):
    if (type(issued) is not int or type(expires) is not int
            or issued < 1 or expires != issued + TTL_SECONDS
            or not issued <= _clock() < expires):
        _blocked()


def _binding(value):
    if (type(value) is not dict or set(value) != BINDING_KEYS
            or type(value["carrier_attempt"]) is not str
            or len(value["carrier_attempt"]) > 256
            or not ATTEMPT.fullmatch(value["carrier_attempt"])
            or type(value["task_id"]) is not str or not TASK_ID.fullmatch(value["task_id"])
            or type(value["expected_main_sha"]) is not str
            or not SHA.fullmatch(value["expected_main_sha"])
            or type(value["proof"]) is not str or not PROOF.fullmatch(value["proof"])
            or type(value["envelope_sha256"]) is not str
            or not NONCE.fullmatch(value["envelope_sha256"])):
        _blocked()
    try:
        parse_affinity(dict(kind="ORIGIN_AFFINE", route_handle=value["route_handle"],
                            generation=value["generation"]))
    except ValueError:
        _blocked()
    return value


def _encoded(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        _blocked()


def _key(value):
    if type(value) is not str or not NONCE.fullmatch(value):
        _blocked()
    return bytes.fromhex(value)


def _signature(receipt, key):
    body = {k: v for k, v in receipt.items() if k != "signature"}
    return hmac.new(_key(key), b"AIOS\x00ORIGIN_AUTHORING_ADMISSION_V1\x00" + _encoded(body),
                    hashlib.sha256).hexdigest()


def envelope_digest(envelope):
    # The parsed envelope is frozen by semantic ingress before checking again.
    from dataclasses import asdict
    return hashlib.sha256(_encoded(asdict(envelope))).hexdigest()


def binding_for(envelope, carrier_attempt):
    from .authoring_ingress import _get_expected_sha, _payload_to_str
    from .task import parse_task
    if envelope.operation != "AUTHOR_TASK":
        _blocked()
    task = parse_task(_payload_to_str(envelope.payload))
    if (task.revision != 1 or task.task_id != envelope.identity["task_id"]
            or not isinstance(task.return_affinity, OriginAffinity)):
        _blocked()
    predecessors = [envelope.expected_state[k] for k in ("expected_main_sha", "main_sha")
                    if k in envelope.expected_state]
    if (not predecessors or any(type(v) is not str or not SHA.fullmatch(v.strip()) for v in predecessors)
            or len({v.strip() for v in predecessors}) != 1):
        _blocked()
    return _binding(dict(carrier_attempt=carrier_attempt, task_id=task.task_id,
        expected_main_sha=_get_expected_sha(envelope.expected_state, "expected_main_sha", "main_sha"),
        route_handle=task.return_affinity.route_handle, generation=task.return_affinity.generation,
        proof=envelope.origin_authoring_proof, envelope_sha256=envelope_digest(envelope)))


class ProofStore(OriginRegistry):
    """External bounded sidecar; never a semantic record, queue or route resolver."""

    def __init__(self, registry):
        super().__init__(registry.path.with_name(registry.path.name + ".authoring"))

    def validate(self, data):
        registry_path(self.path)
        if (type(data) is not dict or set(data) != {"version", "proofs"}
                or type(data["version"]) is not int or data["version"] != 1
                or type(data["proofs"]) is not dict or len(data["proofs"]) > MAX_PROOFS):
            _blocked()
        for digest, record in data["proofs"].items():
            if (type(digest) is not str or not NONCE.fullmatch(digest)
                    or type(record) is not dict or set(record) != {
                        "route_handle", "generation", "bootstrap_attempt", "issued_at", "expires_at", "admission"}
                    or type(record["bootstrap_attempt"]) is not str
                    or not NONCE.fullmatch(record["bootstrap_attempt"])
                    or type(record["issued_at"]) is not int or record["issued_at"] < 1
                    or type(record["expires_at"]) is not int
                    or record["expires_at"] != record["issued_at"] + TTL_SECONDS):
                _blocked()
            try:
                parse_affinity(dict(kind="ORIGIN_AFFINE", route_handle=record["route_handle"],
                                    generation=record["generation"]))
            except ValueError:
                _blocked()
            receipt = record["admission"]
            if receipt is not None:
                _receipt_shape(receipt)
                if (hashlib.sha256(receipt["proof"].encode("ascii")).hexdigest() != digest
                        or any(receipt[k] != record[k] for k in (
                            "route_handle", "generation", "issued_at", "expires_at"))):
                    _blocked()
        if len(_encoded(data)) > MAX_BYTES:
            _blocked()
        return data

    @contextmanager
    def locked(self):
        # Reuse the registry's external-path, exclusive lock and pending barrier.
        with super().locked() as data:
            if not self.path.exists():
                data = {"version": 1, "proofs": {}}
            yield self.validate(data)


def issue_page_proof(registry, route, bootstrap_attempt):
    """Called only inside bootstrap's exact-document and registry-lock boundary."""
    proof = PROOF_PREFIX + secrets.token_hex(32)
    digest = hashlib.sha256(proof.encode("ascii")).hexdigest()
    store = ProofStore(registry)
    with store.locked() as data:
        if len(data["proofs"]) >= MAX_PROOFS or digest in data["proofs"]:
            _blocked()
        issued = _clock()
        data["proofs"][digest] = dict(route_handle=route["handle"], generation=route["generation"],
            bootstrap_attempt=bootstrap_attempt, issued_at=issued,
            expires_at=issued + TTL_SECONDS, admission=None)
        store.write(data)
    return proof


def _receipt_shape(receipt):
    if (type(receipt) is not dict or set(receipt) != RECEIPT_KEYS
            or receipt["format"] != FORMAT or type(receipt["signature"]) is not str
            or not NONCE.fullmatch(receipt["signature"])
            or type(receipt["issued_at"]) is not int or receipt["issued_at"] < 1
            or type(receipt["expires_at"]) is not int
            or receipt["expires_at"] != receipt["issued_at"] + TTL_SECONDS):
        _blocked()
    _binding({k: receipt[k] for k in BINDING_KEYS})


def admit_local(registry, binding, key):
    """Self-hosted only. Lock order matches bootstrap: registry, then sidecar."""
    _binding(binding)
    _key(key)
    store = ProofStore(registry)
    with registry.locked() as routes, store.locked() as data:
        matches = [r for r in routes["routes"].values() if r["handle"] == binding["route_handle"]]
        digest = hashlib.sha256(binding["proof"].encode("ascii")).hexdigest()
        record = data["proofs"].get(digest)
        if (len(matches) != 1 or record is None
                or record["route_handle"] != binding["route_handle"]
                or record["generation"] != binding["generation"]
                or matches[0]["generation"] != binding["generation"]
                or matches[0]["attempt"] != dict(id=record["bootstrap_attempt"], status="SUBMITTED")):
            _blocked()
        _fresh(record["issued_at"], record["expires_at"])
        receipt = dict(binding, format=FORMAT, issued_at=record["issued_at"], expires_at=record["expires_at"])
        receipt["signature"] = _signature(receipt, key)
        if record["admission"] is not None:
            if record["admission"] != receipt:
                _blocked()  # Includes cross-attempt, body, subject and key drift.
        else:
            record["admission"] = receipt
            store.write(data)  # Consume durably before exporting the receipt.
        registry.current(routes, next(k for k, r in routes["routes"].items() if r is matches[0]))
        return receipt


@dataclass(frozen=True)
class AdmittedOrigin:
    _receipt_json: str = field(repr=False)

    @property
    def receipt(self):
        return json.loads(self._receipt_json)

    def require_envelope(self, envelope):
        receipt = self.receipt
        _receipt_shape(receipt)
        _fresh(receipt["issued_at"], receipt["expires_at"])
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        run_id, attempt = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", "")
        if (not repository or not run_id or not attempt
                or not receipt["carrier_attempt"].startswith(f"github-issue:{repository}#")
                or not receipt["carrier_attempt"].endswith(f"/run:{run_id}/attempt:{attempt}")):
            _blocked()
        # Semantic ingress authenticates again using deployment-owned key
        # material, never a key or admission flag from the TASK/Issue payload.
        if not hmac.compare_digest(receipt["signature"], _signature(
                receipt, os.environ.get("AIOS_ORIGIN_ADMISSION_KEY"))):
            _blocked()
        if binding_for(envelope, receipt["carrier_attempt"]) != {
                k: receipt[k] for k in BINDING_KEYS}:
            _blocked()


def authenticate_receipt(receipt, binding, key):
    """Hosted validation authenticates local admission; it cannot inspect H4C0."""
    _receipt_shape(receipt)
    _binding(binding)
    _fresh(receipt["issued_at"], receipt["expires_at"])
    if ({k: receipt[k] for k in BINDING_KEYS} != binding
            or not hmac.compare_digest(receipt["signature"], _signature(receipt, key))):
        _blocked()
    return AdmittedOrigin(_encoded(receipt).decode("ascii"))


def read_receipt(path):
    # Strict duplicate rejection; only bounded operational selectors are allowed.
    def unique(pairs):
        value = {}
        for k, v in pairs:
            if k in value:
                _blocked()
            value[k] = v
        return value
    try:
        from pathlib import Path
        target = Path(path)
        with target.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            _blocked()
        receipt = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=unique)
        _receipt_shape(receipt)
        return receipt
    except (OSError, ValueError, UnicodeError):
        _blocked()
