"""Disposable real Git objects and immutable snapshots; never run candidate proof.

Object-only variants share a bounded fixture graph instead of cloning/worktree
recreation for each negative case. No production admission, network or scheduler.
"""

import copy
import hashlib
import json
import os
import subprocess
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path

import pytest

from aios_renew import intra_run_proof_contract as declaration
from aios_renew import proof_applicability as upstream
from aios_renew import proof_checkpoint_lineage as module
from aios_renew.proof_coverage_contract import (
    BR_GATES, CONTRACT_SCHEMA, EXECUTION_DEFAULT, MAPPING_SCHEMA,
    proof_mapping_digest, validate_proof_contract,
)
from aios_renew.run import Run, RunTaskReference
from aios_renew.verification_contract import MINIMUM_SUFFICIENT_V2


def plain(value):
    return json.loads(json.dumps(value))


def digest(value):
    return upstream.canonical_digest(plain(value))


def pin(value):
    return {"id": value.id, "revision": value.revision, "digest": value.digest}


def mapping_for(contract):
    parsed = validate_proof_contract(contract, expected_task=contract["task"])
    mapping = {"schema": MAPPING_SCHEMA, "id": "mapping-v1", "revision": 1,
        "contract_id": parsed.id, "contract_revision": parsed.revision, "contract_digest": parsed.digest,
        "provenance": contract["provenance"], "proofs": [], "entries": []}
    for obligation in parsed.obligations:
        proof_id = "proof-" + obligation.id
        mapping["proofs"].append({"id": proof_id, "kind": obligation.kind,
            "claims": [plain(asdict(obligation.claim))], "conditions": plain(asdict(obligation.conditions)),
            "provenance": plain(asdict(obligation.provenance))})
        mapping["entries"].append({"obligation_id": obligation.id, "obligation_revision": obligation.revision,
            "obligation_digest": obligation.digest, "proof_id": proof_id})
    return parsed, mapping


def semantic_digest(contract, mapping, base):
    # Independently authored candidate-only normalization specified in the doc.
    contract, mapping = copy.deepcopy(contract), copy.deepcopy(mapping)
    for item in contract["obligations"]:
        item["conditions"]["candidate_sha"] = base
    mapping["contract_digest"] = None
    for item in mapping["proofs"]:
        item["conditions"]["candidate_sha"] = base
    for item in mapping["entries"]:
        item["obligation_digest"] = None
    return digest({"contract": contract, "mapping": mapping, "invariants": list(declaration.INVARIANTS),
        "dimensions": list(upstream.DIMENSIONS), "base_replay_required_gates": list(BR_GATES)})


class GitFixture:
    """Fixture issuer only: explicit offline records with genuine Git identity."""

    def __init__(self, repo):
        self.repo = repo
        repo.mkdir()
        self.trees, self.values = {}, {}
        self.git("init", "-q", "-b", "main")

    def git(self, *args, data=None):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME="Offline", GIT_AUTHOR_EMAIL="offline@example.invalid",
            GIT_COMMITTER_NAME="Offline", GIT_COMMITTER_EMAIL="offline@example.invalid",
            GIT_AUTHOR_DATE="2026-01-01T00:00:00+00:00", GIT_COMMITTER_DATE="2026-01-01T00:00:00+00:00",
            GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
        return subprocess.run(["git", "--no-replace-objects", "-c", "core.hooksPath=.git/offline-no-hooks",
            "-c", "gc.auto=0", "-C", str(self.repo), *args], env=env, input=data,
            stdin=subprocess.DEVNULL if data is None else None, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=5, check=True).stdout

    def tree(self, files):
        children = {}
        for path, blob in files.items():
            first, sep, tail = path.partition("/")
            if sep:
                children.setdefault(first, {})[tail] = blob
            else:
                children[first] = blob
        rows = []
        for name, value in sorted(children.items()):
            mode, kind, sha = ("040000", "tree", self.tree(value)) if type(value) is dict else ("100644", "blob", value)
            rows.append(f"{mode} {kind} {sha}\t{name}".encode() + b"\0")
        return self.git("mktree", "-z", data=b"".join(rows)).decode().strip()

    def commit(self, parent, updates, message="immutable fixture snapshots"):
        files = self.trees.get(parent, {}).copy()
        blobs, digests = {}, {}
        for path, value in updates.items():
            raw = value if type(value) is bytes else json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            sha = self.git("hash-object", "-w", "--stdin", data=raw).decode().strip()
            files[path] = blobs[path] = sha
            digests[path] = hashlib.sha256(raw).hexdigest()
            self.values[sha] = copy.deepcopy(value)
        tree = self.tree(files)
        args = ["commit-tree", tree, "-m", message]
        if parent is not None:
            args.extend(["-p", parent])
        commit = self.git(*args).decode().strip()
        self.trees[commit] = files
        identities = {path: {"commit_sha": commit, "path": path, "blob_sha": sha,
            "sha256": digests[path]} for path, sha in blobs.items()}
        return commit, identities

    def value(self, identity):
        return copy.deepcopy(self.values[identity["blob_sha"]])

    def variant(self, replacements=(), edit_catalog=None, reissue_seals=False):
        """New immutable source objects; original authority and history stay intact."""
        catalog = copy.deepcopy(self.catalog)
        parent = self.catalog_pin["commit_sha"]
        if replacements:
            parent, pins = self.commit(parent, {old["path"]: value for old, value in replacements})
            for old, value in replacements:
                new = pins[old["path"]]
                def substitute(item):
                    if type(item) is dict:
                        if item == old:
                            return copy.deepcopy(new)
                        return {k: substitute(v) for k, v in item.items()}
                    if type(item) is list:
                        return [substitute(v) for v in item]
                    return item
                catalog = substitute(catalog)
        if reissue_seals:
            previous_digest, previous_feedback = None, None
            original_entries = self.catalog["checkpoints"]
            def issue(identity, value):
                nonlocal parent
                if self.value(identity) == value:
                    return identity
                parent, issued = self.commit(parent, {identity["path"]: value})
                return issued[identity["path"]]
            for index, entry in enumerate(catalog["checkpoints"]):
                seal = self.value(entry["checkpoint"])
                checkpoint = seal["checkpoint"]
                checkpoint["predecessor_digest"] = previous_digest
                checkpoint["content_digest"] = digest({"schema": declaration.SCHEMA,
                    "binding": seal["binding"], "delegation": seal["delegation"],
                    "checkpoint": {k: v for k, v in checkpoint.items() if k != "content_digest"}})
                feedback = self.value(entry["feedback"])
                feedback["checkpoint_digest"] = checkpoint["content_digest"]
                feedback["content_digest"] = digest({k: v for k, v in feedback.items() if k != "content_digest"})
                entry["feedback"] = issue(entry["feedback"], feedback)
                facts = self.value(entry["facts"])
                facts["checkpoint_digest"] = checkpoint["content_digest"]
                if index and facts["consumed_feedback"] == original_entries[index - 1]["feedback"]:
                    facts["consumed_feedback"] = previous_feedback
                entry["facts"] = issue(entry["facts"], facts)
                seal["sources"] = {k: entry[k] for k in ("feedback", "facts", "contract", "mapping", "originals")}
                seal["seal_digest"] = digest({k: v for k, v in seal.items() if k != "seal_digest"})
                entry["checkpoint"] = issue(entry["checkpoint"], seal)
                previous_digest, previous_feedback = seal["seal_digest"], entry["feedback"]
            current = self.value(catalog["current"])
            old_tip = self.value(original_entries[-1]["checkpoint"])["seal_digest"]
            current["current_tips"] = [previous_digest if tip == old_tip else tip for tip in current["current_tips"]]
            current["consumed_feedback"] = [
                next((new["feedback"] for old, new in zip(original_entries, catalog["checkpoints"])
                      if receipt == old["feedback"]), receipt) for receipt in current["consumed_feedback"]]
            catalog["current"] = issue(catalog["current"], current)
        if edit_catalog is not None:
            edit_catalog(catalog)
        commit, pins = self.commit(parent, {"lineage/catalog.json": catalog}, "variant trusted fixture catalog")
        authority = module.ReadOnlyLineageAuthority(self.repo,
            upstream.decode_record_identity(pins["lineage/catalog.json"]), 1010)
        request = module.decode_request({"schema": module.SCHEMA, "repository_id": catalog["repository_id"],
            "checkpoints": [entry["checkpoint"] for entry in catalog["checkpoints"]], "replay": None})
        return authority, request

    def request(self, replay=None):
        return module.decode_request({"schema": module.SCHEMA, "repository_id": self.catalog["repository_id"],
            "checkpoints": [entry["checkpoint"] for entry in self.catalog["checkpoints"]], "replay": replay})


