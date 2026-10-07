import os
import stat
import subprocess
import json
import hashlib
import shlex
from contextlib import contextmanager
from pathlib import Path

import pytest

import aios_renew.verification as verification_module
from aios_renew.artifacts import Claim, Result
from aios_renew.verification import (
    RuntimeVerificationError,
    attach_verification_evidence,
    execute_verification,
    materialize_verification_subject,
    execute_minimum_verification,
)


@pytest.fixture
def v2_injected_execution(tmp_path, monkeypatch):
    from tests.test_verification_contract import V2_BASE, V2_CANDIDATE, V2_TOOLCHAIN, v2_observation
    from aios_renew.verification_contract import SELECTED_FULL_SUITE_COMMAND
    state = {"tree": "c" * 40, "observer": "f" * 40, "detail": "AssertionError: expected 1",
             "base_pass": False, "probe_divergent": False, "probe_pass": False, "calls": []}

    def observe_git(repository, *args, **kwargs):
        if args[:2] == ("rev-parse", "--verify"):
            return args[2].split("^")[0]
        if args[:2] == ("diff", "--name-only"):
            return "tests/test_sample.py\0"
        if args[0] == "rev-parse" and args[1].endswith("^{tree}"):
            return "c" * 40 if args[1].startswith(V2_CANDIDATE) else state["tree"]
        if args[0] == "rev-parse" and ":tests/bp_v4_probe_plugin.py" in args[1]:
            return state["observer"]
        if args[0] == "rev-parse" and ":.ai/verification-profiles.yaml" in args[1]:
            return "e" * 40
        if args[0] == "rev-parse" and ":" in args[1]:
            return "1" * 40
        if args[0] == "show":
            return json.dumps(dict(format="AIOS_VERIFICATION_PROFILE_POLICY", version=1,
                ordinary_canonical_full_suite=dict(profile="bounded-parallel-full-suite-v1", command=SELECTED_FULL_SUITE_COMMAND,
                    workers=12, distribution="load", max_worker_restart=0,
                    selection_provenance=dict(authority="HUMAN", task_id="TASK-231"))))
        raise AssertionError(args)

    @contextmanager
    def materialize(repository, *, subject_sha, **kwargs):
        checkout = tmp_path / subject_sha
        checkout.mkdir(exist_ok=True)
        yield checkout

    def runner(shell, **kwargs):
        sha = kwargs["cwd"].name
        command = shell[-1]
        state["calls"].append((sha, command))
        profile = json.loads(kwargs["env"]["AIOS_V2_PROFILE"])
        outcome = "PASS" if sha == V2_BASE and state["base_pass"] else "FAIL"
        if command != SELECTED_FULL_SUITE_COMMAND and state["probe_pass"]:
            outcome = "PASS"
        detail = state["detail"]
        if state["probe_divergent"] and command != SELECTED_FULL_SUITE_COMMAND and sha != V2_BASE:
            detail = "AssertionError: divergent candidate probe"
        value = v2_observation(sha, outcome=outcome, count=51, detail=detail, command=command)
        for report in value["reports"]:
            if report["outcome"] == "FAIL":
                report.update(profile=profile, toolchain=V2_TOOLCHAIN)
        canonical = {k: value[k] for k in ("complete", "unstable", "reports", "failure_count")}
        status = value["exit_code"]
        if command == SELECTED_FULL_SUITE_COMMAND:
            envelope = {"result": dict(canonical=canonical, observation_profile=profile,
                observation_toolchain=V2_TOOLCHAIN, conformance={"all_workers_reported": True},
                exit_status=status, pytest_exit_status=status)}
            stdout = json.dumps(envelope).encode("utf-8")
        else:
            output = Path(kwargs["env"]["AIOS_BP_V4_PLUGIN_OUTPUT"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(dict(schema="AIOS_BP_V4_PYTEST_OBSERVATION", version=1,
                profile=profile, toolchain=V2_TOOLCHAIN, exit_status=status,
                failure_diagnostics=dict(canonical=canonical, reported_count=canonical["failure_count"]))), encoding="utf-8")
            stdout = b"synthetic pytest output\n"
        return completed(status, stdout=stdout)

    monkeypatch.setattr(verification_module, "_git", observe_git)
    monkeypatch.setattr(verification_module, "_optional_git", observe_git)
    monkeypatch.setattr(verification_module, "_v2_toolchain", lambda: dict(V2_TOOLCHAIN))
    monkeypatch.setattr(verification_module, "materialize_verification_subject", materialize)

    def execute(run_id, *, candidate=V2_CANDIDATE, operation="PRIMARY", scope=("tests/test_sample.py",)):
        return execute_minimum_verification((SELECTED_FULL_SUITE_COMMAND,), run_id=run_id,
            base_sha=V2_BASE, subject_sha=candidate, repository=tmp_path,
            raw_directory=tmp_path / run_id, cache_directory=tmp_path / "cache", modification_scope=scope,
            operation=operation, runner=runner, platform="posix", environment={}, subject_check=lambda *a, **k: None)
    return execute, state


def test_v2_full_guard_preserves_51_raw_failures_and_reuses_exact_evidence(v2_injected_execution, tmp_path):
    execute, state = v2_injected_execution
    first = execute("RUN-first")
    assert len(state["calls"]) == 2
    assert first[0].result.exit_code == 1
    assert len(first[0].verification["attribution"]) == 51
    assert {r["classification"] for r in first[0].verification["attribution"]} == {"PRE_EXISTING_BASELINE"}
    raw = Path(first[0].raw_path).read_bytes()
    second = execute("RUN-second")
    assert len(state["calls"]) == 2 and second[0].result.exit_code == 1
    assert second[0].verification["disposition"] == "REUSED"
    assert Path(second[0].raw_path).read_bytes() == raw
    audit = json.loads((tmp_path / "RUN-second" / "minimum-sufficient-v2-plan.json").read_text(encoding="utf-8"))
    assert audit["records"][0]["disposition"] == "REUSED"
    third = execute("RUN-third", candidate="d" * 40)
    assert len(state["calls"]) == 2
    assert third[0].subject_sha == "d" * 40
    assert third[0].verification["candidate"]["subject_sha"] == first[0].subject_sha


@pytest.mark.parametrize("change", ["tree", "observer", "raw"])
def test_v2_changed_relevant_material_executes_the_authored_guard_again(v2_injected_execution, change):
    execute, state = v2_injected_execution
    first = execute("RUN-first")
    if change == "raw":
        Path(first[0].raw_path).write_bytes(b"tampered raw output")
    else:
        state[change] = "9" * 40
    execute("RUN-next", candidate="d" * 40)
    assert len(state["calls"]) == (3 if change == "tree" else 4)


def test_v2_base_pass_candidate_failure_is_blocking(v2_injected_execution):
    execute, state = v2_injected_execution
    state["base_pass"] = True
    with pytest.raises(RuntimeVerificationError) as failure:
        execute("RUN-regression")
    item = failure.value.evidence[0]
    assert item.result.exit_code == 1
    assert {r["classification"] for r in item.verification["attribution"]} == {"CANDIDATE_REGRESSION"}


def test_v2_affected_failure_stops_before_full_guard(v2_injected_execution, tmp_path):
    from aios_renew.verification_contract import SELECTED_FULL_SUITE_COMMAND
    execute, state = v2_injected_execution
    execute("RUN-prior")
    state["calls"].clear()
    state["probe_divergent"] = True
    with pytest.raises(RuntimeVerificationError) as failure:
        execute("RUN-correction", operation="REPAIR")
    candidate_calls = [command for sha, command in state["calls"] if sha == "b" * 40]
    assert len(candidate_calls) == 1
    assert "tests/test_sample.py::test_050" in candidate_calls[0]
    assert SELECTED_FULL_SUITE_COMMAND not in candidate_calls
    assert failure.value.evidence[-1].result.exit_code == 1
    assert failure.value.evidence[0].verification["early_probe"] is True
    audit = json.loads((tmp_path / "RUN-correction" / "minimum-sufficient-v2-plan.json").read_text(encoding="utf-8"))
    assert audit["modification_scope"] == ["tests/test_sample.py"]


def test_v2_affected_success_still_executes_full_guard(v2_injected_execution):
    from aios_renew.verification_contract import SELECTED_FULL_SUITE_COMMAND
    execute, state = v2_injected_execution
    execute("RUN-prior")
    state["calls"].clear()
    state["probe_pass"] = True
    result = execute("RUN-correction", operation="REPAIR")
    candidate_calls = [command for sha, command in state["calls"] if sha == "b" * 40]
    assert len(candidate_calls) == 2
    assert "tests/test_sample.py::test_050" in candidate_calls[0]
    assert candidate_calls[-1] == SELECTED_FULL_SUITE_COMMAND
    assert len(result) == 1 and result[0].source.command == SELECTED_FULL_SUITE_COMMAND
    assert result[0].result.exit_code == 1  # The existing baseline proof remains separate.


def test_v2_canonical_cache_rejects_missing_raw_or_digest_tampering(tmp_path):
    from tests.test_verification_contract import v2_record
    raw = tmp_path / "source.raw"
    raw.write_bytes(b"immutable verification output")
    record = {**v2_record(), "raw_path": str(raw), "raw_digest": hashlib.sha256(raw.read_bytes()).hexdigest()}
    cache = tmp_path / "cache"
    cache.mkdir()
    path = cache / "proof.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    assert verification_module._v2_cache_records(cache) == (record,)
    record["candidate_digest"] = "0" * 64
    path.write_text(json.dumps(record), encoding="utf-8")
    assert verification_module._v2_cache_records(cache) == ()
    raw.unlink()
    assert verification_module._v2_cache_records(cache) == ()


@pytest.fixture
def task320_execution(tmp_path, monkeypatch):
    from tests.test_verification_contract import V2_BASE, V2_CANDIDATE, V2_TOOLCHAIN, task320_observation
    from aios_renew.verification_contract import verification_digest
    command = "python -m pytest -q tests/test_sample.py"
    before = [f"tests/test_sample.py::test_{i:03d}" for i in range(8)]
    state = dict(base_nodes=before, candidate_nodes=before + ["tests/test_sample.py::test_new"],
                 candidate_failures=set(), base_failures=set(), calls=[],
                 nonreproduction=False, collection_defect=None, broad_disagreement=False,
                 projection_defect=None, observer_mismatch=False)

    def observe_git(repository, *args, **kwargs):
        if args[:2] == ("rev-parse", "--verify"):
            return args[2].split("^")[0]
        if args[:2] == ("diff", "--name-only"):
            return "tests/test_sample.py\0"
        if args[0] == "rev-parse" and args[1].endswith("^{tree}"):
            return args[1][0] * 40
        if args[0] == "rev-parse" and ":" in args[1]:
            if state["observer_mismatch"] and args[1].startswith(V2_BASE):
                return "9" * 40
            return "f" * 40
        raise AssertionError(args)

    @contextmanager
    def materialize(repository, *, subject_sha, **kwargs):
        checkout = tmp_path / subject_sha
        checkout.mkdir(exist_ok=True)
        yield checkout

    def runner(shell, **kwargs):
        sha, actual = kwargs["cwd"].name, shell[-1]
        native = shell[0] == "python"
        argv = list(shell) if native else shlex.split(actual)
        if native:
            actual = shlex.join(argv)
        collect = "--collect-only" in argv
        selectors = [a for a in argv[4:] if a != "--collect-only"]
        broad = selectors == ["tests/test_sample.py"]
        nodes = list(state["base_nodes"] if sha == V2_BASE else state["candidate_nodes"]) if broad else selectors
        if broad and not collect and sha != V2_BASE and state["broad_disagreement"]:
            nodes = nodes + ["tests/test_sample.py::test_unstable_collection"]
        failed = set(state["base_failures"] if sha == V2_BASE else state["candidate_failures"]).intersection(nodes)
        if not broad and sha != V2_BASE and state["nonreproduction"]:
            failed = set()
        profile = json.loads(kwargs["env"]["AIOS_V2_PROFILE"])
        observed = task320_observation(sha, actual, nodes, failures=failed, collect=collect, profile=profile)
        canonical = {k: observed[k] for k in ("complete", "unstable", "reports", "failure_count")}
        envelope = dict(schema="AIOS_BP_V4_PYTEST_OBSERVATION", version=1, profile=profile,
                        toolchain=dict(V2_TOOLCHAIN), exit_status=observed["exit_code"],
                        controller_collection=observed["collection"]["identity"], exact_collection=observed["collection"],
                        failure_diagnostics=dict(canonical=canonical, reported_count=canonical["failure_count"]))
        if collect:
            defect = state["collection_defect"]
            if defect == "missing":
                envelope.pop("exact_collection")
            elif defect == "conflicting":
                envelope["controller_collection"] = {**envelope["controller_collection"], "digest": "0" * 64}
            elif defect == "unstable":
                canonical.update(complete=False, unstable=True)
            elif defect == "profile":
                envelope["profile"] = {**profile, "workers": 2}
            elif defect == "toolchain" and sha == V2_BASE:
                envelope["toolchain"]["python_version"] = "changed"
        if not broad and not collect:
            defect = state["projection_defect"]
            if defect == "collection":
                envelope.pop("exact_collection")
            elif defect == "profile":
                envelope["profile"] = {**profile, "workers": 2}
            elif defect == "fingerprint":
                for report in canonical["reports"]:
                    if report["outcome"] == "FAIL":
                        report["fingerprint"] = "0" * 64
            elif defect == "detail":
                for report in canonical["reports"]:
                    if report["outcome"] == "FAIL":
                        report["detail"] += " context-dependent"
                        report["fingerprint"] = verification_digest(report["detail"])
            elif defect == "phase":
                for report in canonical["reports"]:
                    if report["outcome"] == "FAIL":
                        report["phase"] = "collect"
        state["calls"].append(dict(sha=sha, command=actual, collect=collect, broad=broad, nodeids=nodes,
                                   native_argv=tuple(shell) if native else None))
        output = Path(kwargs["env"]["AIOS_BP_V4_PLUGIN_OUTPUT"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(envelope), encoding="utf-8")
        return completed(observed["exit_code"], stdout=b"injected pytest execution\n")

    monkeypatch.setattr(verification_module, "_git", observe_git)
    monkeypatch.setattr(verification_module, "_optional_git", observe_git)
    monkeypatch.setattr(verification_module, "_v2_toolchain", lambda: dict(V2_TOOLCHAIN))
    monkeypatch.setattr(verification_module, "materialize_verification_subject", materialize)

    def seed_previous(failed_nodes, *, sha="d" * 40):
        profile = verification_module._v2_profile(tmp_path, sha, command, {})
        previous = task320_observation(sha, command, state["candidate_nodes"], failures=failed_nodes, profile=profile)
        raw = tmp_path / "previous.raw"
        raw.write_bytes(b"bound previous failure population")
        binding = dict(subject_sha=sha, base_sha=V2_BASE, tree_sha=sha[0] * 40, command=command,
                       profile=profile, toolchain=dict(V2_TOOLCHAIN), envelope_digest=verification_digest([]),
                       changed_files_digest=verification_digest(["tests/test_sample.py"]), failure_set_digest=verification_digest([]))
        record = dict(policy="minimum-sufficient-v2", binding=binding, candidate=previous,
                      candidate_digest=verification_digest(previous), evidence_id="RUN-previous-V001",
                      raw_path=str(raw), raw_digest=hashlib.sha256(raw.read_bytes()).hexdigest())
        cache = tmp_path / "cache"
        cache.mkdir(exist_ok=True)
        (cache / "previous.json").write_text(json.dumps(record), encoding="utf-8")
        return sha

    def execute(run_id="RUN-task320", *, operation="PRIMARY", correction_base=None, platform="posix"):
        return execute_minimum_verification((command,), run_id=run_id, base_sha=V2_BASE, subject_sha=V2_CANDIDATE,
            repository=tmp_path, raw_directory=tmp_path / run_id, cache_directory=tmp_path / "cache",
            modification_scope=("tests/test_sample.py",), operation=operation, runner=runner, platform=platform,
            environment={}, subject_check=lambda *a, **k: None, correction_base_sha=correction_base)
    return execute, state, seed_previous


def test_task320_run313007_shaped_delta_finds_five_failures_without_broad_replay(task320_execution):
    execute, state, _ = task320_execution
    state["base_nodes"] = [f"tests/test_sample.py::test_{i:03d}" for i in range(776)]
    new = [f"tests/test_sample.py::test_new_{i:03d}" for i in range(20)]
    state["candidate_nodes"] = state["base_nodes"] + new
    state["candidate_failures"] = set(new[:5])
    with pytest.raises(RuntimeVerificationError, match="candidate-only delta") as caught:
        execute()
    executed = [c for c in state["calls"] if not c["collect"]]
    assert len(executed) == 1 and len(executed[0]["nodeids"]) == 20
    assert executed[0]["sha"] == "b" * 40 and not executed[0]["broad"]
    record = caught.value.evidence[0].verification
    assert record["candidate"]["failure_count"] == 5
    assert {r["classification"] for r in record["attribution"]} == {"CANDIDATE_ONLY"}
    assert {r["nodeid"] for r in record["attribution"]} == set(new[:5])
    assert record["early_probe"] is True


def test_task320_passing_delta_requires_the_entire_authored_focused_command(task320_execution, tmp_path):
    execute, state, _ = task320_execution
    result = execute()
    executed = [c for c in state["calls"] if not c["collect"]]
    assert [len(c["nodeids"]) for c in executed] == [1, 9]
    assert [c["broad"] for c in executed] == [False, True]
    assert len(result) == 1 and result[0].source.command == executed[-1]["command"]
    assert "early_probe" not in result[0].verification
    audit = json.loads((tmp_path / "RUN-task320" / "minimum-sufficient-v2-plan.json").read_text(encoding="utf-8"))
    assert audit["policy"] == "minimum-sufficient-v2"
    assert [p["kind"] for p in audit["probes"]] == ["EXACT_COLLECTION", "EXACT_COLLECTION", "CANDIDATE_DELTA"]
    assert all(p["early_probe"] for p in audit["probes"])
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1


@pytest.mark.parametrize("defect", ["missing", "conflicting", "unstable", "profile", "toolchain", "near-full", "observer"])
def test_task320_unavailable_or_uneconomical_delta_falls_back_to_authored_proof(task320_execution, defect):
    execute, state, _ = task320_execution
    if defect == "near-full":
        state["base_nodes"] = state["candidate_nodes"][:1]
    elif defect == "observer":
        state["observer_mismatch"] = True
    else:
        state["collection_defect"] = defect
    result = execute()
    executed = [c for c in state["calls"] if not c["collect"]]
    assert len(executed) == 1 and executed[0]["broad"]
    assert len(result) == 1 and result[0].result.exit_code == 0


@pytest.mark.parametrize("operation", ["REPAIR", "REMEDIATION", "DIRECT_CANDIDATE"])
def test_task320_correction_persistent_exact_failure_stops_before_covering_command(task320_execution, operation):
    execute, state, seed_previous = task320_execution
    node = state["candidate_nodes"][0]
    previous = seed_previous({node})
    state["candidate_failures"] = {node}
    with pytest.raises(RuntimeVerificationError, match="previous-failure probe") as caught:
        execute(operation=operation, correction_base=previous)
    assert len(state["calls"]) == 1
    assert state["calls"][0]["nodeids"] == [node] and not state["calls"][0]["broad"]
    binding = caught.value.evidence[0].verification["binding"]
    assert binding["correction_base_sha"] == previous
    assert binding["base_sha"] == "a" * 40
    assert binding["projection_of"] == "python -m pytest -q tests/test_sample.py"


def test_task320_correction_exact_pass_still_requires_broader_authored_proof(task320_execution):
    execute, state, seed_previous = task320_execution
    previous = seed_previous({state["candidate_nodes"][0]})
    result = execute(operation="REPAIR", correction_base=previous)
    assert len(state["calls"]) == 2
    assert [c["broad"] for c in state["calls"]] == [False, True]
    assert len(result) == 1 and result[0].source.command == state["calls"][-1]["command"]


def test_task320_tampered_previous_raw_evidence_cannot_authorize_a_correction_probe(task320_execution, tmp_path):
    execute, state, seed_previous = task320_execution
    previous = seed_previous({state["candidate_nodes"][0]})
    (tmp_path / "previous.raw").write_bytes(b"tampered previous failure")
    result = execute(operation="REPAIR", correction_base=previous)
    assert len(state["calls"]) == 1 and state["calls"][0]["broad"]
    assert len(result) == 1 and result[0].result.exit_code == 0


def test_task320_broad_failure_exact_reproduction_proves_absence_without_776_item_base_replay(task320_execution):
    execute, state, _ = task320_execution
    state["base_nodes"] = [f"tests/test_sample.py::test_{i:03d}" for i in range(776)]
    new = [f"tests/test_sample.py::test_new_{i:03d}" for i in range(20)]
    state["candidate_nodes"] = state["base_nodes"] + new
    state["candidate_failures"] = set(new[:5])
    with pytest.raises(RuntimeVerificationError) as caught:
        execute(operation="REPAIR")  # No previous bound correction failures.
    executed = [c for c in state["calls"] if not c["collect"]]
    assert [len(c["nodeids"]) for c in executed] == [796, 5]
    assert all(c["sha"] == "b" * 40 for c in executed)
    record = caught.value.evidence[0].verification
    assert {r["classification"] for r in record["attribution"]} == {"CANDIDATE_ONLY"}
    assert record["failure_projection"]["base_collection"]["collection"]["identity"]["count"] == 776
    assert "base" not in record["failure_projection"]


@pytest.mark.parametrize("base_pass", [False, True])
def test_task320_same_identity_base_projection_preserves_exact_attribution(task320_execution, base_pass):
    execute, state, _ = task320_execution
    node = state["base_nodes"][0]
    state["candidate_failures"] = {node}
    state["base_failures"] = set() if base_pass else {node}
    if base_pass:
        with pytest.raises(RuntimeVerificationError) as caught:
            execute(operation="REPAIR")
        result = caught.value.evidence
    else:
        result = execute(operation="REPAIR")
    executed = [c for c in state["calls"] if not c["collect"]]
    assert [c["broad"] for c in executed] == [True, False, False]
    assert [c["sha"] for c in executed] == ["b" * 40, "b" * 40, "a" * 40]
    record = result[0].verification
    assert record["candidate"]["exit_code"] == 1
    assert record["attribution"][0]["classification"] == ("CANDIDATE_REGRESSION" if base_pass else "PRE_EXISTING_BASELINE")


@pytest.mark.parametrize("defect", ["pass", "detail", "phase", "fingerprint", "collection", "profile"])
def test_task320_candidate_projection_nonreproduction_uses_broad_attribution(task320_execution, defect):
    execute, state, _ = task320_execution
    node = state["base_nodes"][0]
    state["candidate_failures"] = {node}
    if defect == "pass":
        state["nonreproduction"] = True
    else:
        state["projection_defect"] = defect
    with pytest.raises(RuntimeVerificationError) as caught:
        execute(operation="REPAIR")
    executed = [c for c in state["calls"] if not c["collect"]]
    assert [c["broad"] for c in executed] == [True, False, True]
    assert executed[-1]["sha"] == "a" * 40
    record = caught.value.evidence[0].verification
    assert "failure_projection" not in record
    assert record["attribution"][0]["classification"] == "CANDIDATE_REGRESSION"


def test_task320_missing_base_collection_requires_broad_base_truth(task320_execution):
    execute, state, _ = task320_execution
    node = state["base_nodes"][0]
    state["candidate_failures"] = state["base_failures"] = {node}
    state["collection_defect"] = "missing"
    result = execute(operation="REPAIR")
    executed = [c for c in state["calls"] if not c["collect"]]
    assert [c["broad"] for c in executed] == [True, False, True]
    assert executed[-1]["sha"] == "a" * 40
    record = result[0].verification
    assert "failure_projection" not in record
    assert record["base"]["command"] == record["candidate"]["command"]
    assert record["attribution"][0]["classification"] == "PRE_EXISTING_BASELINE"


def test_task320_nonreproduction_pass_is_discharged_only_by_complete_broad_base_proof(task320_execution):
    execute, state, _ = task320_execution
    node = state["base_nodes"][0]
    state["candidate_failures"] = state["base_failures"] = {node}
    state["nonreproduction"] = True
    result = execute(operation="REPAIR")
    assert state["calls"][-1]["broad"] and state["calls"][-1]["sha"] == "a" * 40
    assert "failure_projection" not in result[0].verification
    assert result[0].verification["attribution"][0]["classification"] == "PRE_EXISTING_BASELINE"
    assert result[0].result.exit_code == 1


def test_task320_collection_toolchain_change_stays_blocking_even_after_broad_fallback(task320_execution):
    execute, state, _ = task320_execution
    node = state["base_nodes"][0]
    state["candidate_failures"] = state["base_failures"] = {node}
    state["collection_defect"] = "toolchain"
    with pytest.raises(RuntimeVerificationError) as caught:
        execute(operation="REPAIR")
    assert state["calls"][-1]["broad"] and state["calls"][-1]["sha"] == "a" * 40
    record = caught.value.evidence[0].verification
    assert record["base"]["unstable"] is True
    assert record["attribution"][0]["classification"] == "UNRESOLVED"


def test_task320_collection_disagreement_cannot_make_a_passing_broad_command_permissive(task320_execution):
    execute, state, _ = task320_execution
    state["broad_disagreement"] = True
    with pytest.raises(RuntimeVerificationError) as caught:
        execute()
    assert caught.value.evidence[0].result.exit_code == 0
    assert caught.value.evidence[0].verification["candidate"]["unstable"] is True


def test_task320_projection_raw_tampering_invalidates_canonical_cache(task320_execution, tmp_path):
    execute, state, _ = task320_execution
    node = state["base_nodes"][0]
    state["candidate_failures"] = state["base_failures"] = {node}
    result = execute(operation="REPAIR")
    cache = tmp_path / "cache"
    assert verification_module._v2_cache_records(cache)
    raw = Path(result[0].verification["failure_projection"]["candidate_raw_path"])
    raw.write_bytes(b"tampered exact reproduction")
    assert verification_module._v2_cache_records(cache) == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows native argument transport")
def test_task320_windows_generated_probe_transports_parameterized_nodeids_literally(task320_execution):
    execute, state, _ = task320_execution
    node = "tests/test_sample.py::test_value[space 'single' \"double\" \\path $() & ; [nested]]"
    state["candidate_nodes"] = state["base_nodes"] + [node]
    state["candidate_failures"] = {node}
    with pytest.raises(RuntimeVerificationError, match="candidate-only delta"):
        execute(platform="nt")
    native = state["calls"][-1]["native_argv"]
    assert native == ("python", "-m", "pytest", "-q", node)
    transported = subprocess.run((native[0], "-c", "import json,sys; print(json.dumps(sys.argv[1:]))", *native[4:]),
                                 capture_output=True, text=False, check=True)
    assert json.loads(transported.stdout.decode("utf-8")) == [node]


class RecordingRunner:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return self.outcomes[len(self.calls) - 1]


def completed(returncode=0, stdout=b"ok\n", stderr=b""):
    return subprocess.CompletedProcess(
        ("shell",), returncode=returncode, stdout=stdout, stderr=stderr
    )


def git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *args),
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def test_materializes_one_clean_exact_subject_with_independent_git_state(
    tmp_path: Path,
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    git(control, "init")
    git(control, "config", "user.name", "Test")
    git(control, "config", "user.email", "test@example.invalid")
    (control / "tracked.txt").write_text("candidate\n", encoding="utf-8")
    git(control, "add", "tracked.txt")
    git(control, "commit", "-m", "candidate")
    candidate = git(control, "rev-parse", "HEAD")

    with materialize_verification_subject(
        control, run_id="RUN-156-001", subject_sha=candidate
    ) as subject:
        assert subject != control
        assert (subject / ".git").is_dir()
        assert git(subject, "rev-parse", "HEAD") == candidate
        assert git(subject, "status", "--porcelain") == ""
        git(subject, "config", "aios.subject", "isolated")
        git(subject, "branch", "subject-only")
        control_config = subprocess.run(
            ("git", "-C", str(control), "config", "--get", "aios.subject"),
            capture_output=True,
            text=True,
            check=False,
        )
        assert control_config.returncode == 1
        assert "subject-only" not in git(control, "branch", "--list")
        subject_root = subject.parent

    assert not subject_root.exists()


def test_materialized_subject_ignores_ambient_and_control_checkout_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    git(control, "init")
    git(control, "config", "user.name", "Test")
    git(control, "config", "user.email", "test@example.invalid")
    canonical = b"first\nsecond\n"
    (control / "sentinel.txt").write_bytes(canonical)
    git(control, "add", "sentinel.txt")
    git(control, "commit", "-m", "LF-only candidate")
    candidate = git(control, "rev-parse", "HEAD")

    host_attributes = tmp_path / "host-attributes"
    host_attributes.write_bytes(b"*.txt text eol=crlf\n")
    host_config = tmp_path / "host-gitconfig"
    git(control, "config", "--file", str(host_config), "core.autocrlf", "true")
    git(control, "config", "--file", str(host_config), "core.eol", "crlf")
    git(
        control,
        "config",
        "--file",
        str(host_config),
        "core.attributesFile",
        str(host_attributes),
    )
    git(control, "config", "--local", "core.autocrlf", "true")
    git(control, "config", "--local", "core.eol", "crlf")
    git(control, "config", "--local", "core.attributesFile", str(host_attributes))
    control_config = (control / ".git" / "config").read_bytes()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(host_config))

    with materialize_verification_subject(
        control, run_id="RUN-205-001", subject_sha=candidate
    ) as subject:
        blob = subprocess.run(
            ("git", "-C", str(control), "show", f"{candidate}:sentinel.txt"),
            capture_output=True,
            check=True,
        )
        assert blob.stdout == canonical
        assert (subject / "sentinel.txt").read_bytes() == canonical
        assert git(subject, "rev-parse", "HEAD") == candidate
        assert git(subject, "status", "--porcelain") == ""
        assert git(subject, "config", "--local", "core.autocrlf") == "false"
        assert git(subject, "config", "--local", "core.eol") == "lf"
        assert git(subject, "config", "--local", "core.attributesFile") != str(
            host_attributes
        )
        assert (control / ".git" / "config").read_bytes() == control_config


def test_materialized_subject_honors_tracked_checkout_attributes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    git(control, "init")
    git(control, "config", "user.name", "Test")
    git(control, "config", "user.email", "test@example.invalid")
    (control / ".gitattributes").write_bytes(
        b"explicit-crlf.txt text eol=crlf\nexplicit-lf.txt text eol=lf\n"
    )
    canonical = b"first\nsecond\n"
    (control / "explicit-crlf.txt").write_bytes(canonical)
    (control / "explicit-lf.txt").write_bytes(canonical)
    git(control, "add", ".gitattributes", "explicit-crlf.txt", "explicit-lf.txt")
    git(control, "commit", "-m", "tracked checkout policy")
    candidate = git(control, "rev-parse", "HEAD")
    host_config = tmp_path / "host-gitconfig"
    git(control, "config", "--file", str(host_config), "core.autocrlf", "true")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(host_config))

    with materialize_verification_subject(
        control, run_id="RUN-205-002", subject_sha=candidate
    ) as subject:
        assert (subject / "explicit-crlf.txt").read_bytes() == b"first\r\nsecond\r\n"
        assert (subject / "explicit-lf.txt").read_bytes() == canonical
        assert git(subject, "rev-parse", "HEAD") == candidate
        assert git(subject, "status", "--porcelain") == ""


