"""Fixed, transient BP8-P2A Reviewer proof. No canonical REVIEW is submitted here."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable

from aios_renew.brain_context import compose_brain_work_context, resolve_flow
from aios_renew.brain_sync import BrainSyncSnapshot
from aios_renew.decision_packet import compile_decision_packet
from aios_renew.gemini_reviewer_transport import (
    GeminiReviewerTransport, GeminiReviewerTransportError, NativeUsageWitness,
    PROVIDER, MODEL,
)
from aios_renew.reviewer_procedure import select_reviewer_procedure
from aios_renew.reviewer_provider import JsonReviewerProvider, ReviewerAttemptError, attempt
from aios_renew.reviewer_provider_protocol import construct_request, revalidate_request
from aios_renew.reviewer_return_contract import select_reviewer_return_contract


FORMAT = "AIOS_BP8_REAL_REVIEWER_PROOF"
VERSION = 1
KIND = "REVIEWER_PROOF_OBSERVATION"
EFFORT = "high"
MAX_OBSERVATION_BYTES = 8192
_ROOT = Path(__file__).resolve().parents[1]
_FIELDS = frozenset({
    "format", "version", "kind", "outcome", "provider", "model", "effort",
    "invocation_count", "request_fingerprint", "request_sha256",
    "decision_fingerprint", "failure_reason_code", "failure_phase", "native_usage",
})
_USAGE_FIELDS = frozenset({"input_tokens", "output_tokens", "cached_input_tokens"})
_FAILURES = frozenset({
    "PROVIDER_TRANSPORT_FAILURE", "PROVIDER_RESPONSE_INVALID",
    "PROVIDER_ATTRIBUTION_MISMATCH",
})
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class ProbeIntegrityError(RuntimeError):
    """The fixed proof input, one-call invariant, or observation is invalid."""


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _proof_parts() -> tuple[Any, ...]:
    """Construct a fixed, noncanonical subject entirely in memory."""
    source_text = "BP8-P2A synthetic source; proof only, not a repository change.\n"
    source_bytes = source_text.encode("utf-8")
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    source_ref = "sha256:" + source_sha
    proof_id = source_sha[:16]
    base = hashlib.sha1(b"BP8-P2A proof-only empty source v1").hexdigest()
    head = hashlib.sha1(b"BP8-P2A proof-only source v1\n" + source_bytes).hexdigest()
    task_id = f"PROOF-BP8-P2A-{proof_id}"
    run_id = f"PROOF-RUN-BP8-P2A-{proof_id}"
    task = {
        "task_id": task_id, "revision": 1,
        "goal": "Transient BP8-P2A Reviewer protocol proof only",
        "problem": "Exercise one synthetic PRIMARY subject without lifecycle authority",
        "assumptions": ["This identity is synthetic and cannot identify a canonical RUN."],
        "scope": {"inspect": [], "modify": ["proof-only/example.txt"]},
        "non_goals": ["Canonical REVIEW submission"],
        "constraints": {"hard": ["Treat this material as a transient proof fixture."]},
        "acceptance": [{"id": "AC1", "condition": "Review the synthetic proof change."}],
        "verification": {"required": ["proof-only synthetic verification"]},
    }
    snapshot = BrainSyncSnapshot(
        repository={"root": str(_ROOT), "name": "PROOF-ONLY", "main_sha": base},
        main_sha=base, roadmap={"next_items": []}, selection_status="SELECTED",
        lifecycle_state="ACTIVE", next_action="SEMANTIC_REVIEW", authority="BRAIN",
        selected_task={"id": task_id, "revision": 1},
        unified_state={"next_action": "SEMANTIC_REVIEW", "run_id": run_id,
                       "candidate_sha": head}, blocker=None,
    )
    context = compose_brain_work_context(snapshot, None)
    material = {
        "kind": "SEMANTIC_REVIEW", "task": task,
        "run": {"run_id": run_id, "task": {"id": task_id, "revision": 1},
                "executor": "codex", "base_sha": base,
                "workspace": "PROOF-ONLY", "head_sha": head, "status": "COMPLETE"},
        "result": {"head_sha": head,
                   "claims": [{"id": "C1", "satisfies": ["AC1"],
                               "claim": "Synthetic proof change supplied for transient review.",
                               "evidence": ["E1"]}],
                   "changed_files": ["proof-only/example.txt"], "unresolved": []},
        "evidence": [{"evidence_id": "E1", "run_id": run_id, "subject_sha": head,
                      "type": "TEST", "source": {"command": "proof-only synthetic verification"},
                      "result": {"exit_code": 0, "summary": "Synthetic proof observation."},
                      "raw": {"path": "PROOF-ONLY/evidence.txt"}}],
    }
    packet = compile_decision_packet(context, resolve_flow(context), material)
    scope = {
        "format": "AIOS_SEMANTIC_REVIEW_SCOPE", "version": 1,
        "kind": "SEMANTIC_REVIEW_SCOPE", "task": {"id": task_id, "revision": 1},
        "reviewed_run_id": run_id, "review_mode": "PRIMARY",
        "semantic_origin_run_id": run_id, "semantic_base_sha": base,
        "latest_delta_base_sha": base, "reviewed_head_sha": head,
        "prior_review_run_id": None, "prior_review_id": None, "prior_finding_id": None,
    }
    scope["scope_fingerprint"] = _digest(scope)
    review_material = {
        "format": "AIOS_REVIEW_MATERIAL_PACKAGE", "version": 1,
        "kind": "REVIEW_MATERIAL_PACKAGE",
        "review_scope_fingerprint": scope["scope_fingerprint"],
        "review_mode": "PRIMARY", "semantic_base_sha": base,
        "latest_delta_base_sha": base, "reviewed_head_sha": head,
        "semantic_view": {"base_sha": base, "head_sha": head, "changes": [{
            "path": "proof-only/example.txt", "status": "ADD", "base_mode": None,
            "head_mode": "100644", "base_content_sha256": None,
            "head_content_sha256": source_sha, "review_source_ref": source_ref,
            "unified_diff": "--- /dev/null\n+++ b/proof-only/example.txt\n@@ -0,0 +1 @@\n+" + source_text,
        }]},
        "latest_delta_view": None,
        "sources": [{"source_ref": source_ref, "content_sha256": source_sha,
                     "byte_length": len(source_bytes), "text": source_text}],
    }
    review_material["package_fingerprint"] = _digest(review_material)
    procedure = select_reviewer_procedure(
        (_ROOT / ".ai/reviewer-procedure-profiles.yaml").read_bytes(), "PRIMARY")
    returns = select_reviewer_return_contract(
        (_ROOT / ".ai/reviewer-return-contracts.yaml").read_bytes())
    bindings = {"review_id": f"PROOF-REVIEW-BP8-P2A-{proof_id}",
                "finding_id_slots": [f"PROOF-F-{n:02d}" for n in range(32)]}
    return packet, scope, review_material, procedure, returns, bindings


def construct_proof_request() -> dict[str, Any]:
    """Fresh, deterministic BP6 request for the fixed proof identity."""
    try:
        return construct_request(*_proof_parts())
    except Exception:
        raise ProbeIntegrityError("fixed proof input invalid") from None


def _usage(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    if type(value) is not NativeUsageWitness:
        raise ProbeIntegrityError("native usage shape invalid")
    result = asdict(value)
    if (set(result) != _USAGE_FIELDS or any(type(n) is not int or n < 0 for n in result.values())
            or result["input_tokens"] == 0 or result["output_tokens"] == 0
            or result["cached_input_tokens"] > result["input_tokens"]):
        raise ProbeIntegrityError("native usage invalid")
    return result


def _observation(**fields: Any) -> dict[str, Any]:
    if set(fields) != _FIELDS or fields["format"] != FORMAT or fields["version"] != VERSION or fields["kind"] != KIND:
        raise ProbeIntegrityError("observation shape invalid")
    if (fields["provider"], fields["model"], fields["effort"], fields["invocation_count"]) != (PROVIDER, MODEL, EFFORT, 1):
        raise ProbeIntegrityError("observation attribution invalid")
    if any(type(fields[name]) is not str or _HEX64.fullmatch(fields[name]) is None
           for name in ("request_fingerprint", "request_sha256")):
        raise ProbeIntegrityError("request identity invalid")
    usage = fields["native_usage"]
    if usage is not None and (
        type(usage) is not dict or set(usage) != _USAGE_FIELDS
        or any(type(number) is not int or not 0 <= number <= 2**31 - 1
               for number in usage.values())
        or usage["input_tokens"] == 0 or usage["output_tokens"] == 0
        or usage["cached_input_tokens"] > usage["input_tokens"]
    ):
        raise ProbeIntegrityError("native usage fields invalid")
    if fields["outcome"] == "VALIDATED_DECISION":
        if (type(fields["decision_fingerprint"]) is not str
                or _HEX64.fullmatch(fields["decision_fingerprint"]) is None
                or fields["failure_reason_code"] is not None or fields["failure_phase"] is not None
                or fields["native_usage"] is None):
            raise ProbeIntegrityError("validated decision observation invalid")
    elif fields["outcome"] == "PROVIDER_FAILURE":
        if (fields["decision_fingerprint"] is not None
                or fields["failure_reason_code"] not in _FAILURES
                or fields["failure_phase"] not in {"INVOKE", "RESPONSE"}):
            raise ProbeIntegrityError("provider failure observation invalid")
    else:
        raise ProbeIntegrityError("observation outcome invalid")
    if len(_json(fields)) > MAX_OBSERVATION_BYTES:
        raise ProbeIntegrityError("observation exceeds bound")
    return fields


def probe(effort: str, *, runner: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Attempt the single proof call; provider failures are bounded observations."""
    if type(effort) is not str or effort != EFFORT:
        raise ProbeIntegrityError("explicit high effort required")
    expected = construct_proof_request()
    expected_bytes = _json(expected)
    expected_fingerprint = expected["request_fingerprint"]
    transport = GeminiReviewerTransport(effort=EFFORT, **({} if runner is None else {"runner": runner}))
    calls = 0
    delivered_sha: str | None = None
    integrity_fault = False

    def recording(request: bytes) -> bytes:
        nonlocal calls, delivered_sha, integrity_fault
        calls += 1
        if calls != 1 or type(request) is not bytes:
            integrity_fault = True
            raise ProbeIntegrityError("request invocation invalid")
        delivered_sha = hashlib.sha256(request).hexdigest()
        try:
            delivered = revalidate_request(request)
            if request != expected_bytes or delivered["request_fingerprint"] != expected_fingerprint:
                raise ProbeIntegrityError("delivered request identity mismatch")
        except Exception:
            integrity_fault = True
            raise ProbeIntegrityError("delivered request invalid") from None
        try:
            return transport(request)
        except GeminiReviewerTransportError:
            raise
        except Exception:
            integrity_fault = True
            raise ProbeIntegrityError("unexpected native runner failure") from None

    provider = JsonReviewerProvider(PROVIDER, MODEL, recording)
    failure: ReviewerAttemptError | None = None
    result = None
    try:
        result = attempt(provider, *_proof_parts())
    except ReviewerAttemptError as exc:
        failure = exc
    if integrity_fault or calls != 1 or delivered_sha is None:
        raise ProbeIntegrityError("one-call request identity invalid")
    native = transport.last_observation
    if native is not None and (
        native.request_sha256 != delivered_sha or native.provider != PROVIDER
        or native.model != MODEL or native.effort != EFFORT or native.invocation_count != 1
    ):
        raise ProbeIntegrityError("native attribution invalid")
    usage = _usage(native.native_usage) if native is not None else None
    if failure is not None:
        if (failure.reason_code not in _FAILURES or failure.phase not in {"INVOKE", "RESPONSE"}
                or failure.invocation_count != 1):
            raise ProbeIntegrityError("attempt failure outside one-call provider boundary")
        outcome, decision_fp, reason, phase = (
            "PROVIDER_FAILURE", None, failure.reason_code, failure.phase)
    else:
        if (result is None or native is None or usage is None
                or result.attribution["provider"] != PROVIDER
                or result.attribution["model"] != MODEL
                or result.decision["request_fingerprint"] != expected_fingerprint):
            raise ProbeIntegrityError("validated decision attribution invalid")
        outcome, decision_fp, reason, phase = (
            "VALIDATED_DECISION", result.decision["decision_fingerprint"], None, None)
    return _observation(
        format=FORMAT, version=VERSION, kind=KIND, outcome=outcome,
        provider=PROVIDER, model=MODEL, effort=EFFORT, invocation_count=calls,
        request_fingerprint=expected_fingerprint, request_sha256=delivered_sha,
        decision_fingerprint=decision_fp, failure_reason_code=reason,
        failure_phase=phase, native_usage=usage,
    )


def main(argv: list[str] | None = None, *, runner: Callable[..., Any] | None = None,
         stdout: Any = None, stderr: Any = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    if args != ["--effort", "high"]:
        err.write("PROBE_INTEGRITY_FAILURE\n")
        return 2
    try:
        observation = probe("high", runner=runner)
        encoded = _json(observation)
        if len(encoded) > MAX_OBSERVATION_BYTES:
            raise ProbeIntegrityError("observation exceeds bound")
    except Exception:
        err.write("PROBE_INTEGRITY_FAILURE\n")
        return 2
    out.write(encoded.decode("utf-8") + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