def observation(subject, conditions, outcome):
    profile = {"worker_mode": conditions["worker_mode"], "workers": conditions["workers"], "policy": MINIMUM_SUFFICIENT_V2}
    tools = {"python_implementation": "CPython", "python_version": "offline-v1", "python_executable": "offline-python",
        "platform_system": "offline-system", "platform_machine": "offline-machine", "pytest_version": "offline-v1",
        "pytest_xdist_version": "offline-v1"}
    reports = [{"nodeid": "tests/offline.py::test_behavior", "phase": phase,
                "outcome": outcome if phase == "call" else "PASS"} for phase in ("setup", "call", "teardown")]
    if outcome == "FAIL":
        reports[1].update(detail="Original observed assertion failed.",
            fingerprint=digest("Original observed assertion failed."), profile=profile, toolchain=tools)
    return {"subject_sha": subject, "command": "python -m pytest -q tests/offline.py", "profile": profile,
        "toolchain": tools, "exit_code": 0 if outcome == "PASS" else 1, "complete": True, "unstable": False,
        "failure_count": 0 if outcome == "PASS" else 1, "reports": reports}


def build_case(repo, *, change=None, fact_change=None, distinct=False, fork=False,
               scope_escape=False, empty_delta=False):
    fixture = GitFixture(repo)
    allowed = ["app.dat", *sorted(f"dep/{d}.dat" for d in upstream.DIMENSIONS)]
    task = {"task_id": "TASK-OFFLINE", "revision": 1, "goal": "Preserve the reviewed offline reference conditions.",
        "problem": "An offline fixture exercises immutable lineage.", "assumptions": ["Disposable local Git only."],
        "scope": {"inspect": ["app.dat"], "modify": allowed}, "non_goals": ["Run production proof."],
        "constraints": {"hard": ["Keep canonical provenance immutable."]},
        "acceptance": [{"id": "AC1", "condition": "The reference conditions hold."}],
        "verification": {"policy": MINIMUM_SUFFICIENT_V2, "required": ["python -m pytest -q tests/offline.py"]},
        "return_affinity": {"kind": "LEGACY_REPOSITORY_DEFAULT_ROUTE"}}
    base_files = {"app.dat": b"base", "records/task.json": task}
    base_files.update({f"dep/{d}.dat": d.encode() for d in upstream.DIMENSIONS})
    base, base_pins = fixture.commit(None, base_files, "task and base")
    task_pin = base_pins["records/task.json"]
    c1, _ = fixture.commit(base, {"app.dat": b"source candidate"}, "C1")
    changes = {} if empty_delta else {"app.dat": b"scoped correction"}
    if scope_escape:
        changes["outside.dat"] = b"actual unauthorized file"
    changed_dimensions = upstream.DIMENSIONS if change == "all" else (() if change is None else (change,))
    for dimension in changed_dimensions:
        changes[f"dep/{dimension}.dat"] = b"changed authenticated dependency"
    c2, _ = fixture.commit(base if fork else c1, changes, "C2")
    candidates = [c1, c2]
    task_binding = {"task_id": task["task_id"], "revision": 1, "envelope_digest": digest(task), "acceptance_ids": ["AC1"]}
    # Real canonical RUN type/decoder; no invented interim status or candidate head.
    run = Run(run_id="RUN-OFFLINE", task=RunTaskReference(task["task_id"], 1), executor="codex",
              base_sha=base, workspace="OFFLINE-DISPOSABLE")
    parent, pins = fixture.commit(c2, {"records/run.json": plain(asdict(run))})
    run_pin = pins["records/run.json"]
    native = {"executor": "codex", "profile": "native-v1", "model": "human-selected-model",
              "effort": "high", "lease_id": "lease-v1", "lease_generation": 1}
    human = {"schema": module.HUMAN_SCHEMA, "task": task_pin, "run": run_pin, **native,
        "lease_issued_at": 1000, "lease_expires_at": 1100, "operation": "PRIMARY",
        "opt_in_version": module.OPT_IN_VERSION, "allowed_paths": allowed,
        "limits": {"corrections": 2, "seconds": 60, "test_items": 100, "tokens": 10_000}}
    parent, pins = fixture.commit(parent, {"records/human.json": human})
    human_pin = pins["records/human.json"]
    delegation = {**native, "human": {"ref": "human-authority", "source_sha": human_pin["commit_sha"],
                                     "record_digest": human_pin["sha256"]}}
    budget = {"schema": module.BUDGET_SCHEMA, "human": delegation["human"], "limits": human["limits"],
              "allowed_paths": allowed, "applicable_resources": list(module.RESOURCE_NAMES)}
    parent, pins = fixture.commit(parent, {"records/budget.json": budget})
    budget_pin = pins["records/budget.json"]
    fixed = {"task_id": task["task_id"], "task_revision": 1, "acceptance_ids": ["AC1"],
        "task_source_sha": task_pin["commit_sha"], "task_blob_sha": task_pin["blob_sha"],
        "task_envelope_digest": task_binding["envelope_digest"], "run_id": run.run_id,
        "run_source_sha": run_pin["commit_sha"], "run_record_digest": run_pin["sha256"], "base_sha": base}
    operational = {"binding": fixed, "delegation": delegation, "opt_in": {"enabled": True, "human": delegation["human"]},
        "budget_authority": {"human": delegation["human"], "record": {"ref": "budget-authority",
            "source_sha": budget_pin["commit_sha"], "record_digest": budget_pin["sha256"]}},
        "limits": human["limits"], "allowed_paths": allowed}
    parent, pins = fixture.commit(parent, {"records/operational.json": operational})
    operational_pin = pins["records/operational.json"]
    provenance = {"authority_ref": "authority-v1", "review_ref": "review-v1", "source_ref": "source-v1"}
    contracts, mappings, parsed, objects, updates = [], [], [], [], {}
    kinds = [("unit", {})]
    if distinct:
        kinds.extend([("integration", {"integration_ref": "whole-suite-v1"}),
            ("concurrency", {"worker_mode": "parallel", "workers": 2, "concurrency_ref": "shared-v1"}),
            ("ordering", {"ordering_ref": "opposite-v1"}), ("comparison", {"base_sha": base})])
    for ordinal, candidate in enumerate(candidates, 1):
        conditions = {"candidate_sha": candidate, "base_sha": None, "population_ref": "population-v1", "population_size": 1,
            "test_ref": "test-v1", "fixture_refs": ["fixtures-v1"], "shared_state_ref": "helpers-state-v1",
            "profile_ref": "profile-v1", "worker_mode": "serial", "workers": 1, "concurrency_ref": "isolated-v1",
            "ordering_ref": "ordered-v1", "integration_ref": "narrow-v1", "repetition_ref": "once-v1", "toolchain_ref": "tools-v1",
            "environment_ref": "env-v1", "evidence_kind": "VERIFICATION", "evidence_schema": "evidence-v1", "collection_ref": "collection-v1"}
        obligations = []
        for name, modifications in kinds:
            obligations.append({"id": name, "revision": 1, "claim": {"id": "reference-v1",
                "text": "The offline reference conditions hold under the reviewed dependencies."}, "acceptance_ids": ["AC1"],
                "kind": "comparison" if name == "comparison" else "candidate", "blocking": True,
                "conditions": dict(conditions, **modifications), "provenance": provenance})
        contract = {"schema": CONTRACT_SCHEMA, "id": "contract-v1", "revision": 1, "execution_default": EXECUTION_DEFAULT,
                    "task": task_binding, "provenance": provenance, "obligations": obligations}
        contract_obj, mapping = mapping_for(contract)
        contracts.append(contract)
        mappings.append(mapping)
        parsed.append(contract_obj)
        updates[f"proofs/{ordinal}-contract.json"] = contract
        updates[f"proofs/{ordinal}-mapping.json"] = mapping
        original_data = {}
        for proof in mapping["proofs"]:
            name = proof["id"].removeprefix("proof-")
            outcome = "FAIL" if ordinal == 1 and name == "unit" else "PASS"
            observed = observation(candidate, proof["conditions"], outcome)
            evidence_id = f"EVIDENCE-{ordinal}-{name}"
            raw_path = f"proofs/{ordinal}-{name}-raw.log"
            tree = fixture.git("rev-parse", candidate + "^{tree}").decode().strip()
            evidence = {"evidence_id": evidence_id, "run_id": run.run_id, "subject_sha": candidate, "type": "VERIFICATION",
                "source": {"command": observed["command"]}, "result": {"exit_code": observed["exit_code"], "summary": "Immutable fixture observation."},
                "raw": {"path": raw_path}, "verification": {"policy": MINIMUM_SUFFICIENT_V2, "evidence_id": evidence_id,
                    "candidate": observed, "candidate_digest": digest(observed), "binding": {"subject_sha": candidate,
                        "base_sha": base, "tree_sha": tree, "envelope_digest": task_binding["envelope_digest"],
                        "changed_files_digest": digest(["app.dat"]), "failure_set_digest": digest([]), "command": observed["command"],
                        "profile": observed["profile"], "toolchain": observed["toolchain"]}}}
            if name == "comparison":
                evidence["verification"]["base"] = observation(base, proof["conditions"], "PASS")
                evidence["verification"]["base_digest"] = digest(evidence["verification"]["base"])
                evidence["verification"]["binding"]["failure_set_digest"] = evidence["verification"]["base_digest"]
            result = {"head_sha": candidate, "claims": [{"id": "original-claim", "satisfies": ["AC1"],
                "claim": "An immutable original observation exists.", "evidence": [evidence_id]}], "changed_files": ["app.dat"], "unresolved": []}
            updates[f"proofs/{ordinal}-{name}-evidence.json"] = evidence
            updates[f"proofs/{ordinal}-{name}-result.json"] = result
            updates[raw_path] = b"Immutable offline source log, never executed by the fixture.\n"
            original_data[name] = (proof, observed, evidence)
        objects.append(original_data)
    parent, object_pins = fixture.commit(parent, updates)
    admission = {"schema": module.ADMISSION_SCHEMA, "task": task_pin, "run": run_pin, "operation": "PRIMARY",
        "admission": "ADMITTED", "version": module.OPT_IN_VERSION, "admitted_at": 1000,
        "operational_authority": operational_pin, "semantic_digest": semantic_digest(contracts[0], mappings[0], base)}
    parent, pins = fixture.commit(parent, {"records/admission.json": admission})
    admission_pin = pins["records/admission.json"]
    proof_catalogs = {}
    # Every self-source and cross-candidate obligation gets a distinct witness.
    for source_idx, target_idx in ((0, 0), (1, 1), (0, 1)):
        context_updates, prepared = {}, []
        for name, _ in kinds:
            source_no, target_no = source_idx + 1, target_idx + 1
            label = f"{source_no}-{target_no}-{name}"
            source_proof, source_observed, source_evidence = objects[source_idx][name]
            target_proof, target_observed, _ = objects[target_idx][name]
            evidence_pin = object_pins[f"proofs/{source_no}-{name}-evidence.json"]
            bindings = {"task": task_pin, "source_contract": pin(parsed[source_idx]),
                "source_mapping": {"id": "mapping-v1", "revision": 1, "digest": proof_mapping_digest(mappings[source_idx])},
                "target_contract": pin(parsed[target_idx]),
                "target_mapping": {"id": "mapping-v1", "revision": 1, "digest": proof_mapping_digest(mappings[target_idx])},
                "source_proof_id": source_proof["id"], "source_proof_digest": digest(source_proof),
                "target_obligation": pin(next(o for o in parsed[target_idx].obligations if o.id == name)), "target_proof_id": target_proof["id"]}
            contexts, plans, comparisons = {}, [], []
            for dimension in upstream.DIMENSIONS:
                paths = [f"dep/{dimension}.dat"]
                role_values = []
                for role, index, proof, observed in (("source", source_idx, source_proof, source_observed),
                                                     ("target", target_idx, target_proof, target_observed)):
                    external_changes = ("profile", "worker_mode", "integration") if fact_change == "all" else (fact_change,)
                    facts = {"state": dimension + ("-v2" if index == 1 and dimension in external_changes else "-v1")}
                    if dimension in ("profile", "worker_mode"):
                        facts["profile"] = observed["profile"]
                    if dimension == "toolchain":
                        facts["toolchain"] = observed["toolchain"]
                    context = {"schema": upstream.CONTEXT_SCHEMA, "dimension": dimension, "subject_sha": candidates[index],
                        "proof_id": proof["id"], "evidence_digest": evidence_pin["sha256"] if role == "source" else None,
                        "conditions": {key: proof["conditions"][key] for key in upstream._FIELDS[dimension]}, "facts": facts}
                    path = f"contexts/{label}-{role}-{dimension}.json"
                    contexts[path] = context
                    role_values.append(context)
                fingerprints = []
                for index, context in zip((source_idx, target_idx), role_values):
                    dependency = [[p, ["100644", fixture.trees[candidates[index]][p]]] for p in sorted(paths)]
                    fingerprints.append(digest({"conditions": context["conditions"], "facts": context["facts"], "objects": dependency}))
                plans.append({"dimension": dimension, "coverage": "COMPLETE", "basis": "reviewed-complete-closure-v1",
                              "paths": paths, "source_context": None, "target_context": None})
                comparisons.append({"dimension": dimension, "source_digest": fingerprints[0], "target_digest": fingerprints[1]})
            context_updates.update(contexts)
            prepared.append({"name": name, "label": label, "bindings": bindings, "plans": plans,
                "comparisons": comparisons, "evidence_pin": evidence_pin, "source_observed": source_observed,
                "source_proof": source_proof})
        parent, context_pins = fixture.commit(parent, context_updates)
        reviews = {}
        for item in prepared:
            label, plans, bindings = item["label"], item["plans"], item["bindings"]
            evidence_pin, source_observed = item["evidence_pin"], item["source_observed"]
            for plan in plans:
                for role in ("source", "target"):
                    plan[role + "_context"] = context_pins[f"contexts/{label}-{role}-{plan['dimension']}.json"]
            review = {"schema": upstream.REVIEW_SCHEMA, **bindings, "dimensions": plans,
                "source_observation": {"evidence_digest": evidence_pin["sha256"], "complete": True, "stable": True,
                    "conflicting": False, "observed_items": 1, "outcome": "PASS" if source_observed["exit_code"] == 0 else "FAIL"}}
            reviews[f"proofs/{label}-review.json"] = review
        parent, review_pins = fixture.commit(parent, reviews)
        witnesses = {}
        for item in prepared:
            name, label, bindings = item["name"], item["label"], item["bindings"]
            evidence_pin, comparisons = item["evidence_pin"], item["comparisons"]
            review_pin = review_pins[f"proofs/{label}-review.json"]
            records = {"task": task_pin, "run": run_pin, "evidence": evidence_pin, "review": review_pin,
                "result": object_pins[f"proofs/{source_no}-{name}-result.json"], "raw": object_pins[f"proofs/{source_no}-{name}-raw.log"],
                "source_contract": object_pins[f"proofs/{source_no}-contract.json"], "source_mapping": object_pins[f"proofs/{source_no}-mapping.json"],
                "target_contract": object_pins[f"proofs/{target_no}-contract.json"], "target_mapping": object_pins[f"proofs/{target_no}-mapping.json"]}
            witness = {"schema": upstream.WITNESS_SCHEMA, "repository_id": "offline-repository-v1", **bindings,
                **{key: records[key] for key in ("run", "result", "evidence", "raw", "review")}, "source_candidate_sha": candidates[source_idx],
                "source_tree_sha": fixture.git("rev-parse", candidates[source_idx] + "^{tree}").decode().strip(),
                "target_candidate_sha": candidates[target_idx], "target_tree_sha": fixture.git("rev-parse", candidates[target_idx] + "^{tree}").decode().strip(),
                "base_sha": base, "dimensions": comparisons}
            witnesses[f"proofs/{label}-witness.json"] = witness
            item["records"] = records
        parent, witness_pins = fixture.commit(parent, witnesses)
        catalogs = {}
        for item in prepared:
            label, bindings, source_proof, records = item["label"], item["bindings"], item["source_proof"], item["records"]
            records["witness"] = witness_pins[f"proofs/{label}-witness.json"]
            catalog = {"schema": upstream.CATALOG_SCHEMA, "repository_id": "offline-repository-v1", "expected_main_sha": base,
                "task_binding": task_binding, "base_sha": base, "source_candidate_sha": candidates[source_idx], "target_candidate_sha": candidates[target_idx],
                "source_proof_id": source_proof["id"], "target_obligation": bindings["target_obligation"], **records}
            catalogs[f"proofs/{label}-catalog.json"] = catalog
        parent, pins = fixture.commit(parent, catalogs)
        for item in prepared:
            label = item["label"]
            proof_catalogs[label] = pins[f"proofs/{label}-catalog.json"]
    checkpoint_data, facts_data, snapshot_pins, facts_pins = [], [], {}, {}
    predecessor = None
    count = len(kinds) + (1 if distinct else 0)  # Original comparison includes its existing base population.
    for ordinal, candidate in enumerate(candidates, 1):
        binding = {**fixed, "candidate_sha": candidate,
            "candidate_tree_sha": fixture.git("rev-parse", candidate + "^{tree}").decode().strip(),
            "operational_authority_digest": digest(operational), "proof_contract": pin(parsed[ordinal - 1]),
            "proof_mapping": {"id": "mapping-v1", "revision": 1, "digest": proof_mapping_digest(mappings[ordinal - 1])}}
        usage = {"corrections": ordinal - 1, "seconds": ordinal * 5, "test_items": ordinal * count, "tokens": ordinal * 10}
        evidence_declarations = []
        for name, _ in kinds:
            proof, observed, evidence = objects[ordinal - 1][name]
            evidence_pin = object_pins[f"proofs/{ordinal}-{name}-evidence.json"]
            evidence_declarations.append({"proof_id": proof["id"], "provenance": {"ref": evidence["evidence_id"],
                "source_sha": evidence_pin["commit_sha"], "record_digest": evidence_pin["sha256"]}, "subject_sha": candidate,
                "conditions_digest": digest(proof["conditions"]), "outcome": "PASS" if observed["exit_code"] == 0 else "FAIL",
                "validity": "VALID", "complete": True, "stable": True, "conflicting": False, "observed_items": 1})
        checkpoint = {"kind": "PRETERMINAL_OBSERVATION", "owner": "RUNTIME_DECLARATION", "ordinal": ordinal,
            "predecessor_digest": predecessor, "committed": True, "clean": True, "resource_usage": usage, "evidence": evidence_declarations}
        checkpoint["content_digest"] = digest({"schema": declaration.SCHEMA, "binding": binding,
                                               "delegation": delegation, "checkpoint": checkpoint})
        feedback = {"kind": "FACTUAL_FAILED_PROOF", "binding": binding, "checkpoint_digest": checkpoint["content_digest"], "items": []}
        for item in evidence_declarations:
            if item["outcome"] != "PASS":
                obligation = next(o for o in parsed[ordinal - 1].obligations if "proof-" + o.id == item["proof_id"])
                feedback["items"].append({"obligation": pin(obligation), "acceptance_ids": ["AC1"],
                                          "evidence": item["provenance"], "outcome": item["outcome"]})
        feedback["content_digest"] = digest(feedback)
        facts = {"schema": module.FACTS_SCHEMA, "checkpoint_digest": checkpoint["content_digest"], "ordinal": ordinal,
            "candidate_sha": candidate, "candidate_tree_sha": binding["candidate_tree_sha"], "committed": True, "clean": True,
            "observed_at": 1000 + ordinal * 5, "changed_paths": ["app.dat"] if ordinal == 1 else sorted(changes),
            "progress": "INITIAL" if ordinal == 1 else "ESTABLISHED", "risk": "UNCHANGED", "semantic_choice": "UNCHANGED",
            "spent": usage, "reserved": {"corrections": 1, "seconds": 10, "test_items": 20, "tokens": 1000} if ordinal == 1 else
                {"corrections": 0, "seconds": 0, "test_items": 0, "tokens": 0}, "accounting_state": "COMPLETE", "effects_state": "ESTABLISHED",
            "consumed_feedback": None if ordinal == 1 else snapshot_pins["lineage/f1.json"],
            "causal_obligation_ids": [] if ordinal == 1 else ["unit"]}
        parent, pins = fixture.commit(parent, {f"lineage/f{ordinal}.json": feedback})
        snapshot_pins.update(pins)
        parent, pins = fixture.commit(parent, {f"lineage/facts{ordinal}.json": facts})
        facts_pins.update(pins)
        seal = {"schema": module.SCHEMA, "binding": binding, "delegation": delegation, "checkpoint": checkpoint,
            "sources": {"feedback": snapshot_pins[f"lineage/f{ordinal}.json"], "facts": facts_pins[f"lineage/facts{ordinal}.json"],
                "contract": object_pins[f"proofs/{ordinal}-contract.json"], "mapping": object_pins[f"proofs/{ordinal}-mapping.json"],
                "originals": [proof_catalogs[f"{ordinal}-{ordinal}-{name}"] for name, _ in kinds]}}
        seal["seal_digest"] = digest(seal)
        parent, pins = fixture.commit(parent, {f"lineage/k{ordinal}.json": seal})
        snapshot_pins.update(pins)
        checkpoint_data.append(seal)
        facts_data.append(facts)
        predecessor = seal["seal_digest"]
    current = {"schema": module.CURRENT_SCHEMA, "task": task_pin, "run": run_pin, "admission": admission_pin,
        "operational_authority": operational_pin, "delegation": delegation, "operation": "PRIMARY", "run_state": "ACTIVE",
        "terminal_ref": None, "observed_at": 1010, "lease_state": "LIVE", "lease_expires_at": 1100, "authority_current": True,
        "risk": "UNCHANGED", "semantic_choice": "UNCHANGED", "tail_state": "SEALED", "effects_state": "ESTABLISHED",
        "accounting_state": "COMPLETE", "spent": facts_data[1]["spent"], "reserved": facts_data[1]["reserved"],
        "current_tips": [checkpoint_data[1]["seal_digest"]], "consumed_feedback": [snapshot_pins["lineage/f1.json"]]}
    parent, pins = fixture.commit(parent, {"lineage/current.json": current})
    catalog = {"schema": module.CATALOG_SCHEMA, "repository_id": "offline-repository-v1", "expected_main_sha": base,
        "expected_head_sha": c2, "task": task_pin, "run": run_pin, "human": human_pin, "budget": budget_pin,
        "operational_authority": operational_pin, "admission": admission_pin, "current": pins["lineage/current.json"], "checkpoints": [],
        "transitions": [{"source_ordinal": 1, "target_ordinal": 2, "catalogs": [proof_catalogs[f"1-2-{name}"] for name, _ in kinds]}]}
    for ordinal in (1, 2):
        catalog["checkpoints"].append({"checkpoint": snapshot_pins[f"lineage/k{ordinal}.json"],
            "feedback": snapshot_pins[f"lineage/f{ordinal}.json"], "facts": facts_pins[f"lineage/facts{ordinal}.json"],
            "contract": object_pins[f"proofs/{ordinal}-contract.json"], "mapping": object_pins[f"proofs/{ordinal}-mapping.json"],
            "originals": [proof_catalogs[f"{ordinal}-{ordinal}-{name}"] for name, _ in kinds]})
    parent, pins = fixture.commit(parent, {"lineage/catalog.json": catalog})
    fixture.catalog, fixture.catalog_pin = catalog, pins["lineage/catalog.json"]
    fixture.authority = module.ReadOnlyLineageAuthority(repo, upstream.decode_record_identity(fixture.catalog_pin), 1010)
    fixture.git("update-ref", "refs/heads/main", base)
    fixture.git("checkout", "--detach", "-q", c2)
    return fixture


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    cache = {}
    def get(**options):
        key = tuple(sorted(options.items()))
        if key not in cache:
            cache[key] = build_case(tmp_path_factory.mktemp("lineage") / "repository", **options)
        return cache[key]
    return get


