"""Focused subprocess regressions; local fixtures are never canonical AIOS proof.

The outer harness exercises the existing source-bound observer launcher. Its one
nested Runtime fixture retains real admission, lease and checkpoint ownership;
only that fixture's provider session and Human HTTP are synthetic.
"""

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from aios_renew import ka01_admission as ka01
from aios_renew import verification as verifier
from aios_renew.verification_contract import VerificationContractError, validate_observation
from tests.git_fixture_support import materialize_git_baseline


ROOT = Path(__file__).resolve().parents[1]
SERIAL = "python -m pytest -q tests/test_subject.py -n 0 --dist no --max-worker-restart 0"
SUBJECT_TEST = """
from pathlib import Path
import bp_v4_probe_plugin as observer
from tests.subject_support import MARKER

def test_subject():
    assert MARKER == 'exact subject'
    assert Path(observer.__file__).resolve() == Path.cwd() / 'tests/bp_v4_probe_plugin.py'
"""


def _case(root, *, files=None, command=SERIAL):
    population = {name: (ROOT / name).read_bytes() for name in ka01.KERNEL_PROOF_PATHS}
    population.update({"src/aios_renew/__init__.py": b"",
        "tests/subject_support.py": "MARKER = 'exact subject'\n",
        "tests/test_subject.py": SUBJECT_TEST})
    population.update(files or {})
    repo, _, sha = materialize_git_baseline(root, files=population,
        user_name="Observer fixture", user_email="observer@example.invalid", commit_message="bound subject")
    tree = verifier._git(repo, "rev-parse", sha + "^{tree}")
    target = SimpleNamespace(run_id="RUN-101-001", base_sha=sha, tree_sha=tree)
    return SimpleNamespace(repo=repo, sha=sha, tree=tree, command=command,
        request=SimpleNamespace(scheduler=SimpleNamespace(target=target, subject_sha=sha)),
        task=SimpleNamespace(verification=SimpleNamespace(required=(command,)),
            scope=SimpleNamespace(modify=("OUTPUT.txt",))))


def _source(control, subject):
    # Snapshot fixture only, not production admission or a first-adoption route.
    files = {name: (control.repo / name).read_bytes() for name in ka01.KERNEL_PROOF_PATHS}
    facts = dict(source_main_sha=control.sha, source_tree_sha=control.tree,
        proof_source={name: ka01._digest(raw) for name, raw in files.items()})
    return ka01._KernelUpgradeSource(control.repo, subject.repo, ka01._bytes(facts))


def _execute(case, raw_directory, *, runner=subprocess.run, source=None, proof=None, **changes):
    profile = verifier._v2_profile(case.repo if source is None else source.control,
        case.sha if source is None else json.loads(source.content)["source_main_sha"], case.command, os.environ)
    arguments = {}
    if source is not None:
        profile.update(isolation_policy="original-runtime-observer-candidate-imports-v1",
            original_proof_source_sha256=ka01._digest(source.content))
        arguments.update(kernel_upgrade_source=source,
            original_proof=proof or verifier._kernel_proof_binding(source, "a" * 64))
    arguments.update(changes)
    return verifier._execute_checkpoint_verification(case.repo,
        request=case.request, task=case.task,
        manifests={"0": (None, dict(command_index=0, profile=profile, toolchain=verifier._v2_toolchain()), None)},
        raw_directory=raw_directory, start_order=0, runner=runner, timeout_seconds=90,
        capture_bytes_remaining=32 * 1024 * 1024, **arguments)


def _assert_capture(error, raw_directory, *, exit_code):
    assert isinstance(error, verifier.RuntimeVerificationError)
    assert len(str(error)) < 256 and str(raw_directory) not in str(error)
    assert len(error.evidence) == 1
    item = error.evidence[0]
    assert item.evidence_id == "RUN-101-001-V001" and item.result.exit_code == exit_code
    raw = (raw_directory / (item.evidence_id + ".raw")).read_bytes()
    assert raw.startswith(b"STDOUT\n") and b"\nSTDERR\n" in raw
    assert len(list(raw_directory.glob("*.raw"))) == 1
    if item.verification is not None:
        assert item.verification["raw_digest"] == hashlib.sha256(raw).hexdigest()
        assert item.verification["binding"]["subject_sha"] == item.subject_sha
    return raw


