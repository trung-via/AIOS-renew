"""Offline focused checks for the fixed BP8-P2A pre-REVIEW observation."""

from __future__ import annotations

import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess

import pytest

from scripts import bp8_real_reviewer_probe as proof
from aios_renew.reviewer_provider_protocol import revalidate_request, validate_response


SECRET = "secret-native-stderr-must-never-appear"


def semantic_body(verdict: str) -> dict:
    failed = verdict == "CHANGES_REQUIRED"
    return {
        "verdict": verdict,
        "acceptance": [{"id": "AC1", "outcome": "FAIL" if failed else "PASS"}],
        "findings": [{"basis": "AC1", "action": "CODE_FIX",
                      "location": "proof-only/example.txt", "issue": "Synthetic issue",
                      "expected": "Synthetic correction"}] if failed else [],
    }


class NativeRunner:
    def __init__(self, *, mode: str = "success", verdict: str = "PASS") -> None:
        self.mode = mode
        self.verdict = verdict
        self.calls: list[tuple[tuple[str, ...], dict]] = []
        self.request_bytes: list[bytes] = []

    def __call__(self, command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
        self.calls.append((command, kwargs))
        request = (Path(str(kwargs["cwd"])) / "reviewer_request.json").read_bytes()
        self.request_bytes.append(request)
        if self.mode == "transport":
            raise OSError(SECRET)
        if self.mode == "unexpected":
            raise RuntimeError(SECRET)
        parsed = revalidate_request(request)
        if self.mode == "response":
            return subprocess.CompletedProcess(command, 0, b"{invalid", SECRET.encode())
        fingerprint = parsed["request_fingerprint"]
        if self.mode == "semantic":
            fingerprint = "0" * 64
        payload = {
            "status": "SUCCESS",
            "structured_output": {"request_fingerprint": fingerprint,
                                  "semantic_body": semantic_body(self.verdict)},
            "usage": {"input_tokens": 120, "output_tokens": 30,
                      "cached_input_tokens": 20},
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(),
                                           SECRET.encode())


def invoke(args: list[str], runner: NativeRunner) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = proof.main(args, runner=runner, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_fresh_proof_request_is_deterministic_and_self_contained() -> None:
    first, second = proof.construct_proof_request(), proof.construct_proof_request()
    assert first == second
    assert proof._json(first) == proof._json(second)
    assert first["request_fingerprint"] == second["request_fingerprint"]
    assert revalidate_request(proof._json(first)) == first
    assert first["review_scope"]["review_mode"] == "PRIMARY"
    assert first["prior_review"] is None
    assert first["decision_packet"]["subject"]["run_id"].startswith("PROOF-")
    assert first["external_bindings"]["review_id"].startswith("PROOF-")
    assert first["decision_packet"]["subject"]["head_sha"] != first["decision_packet"]["subject"]["base_sha"]


@pytest.mark.parametrize("args", [
    [], ["--effort"], ["high"], ["--effort", "medium"],
    ["--effort", "high", "--effort", "high"],
    ["high", "--effort"], ["--effort", "high", "extra"],
])
def test_cli_exact_effort_admission_precedes_native_call(args: list[str]) -> None:
    runner = NativeRunner()
    code, out, err = invoke(args, runner)
    assert (code, out, err) == (2, "", "PROBE_INTEGRITY_FAILURE\n")
    assert runner.calls == []


def test_internal_effort_rejects_other_values() -> None:
    runner = NativeRunner()
    for value in ("medium", "low", "", None):
        with pytest.raises(proof.ProbeIntegrityError):
            proof.probe(value, runner=runner)
    assert runner.calls == []


@pytest.mark.parametrize("verdict", ["PASS", "CHANGES_REQUIRED", "BLOCKED"])
def test_validated_decision_uses_one_native_call_and_exact_request_bytes(verdict: str) -> None:
    runner = NativeRunner(verdict=verdict)
    code, out, err = invoke(["--effort", "high"], runner)
    assert (code, err) == (0, "")
    assert len(runner.calls) == len(runner.request_bytes) == 1
    command, kwargs = runner.calls[0]
    assert command[command.index("--model") + 1] == "gemini-3.8-flash"
    assert command[command.index("--effort") + 1] == "high"
    assert "env" not in kwargs
    request = revalidate_request(runner.request_bytes[0])
    expected_decision = validate_response(request, {
        "request_fingerprint": request["request_fingerprint"],
        "semantic_body": semantic_body(verdict),
    })
    observation = json.loads(out)
    assert set(observation) == proof._FIELDS
    assert observation == {
        "format": proof.FORMAT, "version": 1, "kind": proof.KIND,
        "outcome": "VALIDATED_DECISION", "provider": "antigravity",
        "model": "gemini-3.8-flash", "effort": "high", "invocation_count": 1,
        "request_fingerprint": request["request_fingerprint"],
        "request_sha256": hashlib.sha256(runner.request_bytes[0]).hexdigest(),
        "decision_fingerprint": expected_decision["decision_fingerprint"],
        "failure_reason_code": None, "failure_phase": None,
        "native_usage": {"input_tokens": 120, "output_tokens": 30,
                         "cached_input_tokens": 20},
    }
    assert len(out.encode()) <= proof.MAX_OBSERVATION_BYTES
    assert "review_candidate" not in out and "semantic_body" not in out
    assert runner.request_bytes[0].decode() not in out
    assert SECRET not in out + err


@pytest.mark.parametrize("mode,reason,phase,has_usage", [
    ("transport", "PROVIDER_TRANSPORT_FAILURE", "INVOKE", False),
    ("response", "PROVIDER_TRANSPORT_FAILURE", "INVOKE", False),
    ("semantic", "PROVIDER_RESPONSE_INVALID", "RESPONSE", True),
])
def test_provider_failures_are_bounded_exit_zero_observations(
    mode: str, reason: str, phase: str, has_usage: bool,
) -> None:
    runner = NativeRunner(mode=mode)
    code, out, err = invoke(["--effort", "high"], runner)
    assert (code, err, len(runner.calls)) == (0, "", 1)
    observation = json.loads(out)
    assert set(observation) == proof._FIELDS
    assert observation["outcome"] == "PROVIDER_FAILURE"
    assert observation["failure_reason_code"] == reason
    assert observation["failure_phase"] == phase
    assert observation["decision_fingerprint"] is None
    assert (observation["native_usage"] is not None) is has_usage
    assert observation["request_sha256"] == hashlib.sha256(runner.request_bytes[0]).hexdigest()
    assert len(out.encode()) <= proof.MAX_OBSERVATION_BYTES
    assert SECRET not in out + err
    assert runner.request_bytes[0].decode() not in out


def test_unexpected_native_exception_is_probe_integrity_failure() -> None:
    runner = NativeRunner(mode="unexpected")
    assert invoke(["--effort", "high"], runner) == (
        2, "", "PROBE_INTEGRITY_FAILURE\n")
    assert len(runner.calls) == 1


def test_malformed_input_and_request_identity_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = NativeRunner()
    original = proof.construct_proof_request
    monkeypatch.setattr(proof, "construct_proof_request", lambda: (_ for _ in ()).throw(
        proof.ProbeIntegrityError("malformed fixed input")))
    assert invoke(["--effort", "high"], runner)[0:2] == (2, "")
    assert runner.calls == []
    monkeypatch.setattr(proof, "construct_proof_request", original)
    def corrupted() -> dict:
        value = original()
        value["request_fingerprint"] = "0" * 64
        return value
    monkeypatch.setattr(proof, "construct_proof_request", corrupted)
    assert invoke(["--effort", "high"], runner)[0:2] == (2, "")
    assert runner.calls == []


def test_two_provider_invocations_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    original = proof.attempt
    def twice(provider: object, *parts: object) -> object:
        from aios_renew.reviewer_provider_protocol import construct_request
        request = construct_request(*parts)
        provider.invoke(request)
        return original(provider, *parts)
    monkeypatch.setattr(proof, "attempt", twice)
    runner = NativeRunner()
    code, out, err = invoke(["--effort", "high"], runner)
    assert (code, out, err) == (2, "", "PROBE_INTEGRITY_FAILURE\n")
    assert len(runner.calls) == 1


def test_observation_contract_rejects_extra_fields_and_excess_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = NativeRunner()
    observed = proof.probe("high", runner=runner)
    with pytest.raises(proof.ProbeIntegrityError):
        proof._observation(**{**observed, "raw_response": SECRET})
    with pytest.raises(proof.ProbeIntegrityError):
        proof._observation(**{**observed, "decision_fingerprint": "x" * 9000})
    monkeypatch.setattr(proof, "MAX_OBSERVATION_BYTES", 1)
    with pytest.raises(proof.ProbeIntegrityError, match="bound"):
        proof._observation(**observed)


def test_no_forbidden_authority_import_or_submission_surface() -> None:
    source = Path(proof.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "runtime", "verification", "authoring_ingress", "publication",
        "review_transport", "codex_adapter", "antigravity_adapter",
        "execution_profile", "operator", "unified_state",
    }
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names]
    assert not any(part in forbidden for name in imports if name for part in name.split("."))
    assert not ({"submit_review", "publish", "remediate", "repair", "retry"}
                & {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)})
    assert "os.environ" not in source and "getenv" not in source