def evaluate(fixture, authority=None, request=None):
    return module.evaluate_lineage(request or fixture.request(), authority=authority or fixture.authority)


def no_effects(result):
    assert result.status in ("BLOCK", "UNKNOWN") and result.activation == "NOT_ACTIVATED"
    assert result.base_replay_required_gates == BR_GATES
    for name in ("authorization_granted", "runtime_continuation_authorized", "lifecycle_mutation_authorized",
                 "checkpoint_append_authorized", "feedback_consumption_authorized", "acceptance_discharge_authorized",
                 "evidence_reuse_authorized", "verification_execution_authorized", "terminalization_authorized", "scheduler_activated",
                 "target_execution_evidence_created", "canonical_checkpoint_created", "artifact_persistence_performed"):
        assert getattr(result, name) is False


def test_exact_active_null_head_chain_preserves_fail_and_subject_separation(cases):
    fixture = cases()
    assert fixture.value(fixture.catalog["run"])["head_sha"] is None
    assert fixture.value(fixture.catalog["run"])["status"] == "ACTIVE"
    result = evaluate(fixture)
    assert result.history_authenticated and result.observation == "CONSISTENT", result.reasons
    assert [s.checkpoint.ordinal for s in result.seals] == [1, 2]
    assert result.seals[0].checkpoint.predecessor_digest is None
    assert result.seals[1].checkpoint.predecessor_digest == result.seals[0].seal_digest
    proof = result.proofs[0].result
    assert proof.state == upstream.State.VALID and proof.source.outcome == "FAIL" and proof.source.exit_code == 1
    assert proof.source == result.seals[0].originals[0].source
    assert proof.source.subject_sha == result.seals[0].binding.candidate_sha != proof.witness.target_candidate_sha
    assert proof.witness.target_candidate_sha == result.seals[1].binding.candidate_sha
    assert proof.source.evidence != proof.witness.record and proof.source.result != proof.witness.record
    assert result.seals[1].checkpoint.resource_usage.corrections == 1
    no_effects(result)
    with pytest.raises(FrozenInstanceError):
        result.seals[0].checkpoint.ordinal = 2