OUTER_TEST = r'''
import os
from pathlib import Path
import bp_v4_probe_plugin as observer
from tests.subject_support import MARKER
from tests.test_ka01_v13_activation import ProductionFixture
from tests.test_ka01_v13_intra_run_native import fixture_session, checkpoint

def test_subject(tmp_path, monkeypatch):
    assert MARKER == 'exact subject'
    assert Path(observer.__file__).resolve().parent.parent.name == 'original-proof'
    assert observer._pytest_collection_identity.__module__.startswith('_aios_kernel_original.')
    outer_environment = {key: os.environ[key] for key in ('PYTHONPATH', 'PYTEST_PLUGINS')}
    proof = ("from pathlib import Path\nimport bp_v4_probe_plugin as observer\n"
        "def test_output():\n"
        "    assert Path(observer.__file__).resolve() == Path.cwd() / 'tests/bp_v4_probe_plugin.py'\n"
        "    assert Path('OUTPUT.txt').read_text() == 'complete\\n'\n")
    fixture = ProductionFixture(tmp_path / 'nested', monkeypatch, interactive=True, proof_source=proof)
    fixture.authorize()
    sessions = fixture_session(fixture, monkeypatch)
    verify = fixture.verify
    def own_subject(argv, **kwargs):
        environment, subject = kwargs['env'], Path(kwargs['cwd'])
        assert environment['PYTEST_PLUGINS'] == 'xdist.plugin,bp_v4_probe_plugin'
        assert environment['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'
        assert environment['PYTHONPATH'].split(os.pathsep) == [
            str(subject / 'src'), str(subject / 'tests'), str(subject)]
        assert environment['AIOS_VERIFY_SUBJECT_SHA'] == fixture.owner._ka01_budget.session.last_turn.output['package']['result']['head_sha']
        return verify(argv, **kwargs)
    summary = fixture.execute(verification_runner=own_subject)
    budget = fixture.owner._ka01_budget
    assert len(sessions) == 1 and sessions[0].calls == 1 and sessions[0].closed
    assert len(fixture.verification_calls) == budget.checkpoints_spent == 1
    assert budget.corrections_spent == 0 and budget.pending is None and budget.stopped
    assert budget.verification_reserved_ns == budget.verification_settled_ns
    assert not fixture.owner._interactive_feedback
    record = checkpoint(fixture, 1).data
    assert not record['failed_identities']
    assert record['binding']['candidate_sha'] == summary.head_sha
    assert len(list(fixture.state.results.glob('*.json'))) == 1
    assert not list(fixture.state.failures.glob('*.json'))
    assert {key: os.environ[key] for key in outer_environment} == outer_environment
'''


