"""Prospective terminal/review receipts from the existing admitted owner paths.

Python seals only prevent accidental constructor/fixture enrollment. Independent
authentication comes exclusively from the protected mTLS service, whose approved
Runtime/Control callers are separate from the untrusted Executor account.
"""
from __future__ import annotations

import hashlib
import base64
import re
import sys
import weakref
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .runtime_writer_broker import BrokerError, decode, digest, encoded, metadata, protected_client


@dataclass(frozen=True)
class SourceFacts:
    status: str = "UNKNOWN"
    gaps: tuple[str, ...] = ("INDEPENDENT_ISSUER_UNAVAILABLE",)
    issuer_authenticated: bool = False
    reviewer_origin_authenticated: bool = False
    reviewer_authority: bool = False
    raw_state: str = "RAW_UNAVAILABLE"
    bindings: tuple = ()


def validate_source_facts(kind, facts):
    common = {"task_id", "task_revision", "task_blob_sha", "task_authoring_commit_sha", "admission_operation", "admitted_run_sha256", "run_id", "base_sha", "candidate_sha",
              "candidate_tree_sha", "artifact_sha", "artifact_ref", "run", "profile", "result", "evidence"}
    extra = ({"local_run", "local_result", "local_evidence", "raw"} if kind == "RUNTIME_TERMINAL"
             else {"decision_sha", "decision_ref", "review", "recorded_verdict"})
    if type(facts) is not dict or set(facts) != common | extra:
        raise BrokerError("source fields mismatch")
    if (type(facts["task_id"]) is not str or not re.fullmatch(r"TASK-[A-Za-z0-9_-]+", facts["task_id"])
            or type(facts["task_revision"]) is not int or facts["task_revision"] < 1
            or type(facts["run_id"]) is not str or not re.fullmatch(r"RUN-[A-Za-z0-9_-]+-\d{3,}", facts["run_id"])):
        raise BrokerError("source TASK/RUN identity invalid")
    if facts["admission_operation"] != "PRIMARY" or not re.fullmatch(r"[0-9a-f]{64}", facts["admitted_run_sha256"]):
        raise BrokerError("source admission binding invalid")
    for key in ("task_blob_sha", "task_authoring_commit_sha", "base_sha", "candidate_sha", "candidate_tree_sha", "artifact_sha"):
        if type(facts[key]) is not str or not re.fullmatch(r"[0-9a-f]{40}", facts[key]):
            raise BrokerError("source SHA invalid")
    if facts["artifact_ref"] != "refs/heads/aios/artifacts/" + facts["run_id"]:
        raise BrokerError("source artifact ref mismatch")
    for key in ("run", "profile", "result", "evidence", "local_run", "local_result", "local_evidence", "review"):
        if key in facts:
            value = facts[key]
            if (type(value) is not dict or set(value) != {"sha256", "size"}
                    or type(value["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
                    or type(value["size"]) is not int or not 0 <= value["size"] <= 1048576):
                raise BrokerError("source immutable metadata invalid")
    if kind == "RUNTIME_TERMINAL":
        if type(facts["raw"]) is not list or len(facts["raw"]) > 1024:
            raise BrokerError("raw metadata bound invalid")
        seen = set()
        for item in facts["raw"]:
            if (type(item) is not dict or set(item) != {"evidence_id", "sha256", "size"}
                    or type(item["evidence_id"]) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", item["evidence_id"])
                    or item["evidence_id"] in seen or type(item["sha256"]) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                    or type(item["size"]) is not int or not 0 <= item["size"] <= 2**63 - 1):
                raise BrokerError("raw immutable metadata invalid")
            seen.add(item["evidence_id"])
    elif (not re.fullmatch(r"[0-9a-f]{40}", facts["decision_sha"])
          or facts["decision_ref"] != "refs/heads/aios/review-decision/" + facts["run_id"]
          or facts["recorded_verdict"] not in {"PASS", "CHANGES_REQUIRED"}):
        raise BrokerError("review decision invalid")


def public_result(content):
    """Preserve EVIDENCE schema with opaque unavailable locators, never raw files."""
    data = decode(content)
    for item in data.get("evidence", []):
        identity = item["evidence_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", identity):
            raise BrokerError("unsafe evidence identity")
        item["raw"] = {"path": "protected-runtime-raw/" + identity}
        item["result"]["summary"] = "Runtime verification exit code " + str(item["result"]["exit_code"])
    return encoded(data)


def public_run(content):
    data = decode(content)
    run = data.get("execution", {}).get("run") if data.get("kind") == "REMEDIATION" else data
    if isinstance(run, dict) and "workspace" in run:
        run["workspace"] = "protected-runtime-workspace"
    return encoded(data)


def public_metadata(content):
    """Keep embedded RUN/privacy views coherent across correction transport."""
    def safe(value):
        if isinstance(value, list):
            return [safe(item) for item in value]
        if not isinstance(value, dict):
            return value
        data = {key: safe(item) for key, item in value.items()}
        if "run_id" in data and "workspace" in data and "task" in data:
            data["workspace"] = "protected-runtime-workspace"
        if "evidence_id" in data and "raw" in data:
            identity = data["evidence_id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", identity):
                raise BrokerError("unsafe evidence identity")
            data["raw"] = {"path": "protected-runtime-raw/" + identity}
            data["result"]["summary"] = "Runtime verification exit code " + str(data["result"]["exit_code"])
        if data.get("kind") == "FAILURE" and isinstance(data.get("error"), dict):
            error = data["error"]
            error["message"] = "Protected Runtime diagnostics remain local."
            error.pop("native_diagnostics", None)
            error.pop("executor_diagnostics", None)
            for item in error.get("verification", []):
                item["summary"] = "Runtime verification exit code " + str(item["exit_code"])
        return data
    return encoded(safe(decode(content)))


def _frame(module, function):
    frame = sys._getframe(2)
    owner_module = sys.modules.get("aios_renew." + module)
    if owner_module is None or frame.f_globals is not vars(owner_module):
        return None
    target = getattr(owner_module, function, None)
    code = target.__code__ if target else None
    if frame.f_code is code:
        return frame
    if module == "runtime" and function == "complete":
        if frame.f_code is owner_module.RuntimeCompletion.complete.__code__:
            return frame
    return None


def _pack(repo, targets):
    import base64
    import subprocess
    result = subprocess.run(["git", "-C", str(repo), "pack-objects", "--stdout", "--revs"],
                            input=("\n".join(targets) + "\n").encode(), capture_output=True, check=False)
    if result.returncode or len(result.stdout) > 64 * 1024 * 1024:
        raise BrokerError("source object transfer unavailable")
    return base64.b64encode(result.stdout).decode()


def _channels():
    prepared, issued, contexts = {}, {}, {}

    def retain(table, owner, value):
        identity = id(owner)
        table[identity] = (weakref.ref(owner, lambda unused: table.pop(identity, None)), value)

    def lookup(table, owner):
        entry = table.get(id(owner))
        return entry[1] if entry and entry[0]() is owner else None

    def prepare(owner, package, result_path):
        frame = _frame("runtime", "complete")
        if (frame is None or frame.f_locals.get("self") is not owner
                or frame.f_locals.get("canonical_package") is not package):
            return None
        from .runtime_provenance_owner import read_owner_provenance, State
        from .runtime import result_package_data
        observation = read_owner_provenance(owner)
        pins = dict(observation.bindings)
        # Unsupported recovery/historical/direct sources never acquire origin.
        if (observation.kind != "PRIMARY" or observation.status == State.BLOCK
                or pins.get("guard") != "PRIMARY_OWNER_LEASE"
                or any(k not in pins for k in ("task_authoring_commit_sha", "profile_sha256", "task_blob_sha", "run_sha256"))):
            return None
        try:
            canonical = result_path.read_bytes()
            if decode(canonical) != result_package_data(package):
                raise BrokerError("canonical terminal changed")
            profile_path = (owner.state.execution_profiles or owner.state.root / "execution-profiles") / (owner.run.run_id + ".json")
            profile = profile_path.read_bytes()
            if digest(encoded(decode(profile))) != pins["profile_sha256"]:
                raise BrokerError("admitted profile changed")
            paths, raw = [], []
            for item in package.evidence:
                if item.run_id != owner.run.run_id or item.subject_sha != package.result.head_sha:
                    raise BrokerError("evidence identity changed")
                path = (owner.repo / item.raw_path).resolve(strict=True)
                if not path.is_relative_to(owner.state.verification.resolve()):
                    raise BrokerError("raw outside authorized Runtime state")
                hasher, size = hashlib.sha256(), 0
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        hasher.update(chunk)
                        size += len(chunk)
                paths.append({"evidence_id": item.evidence_id, "path": str(path)})
                raw.append({"evidence_id": item.evidence_id, "sha256": hasher.hexdigest(), "size": size})
            public = public_result(canonical)
            local_run = owner.run_path.read_bytes()
            if digest(local_run) != pins["run_sha256"]:
                raise BrokerError("admitted RUN changed")
            facts = dict(task_id=owner.task.task_id, task_revision=owner.task.revision,
                         task_blob_sha=pins["task_blob_sha"], task_authoring_commit_sha=pins["task_authoring_commit_sha"],
                         admission_operation="PRIMARY", admitted_run_sha256=pins["run_sha256"], run_id=owner.run.run_id,
                         base_sha=owner.run.base_sha, candidate_sha=package.result.head_sha,
                         candidate_tree_sha=owner._git("rev-parse", package.result.head_sha + "^{tree}"),
                         artifact_sha="", artifact_ref="refs/heads/aios/artifacts/" + owner.run.run_id,
                         run=metadata(public_run(local_run)), profile=metadata(profile),
                         result=metadata(public), evidence=metadata(encoded(decode(public)["evidence"])),
                         local_run=metadata(local_run), local_result=metadata(canonical),
                         local_evidence=metadata(encoded(decode(canonical)["evidence"])), raw=raw)
            ticket = object()
            retain(prepared, owner, (ticket, facts, paths, result_path, metadata(canonical)))
            return ticket
        except Exception:
            retain(issued, owner, SourceFacts(status="BLOCK", gaps=("TERMINAL_BINDING_MISMATCH",)))
            return None

    def finish(owner, ticket, artifact_sha):
        frame = _frame("runtime", "complete")
        if frame is None or frame.f_locals.get("self") is not owner:
            return
        entry = lookup(prepared, owner)
        if not entry or entry[0] is not ticket:
            return
        _, facts, paths, result_path, pin = entry
        try:
            if metadata(result_path.read_bytes()) != pin:
                raise BrokerError("terminal changed before issuance")
            facts = {**facts, "artifact_sha": artifact_sha}
            validate_source_facts("RUNTIME_TERMINAL", facts)
            client = protected_client()
            response = client.request({"action": "issue", "kind": "RUNTIME_TERMINAL", "facts": facts,
                                       "paths": paths, "original_result": base64.b64encode(result_path.read_bytes()).decode(),
                                       "original_run": base64.b64encode(owner.run_path.read_bytes()).decode(),
                                       "pack": _pack(owner.transport_repo, [artifact_sha, facts["candidate_sha"]])})
            if response["record"]["facts"] != facts:
                raise BrokerError("service terminal binding mismatch")
            retain(issued, owner, SourceFacts(gaps=("INDEPENDENT_CURRENTNESS_UNAVAILABLE", "RAW_UNAVAILABLE"),
                                       issuer_authenticated=True, bindings=tuple(sorted(facts.items()))))
            retain(contexts, owner, ("runtime", owner.run, owner.task, digest(encoded(asdict(owner.task))),
                                    owner.repo, owner.transport_repo, owner.state, owner.run_path))
        except BrokerError:
            retain(issued, owner, SourceFacts())
        except Exception:
            retain(issued, owner, SourceFacts(status="BLOCK", gaps=("TERMINAL_BINDING_MISMATCH",)))

    def review(result):
        frame = _frame("authoring_ingress", "_execute_submit_review")
        if frame is None or frame.f_locals.get("ingress_result") is not result:
            return
        local = frame.f_locals
        try:
            from . import authoring_ingress as ingress
            client = protected_client()
            run_id = local["run_id"]
            # Replay reads existing authenticated issuance only; never mints history.
            if result.replayed:
                record = client.request({"action": "read", "identity": "REVIEW_INGRESS:" + run_id})["record"]
                if record is None:
                    return
                facts = record["facts"]
                if (facts["decision_sha"] != result.canonical_sha or facts["candidate_sha"] != local["candidate_sha"]
                        or facts["review"] != metadata(local["review_bytes"])):
                    raise BrokerError("replayed review source mismatch")
            else:
                repo, run, task = local["repo"], local["run"], local["task"]
                source = client.request({"action": "read", "identity": "RUNTIME_TERMINAL:" + run_id})["record"]
                if source is None:
                    return
                facts = {k: v for k, v in source["facts"].items()
                         if k not in {"local_run", "local_result", "local_evidence", "raw"}}
                code, blob, _ = ingress._git(repo, "rev-parse", f"{local['candidate_sha']}:.ai/tasks/{task.task_id}.yaml")
                if (code or blob.strip() != facts["task_blob_sha"] or run.task.id != facts["task_id"]
                        or run.task.revision != facts["task_revision"] or run.base_sha != facts["base_sha"]
                        or local["candidate_sha"] != facts["candidate_sha"] or local["artifacts_sha"] != facts["artifact_sha"]
                        or metadata(local["run_bytes"]) != facts["run"] or metadata(local["result_bytes"]) != facts["result"]):
                    raise BrokerError("review Runtime source mismatch")
                facts.update(decision_sha=result.canonical_sha, decision_ref=result.canonical_destination,
                             review=metadata(local["review_bytes"]), recorded_verdict=local["review"].verdict)
                response = client.request({"action": "issue", "kind": "REVIEW_INGRESS", "facts": facts,
                                           "pack": _pack(repo, [result.canonical_sha])})
                if response["record"]["facts"] != facts:
                    raise BrokerError("service review binding mismatch")
            retain(issued, result, SourceFacts(gaps=("INDEPENDENT_CURRENTNESS_UNAVAILABLE", "RAW_UNAVAILABLE"),
                                        reviewer_origin_authenticated=True, bindings=tuple(sorted(facts.items()))))
            retain(contexts, result, ("review", result.as_dict()))
        except BrokerError:
            return
        except Exception:
            retain(issued, result, SourceFacts(status="BLOCK", gaps=("REVIEW_BINDING_MISMATCH",)))

    def read(owner):
        facts = lookup(issued, owner)
        if facts is None:
            return SourceFacts()
        from .runtime import RuntimeCompletion
        from .authoring_ingress import IngressResult
        pins = dict(facts.bindings)
        if pins:
            try:
                if type(owner) is RuntimeCompletion:
                    context = lookup(contexts, owner)
                    if (not context or owner.run is not context[1] or owner.task is not context[2]
                            or digest(encoded(asdict(owner.task))) != context[3]
                            or (owner.repo, owner.transport_repo, owner.state, owner.run_path) != context[4:]
                            or decode(owner.run_path.read_bytes()) != asdict(owner.run)
                            or owner.run.run_id != pins["run_id"] or owner.run.base_sha != pins["base_sha"]
                            or owner.task.task_id != pins["task_id"] or owner.task.revision != pins["task_revision"]
                            or metadata(owner.run_path.read_bytes()) != pins["local_run"]
                            or metadata((owner.state.results / (owner.run.run_id + ".json")).read_bytes()) != pins["local_result"]):
                        raise BrokerError("owner terminal binding changed")
                elif type(owner) is IngressResult:
                    context = lookup(contexts, owner)
                    if (not context or owner.as_dict() != context[1] or owner.canonical_sha != pins["decision_sha"]
                            or owner.canonical_destination != pins["decision_ref"]):
                        raise BrokerError("owner decision changed")
                else:
                    raise BrokerError("unsupported source owner")
            except Exception:
                return SourceFacts(status="BLOCK", gaps=("OWNER_BINDING_CHANGED",))
        return replace(facts, bindings=tuple(sorted(decode(encoded(dict(facts.bindings))).items())))
    return prepare, finish, review, read


_prepare_terminal, _finish_terminal, _issue_review, read_source_provenance = _channels()
del _channels


def correlate_sources(completion, review_ingress, *, raw_paths=None):
    """Read existing authenticated receipts; never infer Reviewer judgment or PASS."""
    left, right = read_source_provenance(completion), read_source_provenance(review_ingress)
    if left.status == "BLOCK" or right.status == "BLOCK":
        return SourceFacts(status="BLOCK", gaps=("SOURCE_BINDING_MISMATCH",))
    if not left.issuer_authenticated or not right.reviewer_origin_authenticated:
        return SourceFacts(gaps=("INDEPENDENT_SOURCE_UNAVAILABLE",))
    source, decision = dict(left.bindings), dict(right.bindings)
    keys = ("task_id", "task_revision", "task_blob_sha", "task_authoring_commit_sha", "admission_operation", "admitted_run_sha256", "run_id", "base_sha", "candidate_sha",
            "candidate_tree_sha", "artifact_ref", "artifact_sha", "run", "profile", "result", "evidence")
    if any(source[k] != decision[k] for k in keys):
        return SourceFacts(status="BLOCK", gaps=("REVIEW_BINDING_MISMATCH",))
    try:
        client = protected_client()
        for kind, expected in (("RUNTIME_TERMINAL", source), ("REVIEW_INGRESS", decision)):
            record = client.request({"action": "read", "identity": kind + ":" + source["run_id"]})["record"]
            if not record or record["facts"] != expected:
                return SourceFacts(status="BLOCK", gaps=("SOURCE_BINDING_MISMATCH",))
        raw_state = "RAW_UNAVAILABLE"
        if raw_paths is not None:
            answer = client.request({"action": "compare_raw", "identity": "RUNTIME_TERMINAL:" + source["run_id"], "paths": raw_paths})
            if not answer["matches"]:
                return SourceFacts(status="BLOCK", gaps=("RAW_BINDING_MISMATCH",))
            raw_state = "MATCHED"
        return SourceFacts(issuer_authenticated=True, reviewer_origin_authenticated=True, raw_state=raw_state,
                           gaps=("INDEPENDENT_CURRENTNESS_UNAVAILABLE",) + (("RAW_UNAVAILABLE",) if raw_state != "MATCHED" else ()),
                           bindings=tuple(sorted({**source, "decision_sha": decision["decision_sha"],
                                                  "recorded_verdict": decision["recorded_verdict"]}.items())))
    except BrokerError:
        return SourceFacts(gaps=("INDEPENDENT_SOURCE_UNAVAILABLE", "RAW_UNAVAILABLE"))


def inspect_sources(expected, *, raw_paths=None):
    """Read-only independent receipt lookup by exact caller-selected content pins.

    Expected pins select content; only the fixed protected service authenticates
    its issuance. No repository, remote, issuer, callback or trust root parameter.
    Historical records absent from the journal remain unavailable.
    """
    keys = {"task_id", "task_revision", "task_blob_sha", "task_authoring_commit_sha", "admission_operation", "admitted_run_sha256", "run_id", "base_sha", "candidate_sha",
            "candidate_tree_sha", "artifact_ref", "artifact_sha", "run", "profile", "result", "evidence",
            "decision_sha", "decision_ref", "review", "recorded_verdict"}
    if type(expected) is not dict or set(expected) != keys:
        return SourceFacts(status="BLOCK", gaps=("UNSUPPORTED_SOURCE_REQUEST",))
    try:
        validate_source_facts("REVIEW_INGRESS", expected)
        client = protected_client()
        source_record = client.request({"action": "read", "identity": "RUNTIME_TERMINAL:" + expected["run_id"]})["record"]
        decision_record = client.request({"action": "read", "identity": "REVIEW_INGRESS:" + expected["run_id"]})["record"]
        if not source_record or not decision_record:
            return SourceFacts(gaps=("INDEPENDENT_SOURCE_UNAVAILABLE", "RAW_UNAVAILABLE"))
        source, decision = source_record["facts"], decision_record["facts"]
        validate_source_facts("RUNTIME_TERMINAL", source)
        validate_source_facts("REVIEW_INGRESS", decision)
        if (source_record["kind"] != "RUNTIME_TERMINAL" or decision_record["kind"] != "REVIEW_INGRESS"
                or decision != expected or any(source[k] != decision[k] for k in keys
                    if k not in {"decision_sha", "decision_ref", "review", "recorded_verdict"})):
            return SourceFacts(status="BLOCK", gaps=("SOURCE_BINDING_MISMATCH",))
        raw_state = "RAW_UNAVAILABLE"
        if raw_paths is not None:
            if not client.request({"action": "compare_raw", "identity": "RUNTIME_TERMINAL:" + expected["run_id"], "paths": raw_paths})["matches"]:
                return SourceFacts(status="BLOCK", gaps=("RAW_BINDING_MISMATCH",))
            raw_state = "MATCHED"
        return SourceFacts(issuer_authenticated=True, reviewer_origin_authenticated=True, raw_state=raw_state,
                           gaps=("INDEPENDENT_CURRENTNESS_UNAVAILABLE",) + (("RAW_UNAVAILABLE",) if raw_state != "MATCHED" else ()),
                           bindings=tuple(sorted({**source, "decision_sha": decision["decision_sha"],
                                                  "recorded_verdict": decision["recorded_verdict"]}.items())))
    except BrokerError:
        return SourceFacts(gaps=("INDEPENDENT_SOURCE_UNAVAILABLE", "RAW_UNAVAILABLE"))
    except Exception:
        return SourceFacts(status="BLOCK", gaps=("SOURCE_BINDING_MISMATCH",))