def test_same_sealed_replay_has_no_double_feedback_budget_or_writes(cases):
    fixture = cases()
    baseline = evaluate(fixture)
    request = fixture.request(replay=fixture.catalog["checkpoints"][1]["checkpoint"])
    before = {p.relative_to(fixture.repo): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in fixture.repo.rglob("*") if p.is_file()}
    replay = evaluate(fixture, request=request)
    assert replay.observation == "SAME_FACT" and replay.history_authenticated
    assert replay.seals == baseline.seals and replay.proofs == baseline.proofs
    assert replay == evaluate(fixture, request=request)
    after = {p.relative_to(fixture.repo): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in fixture.repo.rglob("*") if p.is_file()}
    assert before == after
    no_effects(replay)


@pytest.mark.parametrize("field,value", [
    ("ordinal", 3), ("ordinal", 1), ("predecessor_digest", None), ("predecessor_digest", "0" * 64),
    ("content_digest", "0" * 64), ("clean", False), ("committed", False),
])
def test_gap_conflicting_ordinal_bad_parent_tampering_or_dirty_seal(cases, field, value):
    fixture = cases()
    identity = fixture.catalog["checkpoints"][1]["checkpoint"]
    record = fixture.value(identity)
    record["checkpoint"][field] = value
    authority, request = fixture.variant([(identity, record)])
    result = evaluate(fixture, authority, request)
    assert result.status == "BLOCK" and result.observation == "CONFLICT"
    no_effects(result)