def test_original_isolated_twelve_worker_observer_runs_one_nested_runtime_checkpoint(tmp_path):
    control = _case(tmp_path / "control")
    # Only these support tests are imported; no outer diagnostic suite runs.
    files = {p.relative_to(ROOT).as_posix(): p.read_bytes()
             for p in (ROOT / "src/aios_renew").rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    files.update({name: (ROOT / name).read_bytes() for name in (
        "tests/test_ka01_v13_activation.py", "tests/test_ka01_v13_intra_run_native.py",
        "tests/operator_test_support.py", "docs/AIOS-KERNEL-KA01-PROSPECTIVE-AMENDMENT.md",
        ".ai/executor-profiles.yaml")})
    files["tests/test_subject.py"] = OUTER_TEST
    case = _case(tmp_path / "candidate", files=files, command="python -m pytest -q tests/test_subject.py")
    source = _source(control, case)
    calls = []

    def run(argv, **kwargs):
        calls.append(tuple(argv))
        assert argv[1:4] == ("-I", "-B", "-c")
        assert kwargs["env"]["PYTEST_PLUGINS"] == "xdist.plugin,_aios_kernel_observer"
        assert kwargs["env"]["PYTHONPATH"].split(os.pathsep)[-1] == str(kwargs["cwd"])
        return subprocess.run(argv, **kwargs)

    evidence = _execute(case, tmp_path / "raw", runner=run, source=source)
    assert len(calls) == len(evidence) == 1
    item = evidence[0]
    observed = validate_observation(item.verification["candidate"])
    assert item.result.exit_code == 0 and observed["profile"]["workers"] == 12
    assert len(observed["execution"]["workers"]) == 12
    assert observed["collection"]["nodeids"] == ["tests/test_subject.py::test_subject"]
    assert item.verification["binding"]["original_proof"] == verifier._kernel_proof_binding(source, "a" * 64)
    assert not verifier._git(case.repo, "status", "--porcelain")


def test_old_nested_observer_collision_retains_first_error_and_raw_provenance(tmp_path):
    case = _case(tmp_path / "nested")
    control = _case(tmp_path / "outer")
    source = _source(control, case)
    calls = []
    with verifier._kernel_observer_subject(case.repo, source) as (original, _):
        def old_leak(argv, **kwargs):
            calls.append(tuple(argv))
            environment = kwargs["env"]
            environment["PYTEST_PLUGINS"] = "xdist.plugin,_aios_kernel_observer,bp_v4_probe_plugin"
            environment["PYTHONPATH"] = str(original) + os.pathsep + environment["PYTHONPATH"]
            return subprocess.run(argv, **kwargs)

        raw = tmp_path / "raw"
        with pytest.raises(verifier.RuntimeVerificationError, match="CHECKPOINT_NATIVE_OBSERVATION_UNAVAILABLE") as stopped:
            _execute(case, raw, runner=old_leak)
        capture = _assert_capture(stopped.value, raw, exit_code=4)
        assert b"ERROR: conflicting pytest observer source" in capture
        assert not (raw / "observation-001.json").exists() and len(calls) == 1


def test_ordinary_checkpoint_discards_inherited_plugin_and_import_roots(tmp_path, monkeypatch):
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    (wrong / "bp_v4_probe_plugin.py").write_text("raise AssertionError('inherited observer loaded')\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(wrong))
    monkeypatch.setenv("PYTEST_PLUGINS", "_aios_kernel_observer,untrusted_plugin")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    case = _case(tmp_path / "candidate")
    item, = _execute(case, tmp_path / "raw")
    assert item.result.exit_code == 0
    assert validate_observation(item.verification["candidate"])["errors"] == []
    assert os.environ["PYTHONPATH"] == str(wrong)


@pytest.mark.parametrize("defect,reason,exit_code", [
    ("wrong-plugin", "UNAVAILABLE", 4),
    ("missing", "UNAVAILABLE", 0),
    ("malformed", "INVALID", 0),
    ("wrong-subject", "INVALID", 0),
    ("wrong-profile", "INVALID", 0),
    ("wrong-toolchain", "INVALID", 0),
    ("incomplete", "INVALID", 0),
    ("missing-collection", "INVALID", 0),
    ("unexpected-exit", "UNEXPECTED_PYTEST_EXIT", 5),
])
def test_nested_observation_negatives_stop_once_with_bound_capture(tmp_path, defect, reason, exit_code):
    case = _case(tmp_path / "candidate")
    wrong = tmp_path / "foreign"
    wrong.mkdir()
    (wrong / "bp_v4_probe_plugin.py").write_text("# Wrong-source plugin, never proof.\n", encoding="utf-8")
    calls = []

    def corrupt(argv, **kwargs):
        calls.append(tuple(argv))
        if defect == "wrong-plugin":
            kwargs["env"]["PYTHONPATH"] = str(wrong) + os.pathsep + kwargs["env"]["PYTHONPATH"]
        completed = subprocess.run(argv, **kwargs)
        output = Path(kwargs["env"]["AIOS_BP_V4_PLUGIN_OUTPUT"])
        if defect == "missing":
            output.unlink()
        elif defect == "malformed":
            output.write_text("{invalid", encoding="utf-8")
        elif defect in {"wrong-subject", "wrong-profile", "wrong-toolchain", "incomplete", "missing-collection"}:
            value = json.loads(output.read_bytes())
            if defect == "wrong-subject":
                value["subject_sha"] = "0" * 40
            elif defect == "wrong-profile":
                value["profile"]["workers"] = 12
            elif defect == "wrong-toolchain":
                value["toolchain"]["pytest_version"] = "0.0.0"
            elif defect == "incomplete":
                value["failure_diagnostics"]["canonical"]["reports"].pop()
            else:
                value.pop("exact_collection")
            output.write_text(json.dumps(value), encoding="utf-8")
        elif defect == "unexpected-exit":
            completed = subprocess.CompletedProcess(argv, 5, stdout=completed.stdout, stderr=completed.stderr)
        return completed

    raw = tmp_path / "raw"
    with pytest.raises(verifier.RuntimeVerificationError, match="CHECKPOINT_.*" + reason) as stopped:
        _execute(case, raw, runner=corrupt)
    capture = _assert_capture(stopped.value, raw, exit_code=exit_code)
    assert len(calls) == 1 and stopped.value.evidence[0].verification["candidate"]["complete"] is False
    if defect == "wrong-plugin":
        assert b"conflicting pytest observer source" in capture


def test_stale_observation_stops_before_any_execution(tmp_path):
    case = _case(tmp_path / "candidate")
    raw = tmp_path / "raw"
    raw.mkdir()
    output = raw / "observation-001.json"
    output.write_text("stale", encoding="utf-8")
    with pytest.raises(verifier.RuntimeVerificationError, match="OBSERVATION_ALREADY_EXISTS"):
        _execute(case, raw, runner=lambda *a, **k: pytest.fail("stale proof launched pytest"))
    assert output.read_text(encoding="utf-8") == "stale" and not list(raw.glob("*.raw"))


def test_original_helpers_and_tests_namespace_cannot_be_replaced_by_candidate(tmp_path):
    control = _case(tmp_path / "control")
    test = """
from pathlib import Path
from tests.subject_support import MARKER
import aios_renew.verification_contract as candidate
import bp_v4_probe_plugin as observer
def test_subject():
    assert MARKER == 'exact subject' and candidate.CANDIDATE
    assert Path(candidate.__file__).resolve().is_relative_to(Path.cwd())
    assert observer._pytest_collection_identity.__module__.startswith('_aios_kernel_original.')
"""
    poison = "raise AssertionError('candidate proof substituted')\n"
    case = _case(tmp_path / "candidate", files={"tests/test_subject.py": test,
        "src/aios_renew/verification_contract.py": "CANDIDATE = True\n",
        "tests/conftest.py": poison, "tests/bp_v4_probe_plugin.py": poison,
        "tests/git_fixture_support.py": poison, "_aios_kernel_observer.py": poison})
    item, = _execute(case, tmp_path / "raw", source=_source(control, case))
    assert item.result.exit_code == 0
    assert validate_observation(item.verification["candidate"])["errors"] == []
    assert item.verification["binding"]["profile"]["observer_blob"] == verifier._git(
        control.repo, "rev-parse", control.sha + ":tests/bp_v4_probe_plugin.py")


@pytest.mark.parametrize("defect", ["plugin", "launcher", "missing"])
def test_original_observer_substitution_stops_with_original_cause(tmp_path, defect):
    control = _case(tmp_path / "control")
    case = _case(tmp_path / "candidate", files={"tests/test_subject.py": "def test_subject():\n    pass\n"})
    source = _source(control, case)

    def substitute(argv, **kwargs):
        completed = subprocess.run(argv, **kwargs)
        root = Path(kwargs["cwd"]).parent / "original-proof"
        target = root / ("_aios_kernel_observer.py" if defect == "launcher" else "tests/bp_v4_probe_plugin.py")
        if defect == "missing":
            target.unlink()
        else:
            target.write_bytes(target.read_bytes() + b"\n# substituted\n")
        return completed

    raw = tmp_path / "raw"
    with pytest.raises(verifier.RuntimeVerificationError, match="ORIGINAL_OBSERVER_AUTHENTICATION_FAILED") as stopped:
        _execute(case, raw, runner=substitute, source=source)
    _assert_capture(stopped.value, raw, exit_code=0)
    assert isinstance(stopped.value.__cause__, VerificationContractError)
    assert str(stopped.value.__cause__) == "KA01_V13_KERNEL_UPGRADE_OBSERVER_SUBSTITUTED"


@pytest.mark.parametrize("defect", ["source", "proof"])
def test_wrong_original_source_binding_stops_before_pytest(tmp_path, defect):
    control = _case(tmp_path / "control")
    case = _case(tmp_path / "candidate")
    source = _source(control, case)
    proof = verifier._kernel_proof_binding(source, "a" * 64)
    if defect == "source":
        facts = json.loads(source.content)
        facts["proof_source"]["tests/bp_v4_probe_plugin.py"] = "0" * 64
        source = replace(source, content=ka01._bytes(facts))
    else:
        proof["source_main_sha"] = "0" * 40
    with pytest.raises(VerificationContractError, match="ORIGINAL_OBSERVER_CHANGED|PROOF_SOURCE_MISMATCH"):
        _execute(case, tmp_path / "raw", source=source, proof=proof,
            runner=lambda *a, **k: pytest.fail("wrong original source launched pytest"))