def test_materialized_subject_stays_exact_when_control_head_moves(
    tmp_path: Path,
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    git(control, "init")
    git(control, "config", "user.name", "Test")
    git(control, "config", "user.email", "test@example.invalid")
    (control / "tracked.txt").write_text("candidate\n", encoding="utf-8")
    git(control, "add", "tracked.txt")
    git(control, "commit", "-m", "candidate")
    candidate = git(control, "rev-parse", "HEAD")

    with materialize_verification_subject(
        control, run_id="RUN-156-002", subject_sha=candidate
    ) as subject:
        (control / "tracked.txt").write_text("later\n", encoding="utf-8")
        git(control, "add", "tracked.txt")
        git(control, "commit", "-m", "move control")

        assert git(control, "rev-parse", "HEAD") != candidate
        assert git(subject, "rev-parse", "HEAD") == candidate
        assert (subject / "tracked.txt").read_text(encoding="utf-8") == "candidate\n"
        assert git(subject, "status", "--porcelain") == ""


def test_materialized_subject_has_empty_local_scratch_for_relative_command(
    tmp_path: Path,
) -> None:
    control = tmp_path / "control"
    control.mkdir()
    git(control, "init")
    git(control, "config", "user.name", "Test")
    git(control, "config", "user.email", "test@example.invalid")
    (control / "basetemp_probe.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "target = Path(sys.argv[1].split('=', 1)[1])\n"
        "target.mkdir()\n"
        "(target / 'probe.txt').write_text('ready', encoding='utf-8')\n",
        encoding="utf-8",
    )
    git(control, "add", "basetemp_probe.py")
    git(control, "commit", "-m", "candidate")
    candidate = git(control, "rev-parse", "HEAD")
    control_scratch = control / ".git" / "aios"
    control_scratch.mkdir()
    (control_scratch / "control-sentinel").write_text("control only", encoding="utf-8")
    command = "python basetemp_probe.py --basetemp=.git/aios/pytest-task204"

    with materialize_verification_subject(
        control, run_id="RUN-204-001", subject_sha=candidate
    ) as subject:
        subject_root = subject.parent
        scratch = subject / ".git" / "aios"
        assert scratch.is_dir()
        assert list(scratch.iterdir()) == []
        assert not (scratch / "control-sentinel").exists()

        evidence = execute_verification(
            (command,),
            run_id="RUN-204-001",
            subject_sha=candidate,
            repository=subject,
            raw_directory=control_scratch / "verification",
        )

        assert evidence[0].source.command == command
        assert evidence[0].subject_sha == candidate
        assert evidence[0].result.exit_code == 0
        assert list(scratch.iterdir()) == [scratch / "pytest-task204"]
        assert (scratch / "pytest-task204" / "probe.txt").read_text(
            encoding="utf-8"
        ) == "ready"
        assert git(subject, "rev-parse", "HEAD") == candidate
        assert git(subject, "status", "--porcelain") == ""

    assert not subject_root.exists()
    assert (control_scratch / "control-sentinel").read_text(
        encoding="utf-8"
    ) == "control only"


def test_relative_git_basetemp_command_runs_unchanged_from_subject_repo(
    tmp_path: Path,
) -> None:
    subject = tmp_path / "historical-subject"
    subject.mkdir()
    (subject / ".git").mkdir()
    (subject / "basetemp_probe.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "Path(sys.argv[1].split('=', 1)[1]).mkdir(parents=True)\n",
        encoding="utf-8",
    )
    command = (
        "python basetemp_probe.py "
        "--basetemp=.git/aios/pytest-historical-subject"
    )

    evidence = execute_verification(
        (command,),
        run_id="RUN-148-TEST",
        subject_sha="a" * 40,
        repository=subject,
        raw_directory=tmp_path / "control-runtime" / "verification",
    )

    assert evidence[0].source.command == command
    assert evidence[0].result.exit_code == 0
    assert (
        subject / ".git" / "aios" / "pytest-historical-subject"
    ).is_dir()


def test_executes_posix_commands_once_in_exact_order_and_builds_evidence(
    tmp_path: Path,
) -> None:
    commands = (
        "printf 'one  two'",
        "git status --porcelain",
        "printf 'one  two'",
    )
    runner = RecordingRunner(
        [completed(stdout=b"one  two"), completed(), completed(stdout=b"one  two")]
    )
    raw = tmp_path / ".git" / "aios" / "verification" / "RUN-027-001"

    evidence = execute_verification(
        commands,
        run_id="RUN-027-001",
        subject_sha="abc123",
        repository=tmp_path,
        raw_directory=raw,
        runner=runner,
        platform="posix",
    )

    assert [call[0] for call in runner.calls] == [
        ("/bin/sh", "-c", commands[0]),
        ("/bin/sh", "-c", commands[1]),
        ("/bin/sh", "-c", commands[2]),
    ]
    assert all(call[1]["cwd"] == tmp_path.resolve() for call in runner.calls)
    assert all(call[1]["capture_output"] is True for call in runner.calls)
    assert all(call[1]["text"] is False for call in runner.calls)
    assert all(call[1]["check"] is False for call in runner.calls)
    assert [item.source.command for item in evidence] == list(commands)
    assert [item.evidence_id for item in evidence] == [
        "RUN-027-001-V001",
        "RUN-027-001-V002",
        "RUN-027-001-V003",
    ]
    assert all(item.run_id == "RUN-027-001" for item in evidence)
    assert all(item.subject_sha == "abc123" for item in evidence)
    assert all(Path(item.raw_path).is_relative_to(raw) for item in evidence)


def test_windows_uses_one_noninteractive_powershell_wrapper_with_utf8_preamble(
    tmp_path: Path,
) -> None:
    command = "git diff --check"
    runner = RecordingRunner([completed()])

    evidence = execute_verification(
        (command,),
        run_id="RUN-027-002",
        subject_sha="def456",
        repository=tmp_path,
        raw_directory=tmp_path / ".git" / "aios" / "verification",
        runner=runner,
        platform="nt",
        environment={"EXISTING_VAR": "val"},
    )

    assert runner.calls[0][0] == (
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        f"& {{ {command} }}; exit $LASTEXITCODE",
    )
    assert runner.calls[0][1]["env"]["PYTHONIOENCODING"] == "utf-8"
    assert "PYTHONUTF8" not in runner.calls[0][1]["env"]
    assert runner.calls[0][1]["env"]["EXISTING_VAR"] == "val"
    isolated = runner.calls[0][1]["env"]["TEMP"]
    assert runner.calls[0][1]["env"]["TMP"] == isolated
    assert runner.calls[0][1]["env"]["TMPDIR"] == isolated
    isolated_path = Path(isolated)
    assert isolated_path.name.startswith("aios-verification-RUN-027-002-")
    assert not isolated_path.is_relative_to(tmp_path.resolve())
    assert not isolated_path.exists()
    assert evidence[0].source.command == command


def test_windows_temp_isolation_overrides_conflicting_ambient_values_for_all_commands(
    tmp_path: Path,
) -> None:
    commands = ("first --unchanged", "second --unchanged")
    runner = RecordingRunner([completed(), completed()])
    ambient = {
        "Temp": "ambient-temp",
        "TMP": "ambient-tmp",
        "TMPDIR": "ambient-tmpdir",
        "UNCHANGED": "preserved",
    }

    execute_verification(
        commands,
        run_id="RUN-120-001",
        subject_sha="abc123",
        repository=tmp_path,
        raw_directory=tmp_path / "runtime" / "RUN-120-001",
        runner=runner,
        platform="nt",
        environment=ambient,
    )

    assert len(runner.calls) == 2
    assert [call[0][-1] for call in runner.calls] == [
        f"[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        f"& {{ {command} }}; exit $LASTEXITCODE"
        for command in commands
    ]
    environments = [call[1]["env"] for call in runner.calls]
    isolated = environments[0]["TEMP"]
    assert all(
        env["TEMP"] == env["TMP"] == env["TMPDIR"] == isolated
        for env in environments
    )
    assert all("Temp" not in env for env in environments)
    assert all(env["UNCHANGED"] == "preserved" for env in environments)
    assert ambient == {
        "Temp": "ambient-temp",
        "TMP": "ambient-tmp",
        "TMPDIR": "ambient-tmpdir",
        "UNCHANGED": "preserved",
    }
    assert not Path(isolated).exists()


def test_windows_temp_isolation_does_not_mutate_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEMP", "process-temp")
    monkeypatch.setenv("TMP", "process-tmp")
    monkeypatch.setenv("TMPDIR", "process-tmpdir")
    runner = RecordingRunner([completed()])

    execute_verification(
        ("one-command",),
        run_id="RUN-120-006",
        subject_sha="abc123",
        repository=tmp_path,
        raw_directory=tmp_path / "runtime" / "RUN-120-006",
        runner=runner,
        platform="nt",
    )

    assert os.environ["TEMP"] == "process-temp"
    assert os.environ["TMP"] == "process-tmp"
    assert os.environ["TMPDIR"] == "process-tmpdir"
    assert runner.calls[0][1]["env"]["TEMP"] != "process-temp"


def test_windows_verification_executions_use_distinct_temp_roots(
    tmp_path: Path,
) -> None:
    first_runner = RecordingRunner([completed()])
    second_runner = RecordingRunner([completed()])

    execute_verification(
        ("same-command",),
        run_id="RUN-120-002",
        subject_sha="abc123",
        repository=tmp_path,
        raw_directory=tmp_path / "runtime" / "RUN-120-002",
        runner=first_runner,
        platform="nt",
        environment={},
    )
    execute_verification(
        ("same-command",),
        run_id="RUN-120-003",
        subject_sha="abc123",
        repository=tmp_path,
        raw_directory=tmp_path / "runtime" / "RUN-120-003",
        runner=second_runner,
        platform="nt",
        environment={},
    )

    first_temp = first_runner.calls[0][1]["env"]["TEMP"]
    second_temp = second_runner.calls[0][1]["env"]["TEMP"]
    assert first_temp != second_temp
    assert not Path(first_temp).exists()
    assert not Path(second_temp).exists()


def test_windows_temp_isolation_failure_stops_before_command_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "runtime" / "RUN-120-004"
    runner = RecordingRunner([completed()])

    def fail_create(*args, **kwargs):
        raise OSError("isolated create denied")

    monkeypatch.setattr(verification_module.tempfile, "mkdtemp", fail_create)

    with pytest.raises(RuntimeVerificationError, match="could not be isolated"):
        execute_verification(
            ("must-not-run",),
            run_id="RUN-120-004",
            subject_sha="abc123",
            repository=tmp_path,
            raw_directory=raw,
            runner=runner,
            platform="nt",
            environment={},
        )

    assert runner.calls == []


def test_windows_temp_is_outside_repository_git_ancestry_and_shared_until_cleanup(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "subject"
    (repository / ".git").mkdir(parents=True)
    seen: list[Path] = []

    def runner(command, **kwargs):
        isolated = Path(kwargs["env"]["TEMP"])
        assert isolated.is_dir()
        assert not isolated.is_relative_to(repository.resolve())
        assert not any((parent / ".git").exists() for parent in isolated.parents)
        seen.append(isolated)
        return completed()

    raw = repository / ".git" / "aios" / "verification" / "RUN-120-007"
    execute_verification(
        ("first", "second"),
        run_id="RUN-120-007",
        subject_sha="abc123",
        repository=repository,
        raw_directory=raw,
        runner=runner,
        platform="nt",
        environment={},
    )

    assert len(seen) == 2
    assert seen[0] == seen[1]
    assert not seen[0].exists()
    assert (raw / "RUN-120-007-V001.raw").is_file()
    assert (raw / "RUN-120-007-V002.raw").is_file()


def test_windows_temp_cleanup_failure_fails_closed_and_preserves_raw_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = RecordingRunner([completed(stdout=b"captured\n")])
    raw = tmp_path / "runtime" / "RUN-120-008"
    real_rmtree = verification_module.shutil.rmtree
    isolated: Path | None = None

    def fail_cleanup(path, *args, **kwargs):
        nonlocal isolated
        isolated = Path(path)
        raise OSError("isolated cleanup denied")

    monkeypatch.setattr(verification_module.shutil, "rmtree", fail_cleanup)
    try:
        with pytest.raises(
            RuntimeVerificationError, match="could not be cleaned"
        ) as caught:
            execute_verification(
                ("one-command",),
                run_id="RUN-120-008",
                subject_sha="abc123",
                repository=tmp_path,
                raw_directory=raw,
                runner=runner,
                platform="nt",
                environment={},
            )
    finally:
        if isolated is not None and isolated.exists():
            real_rmtree(isolated)

    assert len(caught.value.evidence) == 1
    assert caught.value.evidence[0].result.exit_code == 0
    assert (raw / "RUN-120-008-V001.raw").read_bytes().endswith(
        b"captured\n\nSTDERR\n"
    )


@pytest.mark.parametrize("scenario", [
    "command_failure", "returned_failure", "start_failure", "invalid_utf8", "unexpected_exception",
])
def test_windows_primary_cause_survives_bounded_cleanup_diagnostic(tmp_path, monkeypatch, scenario):
    cleanup_calls = []
    original = ValueError("original runner failure") if scenario == "unexpected_exception" else OSError("command start denied")

    def runner(*args, **kwargs):
        if scenario in {"start_failure", "unexpected_exception"}:
            raise original
        return completed(7 if scenario in {"command_failure", "returned_failure"} else 0,
                         stdout=b"\xff" if scenario == "invalid_utf8" else b"primary output\n")

    def fail_cleanup(path):
        cleanup_calls.append(path)
        raise PermissionError("operator.lock sharing violation " + "x" * 4000)

    root = tmp_path / "isolated"
    monkeypatch.setattr(verification_module, "_isolated_temp_root", lambda **kwargs: root)
    monkeypatch.setattr(verification_module, "_remove_temp_root", fail_cleanup)
    error_type = ValueError if scenario == "unexpected_exception" else RuntimeVerificationError
    with pytest.raises(error_type) as caught:
        execute_verification(("one-command",), run_id="RUN-cleanup", subject_sha="a" * 40,
            repository=tmp_path, raw_directory=tmp_path / "raw", runner=runner,
            platform="nt", environment={}, stop_on_failure=scenario != "returned_failure")
    primary = caught.value
    assert cleanup_calls == [root]
    assert len(primary.__notes__) == 1
    assert "operator.lock sharing violation" in primary.__notes__[0]
    assert len(primary.__notes__[0]) <= verification_module._MAX_CLEANUP_DIAGNOSTIC_CHARS
    if scenario == "unexpected_exception":
        assert primary is original
    else:
        assert primary.cleanup_diagnostics == tuple(primary.__notes__)
        if scenario in {"command_failure", "returned_failure"}:
            assert str(primary) == "verification command failed with exit code 7: one-command"
            assert primary.evidence[0].result.exit_code == 7
            assert Path(primary.evidence[0].raw_path).read_bytes().startswith(b"STDOUT\nprimary output\n")
        elif scenario == "start_failure":
            assert "could not start" in str(primary) and primary.__cause__ is original
        else:
            assert "not strict UTF-8" in str(primary)
            assert isinstance(primary.__cause__, UnicodeDecodeError)
            assert (tmp_path / "raw" / "RUN-cleanup-V001.raw").read_bytes().startswith(b"STDOUT\n\xff")


@pytest.mark.parametrize("scenario", ["materialization_failure", "body_failure", "success"])
def test_subject_cleanup_preserves_primary_or_blocks_success(tmp_path, monkeypatch, scenario):
    root = tmp_path / "isolated"
    root.mkdir()
    subject_sha = "a" * 40
    primary = RuntimeVerificationError("primary V2 blocking cause")
    cleanup_calls = []

    def observe_git(repository, *args, **kwargs):
        if "clone" in args:
            if scenario == "materialization_failure":
                raise OSError("clone failed")
            (Path(args[-1]) / ".git").mkdir(parents=True)
        return subject_sha if args[0] == "rev-parse" else ""

    def fail_cleanup(path):
        cleanup_calls.append(path)
        raise PermissionError("subject cleanup denied")

    monkeypatch.setattr(verification_module, "_git", observe_git)
    monkeypatch.setattr(verification_module, "_optional_git", lambda *args, **kwargs: None)
    monkeypatch.setattr(verification_module, "_isolated_temp_root", lambda **kwargs: root)
    monkeypatch.setattr(verification_module, "_remove_temp_root", fail_cleanup)
    with pytest.raises(RuntimeVerificationError) as caught:
        with materialize_verification_subject(tmp_path, run_id="RUN-cleanup", subject_sha=subject_sha):
            if scenario == "body_failure":
                raise primary
    assert cleanup_calls == [root]
    if scenario == "success":
        assert "could not be cleaned" in str(caught.value)
        assert isinstance(caught.value.__cause__, PermissionError)
    else:
        assert "subject cleanup denied" in caught.value.cleanup_diagnostics[0]
        if scenario == "body_failure":
            assert caught.value is primary
        else:
            assert "could not be materialized: clone failed" in str(caught.value)
            assert isinstance(caught.value.__cause__, OSError)


def test_cleanup_diagnostic_population_remains_finite():
    primary = RuntimeVerificationError("primary cause")
    for _ in range(10):
        verification_module._retain_cleanup_failure(primary, PermissionError("locked"))
    assert len(primary.cleanup_diagnostics) == len(primary.__notes__) == verification_module._MAX_CLEANUP_DIAGNOSTICS
    assert str(primary) == "primary cause"


def test_v2_blocking_outcome_is_raised_before_subject_cleanup(v2_injected_execution, tmp_path, monkeypatch):
    execute, state = v2_injected_execution
    state["base_pass"] = True
    original_materialize = verification_module.materialize_verification_subject

    @contextmanager
    def materialize(*args, **kwargs):
        with original_materialize(*args, **kwargs) as checkout:
            primary = None
            try:
                yield checkout
            except BaseException as exc:
                primary = exc
                raise
            finally:
                verification_module._cleanup_verification_root(checkout, primary=primary)

    def fail_cleanup(path):
        raise PermissionError("V2 subject sharing violation")

    monkeypatch.setattr(verification_module, "materialize_verification_subject", materialize)
    monkeypatch.setattr(verification_module, "_remove_temp_root", fail_cleanup)
    with pytest.raises(RuntimeVerificationError, match="V2 verification remains blocking") as caught:
        execute("RUN-cleanup")
    assert len(caught.value.cleanup_diagnostics) == 2
    assert caught.value.evidence[0].result.exit_code == 1
    assert {r["classification"] for r in caught.value.evidence[0].verification["attribution"]} == {"CANDIDATE_REGRESSION"}


def test_temp_cleanup_recovers_read_only_git_object_tree(tmp_path: Path) -> None:
    temp_root = tmp_path / "isolated-verification-root"
    object_directory = temp_root / "smoke-repo" / ".git" / "objects" / "0a"
    object_directory.mkdir(parents=True)
    object_file = object_directory / "76e58f038b4db6cd072967d84f862c896a01f2"
    object_file.write_bytes(b"git-object")
    object_file.chmod(stat.S_IREAD)
    object_directory.chmod(stat.S_IREAD)

    verification_module._remove_temp_root(temp_root)

    assert not temp_root.exists()


def test_temp_cleanup_retries_transient_permission_failure_until_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_root = tmp_path / "isolated-verification-root"
    temp_root.mkdir()
    real_rmtree = verification_module.shutil.rmtree
    attempts = 0
    sleeps: list[float] = []

    def transient_rmtree(path, *args, **kwargs):
        error = PermissionError("Git object is temporarily locked")

        def transient_remove(locked_path):
            nonlocal attempts
            attempts += 1
            if attempts <= 3:
                raise PermissionError("Git object is temporarily locked")
            real_rmtree(locked_path)

        kwargs["onerror"](
            transient_remove,
            os.fspath(path),
            (PermissionError, error, None),
        )

    monkeypatch.setattr(verification_module.shutil, "rmtree", transient_rmtree)
    monkeypatch.setattr(verification_module.time, "sleep", sleeps.append)

    verification_module._remove_temp_root(temp_root)

    assert attempts == 4
    assert sleeps == [0.05, 0.1, 0.2]
    assert not temp_root.exists()


def test_temp_cleanup_persistent_permission_failure_exhausts_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_root = tmp_path / "isolated-verification-root"
    temp_root.mkdir()
    attempts = 0
    now = 0.0

    def persistent_rmtree(path, *args, **kwargs):
        error = PermissionError("Git object remains locked")

        def persistent_remove(locked_path):
            nonlocal attempts
            attempts += 1
            raise PermissionError("Git object remains locked")

        kwargs["onerror"](
            persistent_remove,
            os.fspath(path),
            (PermissionError, error, None),
        )

    def advance(delay: float) -> None:
        nonlocal now
        now += delay

    monkeypatch.setattr(verification_module.shutil, "rmtree", persistent_rmtree)
    monkeypatch.setattr(verification_module.time, "monotonic", lambda: now)
    monkeypatch.setattr(verification_module.time, "sleep", advance)
    monkeypatch.setattr(
        verification_module, "_TEMP_CLEANUP_MAX_ELAPSED_SECONDS", 0.2
    )

    with pytest.raises(PermissionError, match="remains locked"):
        verification_module._remove_temp_root(temp_root)

    assert attempts == 4
    assert now == pytest.approx(0.2)
    assert temp_root.exists()
    temp_root.rmdir()


def test_windows_nonzero_remains_canonical_failure_without_retry(
    tmp_path: Path,
) -> None:
    command = "tool-neutral-command"
    runner = RecordingRunner([completed(returncode=9, stderr=b"failed\n")])

    with pytest.raises(RuntimeVerificationError) as caught:
        execute_verification(
            (command,),
            run_id="RUN-120-005",
            subject_sha="abc123",
            repository=tmp_path,
            raw_directory=tmp_path / "runtime" / "RUN-120-005",
            runner=runner,
            platform="nt",
            environment={},
        )

    assert len(runner.calls) == 1
    assert caught.value.evidence[0].source.command == command
    assert caught.value.evidence[0].result.exit_code == 9
    assert not Path(runner.calls[0][1]["env"]["TEMP"]).exists()


def test_posix_environment_not_mutated_with_windows_encoding(
    tmp_path: Path,
) -> None:
    command = "git diff --check"
    runner = RecordingRunner([completed()])

    execute_verification(
        (command,),
        run_id="RUN-027-007",
        subject_sha="def456",
        repository=tmp_path,
        raw_directory=tmp_path / ".git" / "aios" / "verification",
        runner=runner,
        platform="posix",
        environment={"CUSTOM": "123"},
    )

    assert runner.calls[0][0] == ("/bin/sh", "-c", command)
    assert runner.calls[0][1]["env"] == {"CUSTOM": "123"}


def test_first_nonzero_records_raw_evidence_and_stops(tmp_path: Path) -> None:
    runner = RecordingRunner(
        [completed(), completed(returncode=7, stderr=b"failed\n"), completed()]
    )
    raw = tmp_path / ".git" / "aios" / "verification"

    with pytest.raises(RuntimeVerificationError) as caught:
        execute_verification(
            ("first", "second", "never"),
            run_id="RUN-027-003",
            subject_sha="abc123",
            repository=tmp_path,
            raw_directory=raw,
            runner=runner,
            platform="posix",
        )

    assert len(runner.calls) == 2
    assert [item.source.command for item in caught.value.evidence] == [
        "first",
        "second",
    ]
    assert caught.value.evidence[-1].result.exit_code == 7
    assert (raw / "RUN-027-003-V002.raw").read_bytes().endswith(b"failed\n")


def test_invalid_utf8_fails_closed_after_storing_raw_bytes(tmp_path: Path) -> None:
    runner = RecordingRunner([completed(stdout=b"\xff")])
    raw = tmp_path / ".git" / "aios" / "verification"

    with pytest.raises(RuntimeVerificationError, match="strict UTF-8"):
        execute_verification(
            ("bad-output",),
            run_id="RUN-027-004",
            subject_sha="abc123",
            repository=tmp_path,
            raw_directory=raw,
            runner=runner,
            platform="posix",
        )

    assert (raw / "RUN-027-004-V001.raw").read_bytes() == (
        b"STDOUT\n\xff\nSTDERR\n"
    )


def test_invalid_utf8_command_fails_before_shell_invocation(tmp_path: Path) -> None:
    runner = RecordingRunner([completed()])

    with pytest.raises(RuntimeVerificationError, match="command must be strict UTF-8"):
        execute_verification(
            ("bad-\udcff",),
            run_id="RUN-027-006",
            subject_sha="abc123",
            repository=tmp_path,
            raw_directory=tmp_path / ".git" / "aios" / "verification",
            runner=runner,
            platform="posix",
        )

    assert runner.calls == []


def test_attaches_complete_evidence_set_without_changing_claim_semantics(
    tmp_path: Path,
) -> None:
    result = Result(
        head_sha="abc123",
        claims=(Claim("C1", ("AC1", "AC2"), "semantic claim", ()),),
        changed_files=("file.py",),
        unresolved=(),
    )
    runner = RecordingRunner([completed(), completed()])
    evidence = execute_verification(
        ("one", "two"),
        run_id="RUN-027-005",
        subject_sha="abc123",
        repository=tmp_path,
        raw_directory=tmp_path / ".git" / "aios" / "verification-test",
        runner=runner,
        platform="posix",
    )

    attached = attach_verification_evidence(result, evidence)

    assert attached.claims[0].id == "C1"
    assert attached.claims[0].satisfies == ("AC1", "AC2")
    assert attached.claims[0].claim == "semantic claim"
    assert attached.claims[0].evidence == (
        "RUN-027-005-V001",
        "RUN-027-005-V002",
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell integration test")
def test_windows_real_subprocess_captures_non_ascii_unicode_as_utf8(
    tmp_path: Path,
) -> None:
    command = "python -c \"print('Tést Unicode: 🚀 — こんにちは')\""
    raw = tmp_path / ".git" / "aios" / "verification"

    evidence = execute_verification(
        (command,),
        run_id="RUN-034-REG",
        subject_sha="abc123",
        repository=tmp_path,
        raw_directory=raw,
        platform="nt",
    )

    assert len(evidence) == 1
    assert evidence[0].result.exit_code == 0
    assert "Tést Unicode: 🚀 — こんにちは" in evidence[0].result.summary
    raw_content = (raw / "RUN-034-REG-V001.raw").read_bytes()
    assert "Tést Unicode: 🚀 — こんにちは".encode("utf-8") in raw_content


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell integration test")
def test_windows_real_subprocess_propagates_native_nonzero_exit_code(
    tmp_path: Path,
) -> None:
    command = "python -c \"import sys; sys.stderr.write('fatal boom\\n'); sys.exit(42)\""
    raw = tmp_path / ".git" / "aios" / "verification"

    with pytest.raises(RuntimeVerificationError) as caught:
        execute_verification(
            (command,),
            run_id="RUN-036-REG",
            subject_sha="abc123",
            repository=tmp_path,
            raw_directory=raw,
            platform="nt",
        )

    assert "exit code 42" in str(caught.value)
    assert len(caught.value.evidence) == 1
    assert caught.value.evidence[0].result.exit_code == 42
    assert caught.value.evidence[0].source.command == command
    assert "fatal boom" in caught.value.evidence[0].result.summary
    raw_content = (raw / "RUN-036-REG-V001.raw").read_bytes()
    assert b"fatal boom" in raw_content