@pytest.mark.parametrize("defect", ["duplicate", "missing-first", "missing-tail", "competing-tip", "wrong-repository", "reordered"])
def test_history_requires_exact_finite_unique_order_and_tip(cases, defect):
    fixture = cases()
    if defect == "competing-tip":
        identity = fixture.catalog["current"]
        record = fixture.value(identity)
        record["current_tips"].append("0" * 64)
        authority, request = fixture.variant([(identity, record)])
    else:
        def edit(catalog):
            if defect == "duplicate":
                catalog["checkpoints"].append(copy.deepcopy(catalog["checkpoints"][-1]))
            elif defect == "missing-first":
                catalog["checkpoints"].pop(0)
            elif defect == "reordered":
                catalog["checkpoints"].reverse()
        authority, request = fixture.variant(edit_catalog=edit)
        if defect == "missing-tail":
            request = replace(request, checkpoints=request.checkpoints[:1])
        if defect == "wrong-repository":
            request = replace(request, repository_id="another-repository")
    result = evaluate(fixture, authority, request)
    assert result.observation in ("CONFLICT", "INCOMPLETE") and not result.history_authenticated
    no_effects(result)


def test_real_git_sibling_is_not_candidate_continuity(cases):
    result = evaluate(cases(fork=True))
    assert result.status == "BLOCK" and "LINEAGE_CONFLICT" in result.reasons
    no_effects(result)


@pytest.mark.parametrize("defect", ["scope_escape", "empty_delta"])
def test_actual_git_delta_must_be_nonempty_and_in_scope(cases, defect):
    result = evaluate(cases(**{defect: True}))
    assert result.status == "BLOCK" and "EMPTY_DELTA_OR_SCOPE_ESCAPE" in result.reasons
    no_effects(result)


@pytest.mark.parametrize("field,value", [("task_revision", 2), ("run_id", "RUN-OTHER"), ("base_sha", "0" * 40),
    ("candidate_sha", "base"), ("candidate_sha", "prior"), ("candidate_tree_sha", "0" * 40),
    ("operational_authority_digest", "0" * 64)])
def test_fixed_task_run_base_tree_and_operational_identity(cases, field, value):
    fixture = cases()
    identity = fixture.catalog["checkpoints"][1]["checkpoint"]
    record = fixture.value(identity)
    if value == "base":
        value = record["binding"]["base_sha"]
    if value == "prior":
        value = fixture.value(fixture.catalog["checkpoints"][0]["checkpoint"])["binding"]["candidate_sha"]
    record["binding"][field] = value
    authority, request = fixture.variant([(identity, record)])
    result = evaluate(fixture, authority, request)
    assert result.status == "BLOCK"
    no_effects(result)


@pytest.mark.parametrize("field,value", [("profile", "changed-profile"), ("model", "changed-model"), ("effort", "max"),
    ("executor", "antigravity"), ("lease_id", "changed-lease"), ("lease_generation", 2)])
def test_checkpoint_cannot_change_human_native_delegation(cases, field, value):
    fixture = cases()
    identity = fixture.catalog["checkpoints"][1]["checkpoint"]
    record = fixture.value(identity)
    record["delegation"][field] = value
    authority, request = fixture.variant([(identity, record)])
    result = evaluate(fixture, authority, request)
    assert "DELEGATION_CHANGED" in result.reasons and result.status == "BLOCK"
    no_effects(result)


@pytest.mark.parametrize("field,value", [("tail_state", "TORN"), ("tail_state", "MISSING"), ("effects_state", "UNKNOWN"),
    ("accounting_state", "PARTIAL"), ("authority_current", False), ("observed_at", 1009), ("current_tips", []),
    ("lease_state", "LOST"), ("lease_state", "UNKNOWN"), ("lease_expires_at", 1200),
    ("risk", "HUMAN_REQUIRED"), ("semantic_choice", "BRAIN_REQUIRED"), ("operation", "REPAIR"),
    ("operation", "REMEDIATION"), ("run_state", "RESULT"), ("run_state", "FAILURE"), ("terminal_ref", "terminal-v1")])
def test_live_drift_partial_crash_and_terminal_or_other_operation_fail_closed(cases, field, value):
    fixture = cases()
    identity = fixture.catalog["current"]
    record = fixture.value(identity)
    record[field] = value
    authority, request = fixture.variant([(identity, record)])
    result = evaluate(fixture, authority, request)
    assert result.observation in ("CONFLICT", "INCOMPLETE")
    no_effects(result)


def test_expired_time_missing_root_and_historical_version_never_enable_ka01(cases):
    fixture = cases()
    assert evaluate(fixture, replace(fixture.authority, observed_at=1100)).status == "BLOCK"
    result = module.evaluate_lineage(fixture.request(), authority=None)
    assert result.status == "UNKNOWN" and result.seals == ()
    identity = fixture.catalog["admission"]
    record = fixture.value(identity)
    record["version"] = "V2"
    authority, request = fixture.variant([(identity, record)])
    assert evaluate(fixture, authority, request).status == "BLOCK"


@pytest.mark.parametrize("resource", module.RESOURCE_NAMES)
@pytest.mark.parametrize("defect", ["reset", "negative", "overflow", "overrun", "missing"])
def test_all_resources_are_explicit_finite_monotonic_and_reserved(cases, resource, defect):
    fixture = cases()
    identity = fixture.catalog["checkpoints"][1]["facts"]
    record = fixture.value(identity)
    if defect == "missing":
        record["spent"].pop(resource)
    else:
        record["spent"][resource] = {"reset": 0, "negative": -1, "overflow": 2_147_483_648,
                                      "overrun": fixture.value(fixture.catalog["human"])["limits"][resource] + 1}[defect]
    authority, request = fixture.variant([(identity, record)], reissue_seals=True)
    result = evaluate(fixture, authority, request)
    assert result.observation in ("CONFLICT", "INCOMPLETE")
    no_effects(result)


@pytest.mark.parametrize("defect", ["reserve-reset", "reserve-overrun", "progress", "causal", "scope", "spent-reset", "double-feedback"])
def test_crash_cannot_reset_reservation_effects_progress_scope_or_live_accounting(cases, defect):
    fixture = cases()
    index = 0 if defect.startswith("reserve") else 1
    identity = fixture.catalog["current"] if defect in ("spent-reset", "double-feedback") else fixture.catalog["checkpoints"][index]["facts"]
    record = fixture.value(identity)
    if defect == "reserve-reset":
        record["reserved"]["corrections"] = 0
    elif defect == "reserve-overrun":
        record["reserved"]["seconds"] = 60
    elif defect == "progress":
        record["progress"] = "UNKNOWN"
    elif defect == "causal":
        record["causal_obligation_ids"] = []
    elif defect == "scope":
        record["changed_paths"] = ["outside.dat"]
    elif defect == "spent-reset":
        record["spent"]["tokens"] = 0
    elif defect == "double-feedback":
        record["consumed_feedback"].append(record["consumed_feedback"][0])
    authority, request = fixture.variant([(identity, record)], reissue_seals=defect not in ("spent-reset", "double-feedback"))
    result = evaluate(fixture, authority, request)
    assert result.observation in ("CONFLICT", "INCOMPLETE")
    no_effects(result)


@pytest.mark.parametrize("field", ["limits", "human", "allowed_paths", "applicable_resources"])
def test_human_budget_and_scope_cannot_drift_or_be_missing(cases, field):
    fixture = cases()
    identity = fixture.catalog["budget"]
    record = fixture.value(identity)
    if field == "limits":
        record[field]["tokens"] += 1
    else:
        record.pop(field)
    authority, request = fixture.variant([(identity, record)])
    result = evaluate(fixture, authority, request)
    assert result.observation in ("CONFLICT", "INCOMPLETE")
    no_effects(result)


def test_changed_real_dependency_objects_invalidate_without_execution(cases):
    fixture = cases(change="all")
    result = evaluate(fixture)
    assert result.history_authenticated and result.observation == "CONSISTENT", result.reasons
    proof = result.proofs[0].result
    assert proof.state == upstream.State.INVALIDATED and proof.source.outcome == "FAIL"
    assert {r.dimension for r in proof.reasons} == set(upstream.DIMENSIONS)
    assert proof.applicable_obligation is None
    no_effects(result)


def test_changed_authenticated_external_profile_worker_and_integration_facts(cases):
    result = evaluate(cases(fact_change="all"))
    assert result.history_authenticated and result.proofs[0].result.state == upstream.State.INVALIDATED, result.reasons
    assert {r.dimension for r in result.proofs[0].result.reasons} == {"profile", "worker_mode", "integration"}
    no_effects(result)


def test_distinct_obligations_and_existing_comparison_remain_separate(cases):
    fixture = cases(distinct=True)
    result = evaluate(fixture)
    assert result.history_authenticated and len(result.proofs) == 5, result.reasons
    assert {p.result.witness.target_obligation.id for p in result.proofs} == {"unit", "integration", "concurrency", "ordering", "comparison"}
    assert all(p.result.state == upstream.State.VALID for p in result.proofs)
    no_effects(result)
    for defect in ("missing", "duplicate"):
        def edit(catalog):
            if defect == "missing":
                catalog["transitions"][0]["catalogs"].pop()
            else:
                catalog["transitions"][0]["catalogs"][-1] = catalog["transitions"][0]["catalogs"][0]
        authority, request = fixture.variant(edit_catalog=edit)
        broken = evaluate(fixture, authority, request)
        assert broken.observation in ("CONFLICT", "INCOMPLETE")
        no_effects(broken)


@pytest.mark.parametrize("defect", ["missing", "forged", "outcome", "source-subject", "contract", "mapping", "witness"])
def test_no_bare_digest_valid_label_or_forged_original_provenance(cases, defect):
    fixture = cases()
    if defect == "outcome":
        identity = fixture.catalog["checkpoints"][0]["checkpoint"]
        record = fixture.value(identity)
        record["checkpoint"]["evidence"][0]["outcome"] = "PASS"
    else:
        identity = fixture.catalog["checkpoints"][0]["originals"][0]
        record = fixture.value(identity)
        if defect in ("missing", "forged"):
            record["evidence"]["blob_sha" if defect == "missing" else "sha256"] = "0" * (40 if defect == "missing" else 64)
        elif defect == "source-subject":
            record["source_candidate_sha"] = record["target_candidate_sha"] = fixture.catalog["expected_head_sha"]
        elif defect == "contract":
            record["source_contract"]["sha256"] = "0" * 64
        elif defect == "mapping":
            record["target_mapping"]["sha256"] = "0" * 64
        elif defect == "witness":
            record["witness"]["sha256"] = "0" * 64
    authority, request = fixture.variant([(identity, record)], reissue_seals=True)
    result = evaluate(fixture, authority, request)
    assert result.observation in ("CONFLICT", "INCOMPLETE")
    no_effects(result)


def test_valid_mapping_change_still_cannot_change_admitted_meaning(cases):
    fixture = cases()
    entry = fixture.catalog["checkpoints"][1]
    contract = fixture.value(entry["contract"])
    contract["obligations"][0]["claim"]["text"] = "A different semantic requirement now applies."
    parsed, mapping = mapping_for(contract)
    seal = fixture.value(entry["checkpoint"])
    seal["binding"]["proof_contract"] = pin(parsed)
    seal["binding"]["proof_mapping"] = {"id": mapping["id"], "revision": mapping["revision"], "digest": proof_mapping_digest(mapping)}
    authority, request = fixture.variant([(entry["contract"], contract), (entry["mapping"], mapping),
                                         (entry["checkpoint"], seal)], reissue_seals=True)
    result = evaluate(fixture, authority, request)
    assert result.status == "BLOCK" and "SEMANTIC_OR_MAPPING_CHANGED" in result.reasons
    no_effects(result)


def test_upstream_unknown_is_retained_per_proof_and_never_discharges(cases):
    fixture = cases()
    identity = fixture.catalog["transitions"][0]["catalogs"][0]
    record = fixture.value(identity)
    record["witness"]["sha256"] = "0" * 64
    authority, request = fixture.variant([(identity, record)])
    result = evaluate(fixture, authority, request)
    assert result.status == "UNKNOWN" and result.history_authenticated
    assert result.proofs[0].result.state == upstream.State.UNKNOWN
    assert result.proofs[0].result.applicable_obligation is None
    no_effects(result)


def test_conflicting_replay_and_torn_missing_replay_are_distinct(cases):
    fixture = cases()
    old = fixture.catalog["checkpoints"][1]["checkpoint"]
    forged = fixture.value(old)
    forged["checkpoint"]["ordinal"] = 3
    commit, pins = fixture.commit(fixture.catalog_pin["commit_sha"], {"lineage/replay.json": forged})
    # Trusted root authenticates the delivery bytes without admitting a new seal.
    commit, root_pins = fixture.commit(commit, {"lineage/replay-catalog.json": fixture.catalog})
    authority = replace(fixture.authority, catalog=upstream.decode_record_identity(root_pins["lineage/replay-catalog.json"]))
    request = fixture.request(replay=pins["lineage/replay.json"])
    conflict = evaluate(fixture, authority, request)
    assert conflict.status == "BLOCK" and "CONFLICTING_OR_UNCATALOGED_REPLAY" in conflict.reasons
    missing = replace(request, replay=replace(request.replay, blob_sha="0" * 40))
    partial = evaluate(fixture, authority, missing)
    assert partial.status == "UNKNOWN"
    no_effects(conflict)
    no_effects(partial)


@pytest.mark.parametrize("which", ["main", "HEAD", "during-read"])
def test_moved_current_head_or_main_fails_closed(cases, monkeypatch, which):
    fixture = cases()
    base = fixture.catalog["expected_main_sha"]
    target = fixture.catalog["expected_head_sha"]
    try:
        if which == "main":
            fixture.git("update-ref", "refs/heads/main", target)
        elif which == "HEAD":
            fixture.git("update-ref", "--no-deref", "HEAD", base)
        else:
            original = upstream._Git._read
            reads = 0
            def moved(reader, args, oid=None):
                nonlocal reads
                if args == ["rev-parse", "--verify", "HEAD"]:
                    reads += 1
                    if reads == 2:
                        return (base + "\n").encode()
                return original(reader, args, oid)
            monkeypatch.setattr(upstream._Git, "_read", moved)
        result = evaluate(fixture)
        assert result.status == "UNKNOWN" and result.observation == "INCOMPLETE"
        no_effects(result)
    finally:
        fixture.git("update-ref", "refs/heads/main", base)
        fixture.git("update-ref", "--no-deref", "HEAD", target)


@pytest.mark.parametrize("defect", ["extra", "missing", "schema", "unbounded", "nondecoded", "short-sha", "path", "caller-valid", "caller-root"])
def test_exact_request_and_bounded_plain_identity_decode(cases, defect):
    fixture = cases()
    values = {"schema": module.SCHEMA, "repository_id": "offline-repository-v1",
              "checkpoints": [e["checkpoint"] for e in fixture.catalog["checkpoints"]], "replay": None}
    if defect == "extra":
        values["instructions"] = "retry"
    elif defect == "missing":
        values.pop("replay")
    elif defect == "schema":
        values["schema"] = "v2"
    elif defect == "unbounded":
        values["checkpoints"] *= module.MAX_CHECKPOINTS
    elif defect == "nondecoded":
        values["repository_id"] = object()
    elif defect == "short-sha":
        values["checkpoints"][0] = dict(values["checkpoints"][0], commit_sha="1234")
    elif defect == "path":
        values["checkpoints"][0] = dict(values["checkpoints"][0], path="../catalog.json")
    elif defect == "caller-valid":
        values["validity"] = "VALID"
    elif defect == "caller-root":
        values["authority"] = fixture.catalog_pin
    with pytest.raises(module.LineageInputError):
        module.decode_request(values)


def test_bounded_evaluator_uses_only_read_only_git_and_published_engine(cases, monkeypatch):
    fixture = cases()
    run_process = subprocess.run
    engine = upstream.evaluate_applicability
    calls, executions = [], []
    def guard(command, *args, **kwargs):
        assert command[0] == "git"
        assert command[command.index(str(fixture.repo)) + 1] in ("rev-parse", "cat-file")
        calls.append(command)
        return run_process(command, *args, **kwargs)
    def engine_guard(request, *, authority):
        executions.append(request)
        return engine(request, authority=authority)
    monkeypatch.setattr(subprocess, "run", guard)
    monkeypatch.setattr(upstream, "evaluate_applicability", engine_guard)
    result = evaluate(fixture)
    assert result.history_authenticated and len(executions) == 3
    assert len(calls) <= (len(executions) + 1) * upstream.MAX_GIT_CALLS
    no_effects(result)
    def too_many(catalog):
        catalog["transitions"][0]["catalogs"] *= module.MAX_EVALUATIONS + 1
    # Prepare immutable variant outside the read-only guard.
    monkeypatch.setattr(subprocess, "run", run_process)
    authority, request = fixture.variant(edit_catalog=too_many)
    executions.clear()
    bounded = evaluate(fixture, authority, request)
    assert bounded.status == "UNKNOWN" and "RESOURCE_BOUND" in bounded.reasons and executions == []
