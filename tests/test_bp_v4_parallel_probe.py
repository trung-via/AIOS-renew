"""Focused tests for the bounded BP-V4 measurement primitive."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bp_v4_parallel_probe as probe


def _load_unit_plugin():
    # Runtime registers bp_v4_probe_plugin itself. Direct hook calls in these
    # tests must use independent globals, never the surrounding observer's.
    spec = importlib.util.spec_from_file_location(
        "_bp_v4_probe_plugin_unit", Path(__file__).with_name("bp_v4_probe_plugin.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plugin = _load_unit_plugin()


NODEIDS = ["tests/test_b.py::test_two", "tests\\test_a.py::test_one"]


def observation(
    *,
    workers: int = 0,
    nodeids: list[str] | None = None,
    exit_status: int = 0,
    shared_root: bool = False,
    failures: dict[str, object] | None = None,
) -> dict[str, object]:
    collection = NODEIDS if nodeids is None else nodeids
    identity = probe.collection_identity(collection)
    worker_collections = {
        f"gw{index}": dict(identity) for index in range(workers)
    }
    worker_data = {
        f"gw{index}": {
            "worker_id": f"gw{index}",
            "process_id": 100 + index,
            "temporary_root": "root/shared" if shared_root else f"root/tmp-{index}",
            "git_fixture_cache_root": (
                "root/git-shared" if shared_root else f"root/git-{index}"
            ),
            "collection": dict(identity),
        }
        for index in range(workers)
    }
    return {
        "schema": "AIOS_BP_V4_PYTEST_OBSERVATION",
        "version": 1,
        "exit_status": exit_status,
        "controller_collection": dict(identity),
        "worker_collections": worker_collections,
        "workers": worker_data,
        "failure_diagnostics": failures
        if failures is not None
        else {
            "reported_count": 0,
            "displayed_count": 0,
            "display_limit": probe.MAX_FAILURE_IDENTITIES,
            "displayed_identities": [],
            "truncated": False,
        },
    }


@pytest.mark.parametrize("values", [[], ["1"], ["auto"], ["2", "2"], ["3", "2"], ["4", "5"], ["2", "&&"]])
def test_workers_reject_defaults_duplicates_order_and_injection(values) -> None:
    with pytest.raises(probe.ProbeError):
        probe.parse_workers(values)


@pytest.mark.parametrize(
    "arguments",
    [[], ["--workers=2"], ["--workers", "2", "--"], ["--workers", "2", "--workers", "3"]],
)
def test_cli_rejects_noncanonical_construction_before_measurement(
    arguments, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def measure(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("measurement must not start")

    monkeypatch.setattr(probe, "measure", measure)
    with pytest.raises(SystemExit):
        probe.main(arguments)
    assert called is False


def test_collection_identity_is_normalized_and_deterministic() -> None:
    first = probe.collection_identity(NODEIDS)
    second = probe.collection_identity(list(reversed(NODEIDS)))

    assert first == second
    assert first["count"] == 2
    assert first["rule"] == "sorted-posix-nodeid-lf-sha256-v1"
    assert len(first["digest"]) == 64


def test_plugin_registration_preserves_collection_identity_without_unknown_hooks() -> None:
    from aios_renew.verification_contract import pytest_collection_identity

    manager = pytest.PytestPluginManager()
    manager.add_hookspecs(pytest.importorskip("xdist.newhooks"))
    manager.register(plugin, "aios-bp-v4-probe")
    manager.check_pending()

    expected = pytest_collection_identity(NODEIDS)
    assert plugin._collection_identity(NODEIDS) == expected
    assert plugin._collection_identity(list(reversed(NODEIDS))) == expected


def test_task320_serial_collection_opt_in_preserves_lossless_parameter_text(tmp_path, monkeypatch):
    from aios_renew.verification_contract import validate_exact_collection
    observer = _load_unit_plugin()
    output = tmp_path / "collection.json"
    monkeypatch.setenv("AIOS_BP_V4_PLUGIN_OUTPUT", str(output))
    monkeypatch.setenv("AIOS_V2_EXACT_COLLECTION", "1")
    monkeypatch.setenv("AIOS_V2_PROFILE", json.dumps({"profile": "pytest-observed-v2", "collect_only": True}))
    config = SimpleNamespace(rootpath=tmp_path, option=SimpleNamespace(collectonly=True))
    nodes = ["tests\\test_sample.py::test_value[space 'quote' [nested] \\path]",
             "tests/test_sample.py::test_other"]
    session = SimpleNamespace(config=config, items=[SimpleNamespace(nodeid=n) for n in nodes])
    observer.pytest_sessionstart(session)
    observer.pytest_collection_finish(session)
    observer.pytest_sessionfinish(session, 0)
    payload = json.loads(output.read_text(encoding="utf-8"))
    expected = tuple(sorted(["tests/test_sample.py::test_value[space 'quote' [nested] \\path]", nodes[1]]))
    assert validate_exact_collection(payload["exact_collection"]) == expected
    assert payload["controller_collection"] == payload["exact_collection"]["identity"]
    assert payload["failure_diagnostics"]["canonical"]["complete"] is True


def test_task320_parallel_worker_collection_stays_compact_with_opt_in(tmp_path, monkeypatch):
    observer = _load_unit_plugin()
    monkeypatch.setenv("AIOS_V2_EXACT_COLLECTION", "1")
    monkeypatch.setenv("AIOS_V2_PROFILE", json.dumps({"profile": "pytest-observed-v2", "collect_only": True}))
    config = SimpleNamespace(rootpath=tmp_path, option=SimpleNamespace(collectonly=True),
                             workerinput={"workerid": "gw0"}, workeroutput={})
    session = SimpleNamespace(config=config, items=[SimpleNamespace(nodeid=n) for n in NODEIDS])
    observer.pytest_sessionstart(session)
    observer.pytest_collection_finish(session)
    assert config.workeroutput["aios_bp_v4_collection"] == probe.collection_identity(NODEIDS)
    assert not hasattr(config, "_aios_v2_exact_collection")


def test_task320_collection_mode_mismatch_is_incomplete(tmp_path, monkeypatch):
    observer = _load_unit_plugin()
    output = tmp_path / "mismatch.json"
    monkeypatch.setenv("AIOS_BP_V4_PLUGIN_OUTPUT", str(output))
    monkeypatch.setenv("AIOS_V2_PROFILE", json.dumps({"profile": "pytest-observed-v2", "collect_only": True}))
    config = SimpleNamespace(rootpath=tmp_path, option=SimpleNamespace(collectonly=False))
    session = SimpleNamespace(config=config, items=[])
    observer.pytest_sessionstart(session)
    observer.pytest_collection_finish(session)
    observer.pytest_sessionfinish(session, 0)
    canonical = json.loads(output.read_text(encoding="utf-8"))["failure_diagnostics"]["canonical"]
    assert canonical["complete"] is False
    assert "actual collection mode disagrees with bound profile" in canonical["errors"]


def test_unit_plugin_fail_closed_paths_do_not_mutate_registered_observer(
    request: pytest.FixtureRequest,
) -> None:
    import bp_v4_probe_plugin as observer

    active = request.config.pluginmanager.get_plugin("bp_v4_probe_plugin")
    assert active is None or active is observer
    assert plugin is not observer

    def state(module):
        return deepcopy({
            name: value for name, value in vars(module).items()
            if name.startswith("_") and not name.startswith("__") and not callable(value)
        })

    before = state(observer)
    plugin.pytest_sessionstart(None)
    plugin.pytest_testnodedown(
        SimpleNamespace(
            gateway=SimpleNamespace(id="gw0"),
            workeroutput={
                "aios_bp_v4_failures": {
                    "reported_count": 0,
                    "displayed_identities": [],
                    "truncated": False,
                },
            },
        ),
        None,
    )
    canonical = plugin._canonical_summary()
    assert canonical["complete"] is False
    assert canonical["unstable"] is True
    assert canonical["errors"] == [
        "malformed pytest collection identity",
        "missing worker canonical population",
    ]
    assert state(observer) == before


def test_pytest_command_is_fixed_and_uses_only_requested_workers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        Path(kwargs["env"][probe.PLUGIN_OUTPUT_ENV]).write_text(
            json.dumps(observation(workers=3)), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(probe.subprocess, "run", run)
    status, _elapsed, _payload = probe.run_pytest(
        tmp_path,
        tmp_path,
        label="parallel-3",
        workers=3,
        collect_only=False,
    )

    assert status == 0
    assert captured["command"][-6:] == [
        "-n",
        "3",
        "--dist",
        "load",
        "--max-worker-restart",
        "0",
    ]
    assert "auto" not in captured["command"]
    assert captured["environment"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_missing_optional_toolchain_fails_without_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(probe.importlib.metadata, "version", missing)
    called = False

    def runner(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("measurement must not start")

    with pytest.raises(probe.ProbeError, match="optional bp-v4-measurement"):
        probe.measure(Path.cwd(), (2,), runner=runner)
    assert called is False


def test_one_collection_and_each_profile_execute_once_in_authored_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int, bool]] = []

    def runner(repository, temporary_root, *, label, workers, collect_only):
        del repository, temporary_root
        calls.append((label, workers, collect_only))
        return 0, {"serial": 9.0, "parallel-2": 12.0, "parallel-4": 7.0}.get(label, 0.1), observation(workers=0 if workers == 1 else workers)

    monkeypatch.setattr(
        probe, "subject_identity", lambda _repository: {"kind": "git-commit", "head_sha": "a" * 40}
    )
    envelope, success = probe.measure(
        Path.cwd(),
        (2, 4),
        runner=runner,
        toolchain_loader=lambda: {
            "python_implementation": "CPython",
            "python_version": "3.11.0",
            "pytest_version": "8.2.0",
            "pytest_xdist_version": "3.6.0",
        },
    )

    assert calls == [
        ("collection", 1, True),
        ("serial", 1, False),
        ("parallel-2", 2, False),
        ("parallel-4", 4, False),
    ]
    assert success is True
    assert envelope["format"] == "AIOS_BP_V4_MEASUREMENT"
    assert envelope["version"] == 1
    assert [item["workers"] for item in envelope["profiles"]] == [1, 2, 4]
    # A slower parallel profile is still a successful observation.
    assert envelope["profiles"][1]["elapsed_seconds"] > envelope["profiles"][0]["elapsed_seconds"]
    forbidden = {"selected", "recommended", "best", "winner", "score", "adaptive"}

    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    assert forbidden.isdisjoint(keys(envelope))


def test_test_failure_changes_outcome_but_timing_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def runner(repository, temporary_root, *, label, workers, collect_only):
        del repository, temporary_root, collect_only
        failed = label == "parallel-2"
        return (1 if failed else 0), 0.01, observation(
            workers=0 if workers == 1 else workers,
            exit_status=1 if failed else 0,
        )

    monkeypatch.setattr(probe, "subject_identity", lambda _repository: {"kind": "unavailable", "head_sha": None})
    _envelope, success = probe.measure(
        Path.cwd(),
        (2,),
        runner=runner,
        toolchain_loader=lambda: {},
    )
    assert success is False


def test_nonzero_profiles_add_serial_failure_identity_and_success_stays_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = plugin._failure_identity("tests\\test_bad.py::test_broken", "call")
    diagnostics = {
        "reported_count": 1,
        "displayed_count": 1,
        "display_limit": probe.MAX_FAILURE_IDENTITIES,
        "displayed_identities": [identity],
        "truncated": False,
    }

    def runner(repository, temporary_root, *, label, workers, collect_only):
        del repository, temporary_root, collect_only
        failed = label == "serial"
        return (1 if failed else 0), 0.01, observation(
            workers=0 if workers == 1 else workers,
            exit_status=1 if failed else 0,
            failures=diagnostics if failed else None,
        )

    monkeypatch.setattr(
        probe,
        "subject_identity",
        lambda _repository: {"kind": "unavailable", "head_sha": None},
    )
    envelope, success = probe.measure(
        Path.cwd(), (2,), runner=runner, toolchain_loader=lambda: {}
    )
    assert success is False
    assert envelope["profiles"][0]["failure_diagnostics"] == diagnostics
    displayed = envelope["profiles"][0]["failure_diagnostics"][
        "displayed_identities"
    ]
    assert displayed[0]["nodeid"] == "tests/test_bad.py::test_broken"
    assert "failure_diagnostics" not in envelope["profiles"][1]


def test_empty_diagnostics_do_not_fabricate_identity_for_non_runtest_exit() -> None:
    data = observation(exit_status=3)
    result = probe._profile_result(
        mode="serial",
        workers=1,
        elapsed=0.01,
        status=3,
        observation=data,
        conformance={"collection_matches_canonical": True},
    )
    assert result["failure_diagnostics"]["reported_count"] == 0
    assert result["failure_diagnostics"]["displayed_identities"] == []


def test_failure_identity_clips_with_stable_fingerprint() -> None:
    long_nodeid = "tests\\test_params.py::test_value[" + "x" * 400 + "]"
    first = plugin._failure_identity(long_nodeid, "setup")
    second = plugin._failure_identity(long_nodeid, "setup")
    assert first == second
    assert first["clipped"] is True
    assert len(first["nodeid"]) == plugin.MAX_NODEID_DISPLAY_CHARS
    assert first["nodeid"].endswith(f"...#{first['fingerprint']}")
    assert "\\" not in first["nodeid"]


def test_parallel_failure_merge_is_order_invariant_and_bounded() -> None:
    facts = [
        plugin._failure_identity(f"tests/test_{index:02d}.py::test_bad", "call")
        for index in range(plugin.MAX_FAILURE_IDENTITIES + 3)
    ]

    def merged(groups):
        plugin.pytest_sessionstart(None)
        for index, group in enumerate(groups):
            node = SimpleNamespace(
                gateway=SimpleNamespace(id=f"gw{index}"),
                workeroutput={
                    "aios_bp_v4_failures": {
                        "reported_count": len(group),
                        "displayed_identities": group,
                        "truncated": False,
                    }
                },
            )
            plugin.pytest_testnodedown(node, None)
        return plugin._summary(
            plugin._parallel_failure_count,
            plugin._parallel_failure_facts,
            plugin._parallel_failure_truncated,
        )

    forward = merged([facts[::2], facts[1::2]])
    reassigned = merged(
        [
            list(reversed(facts[::3])),
            list(reversed(facts[1::3])),
            list(reversed(facts[2::3])),
        ]
    )
    assert forward == reassigned
    assert forward["reported_count"] == len(facts)
    assert forward["displayed_count"] == plugin.MAX_FAILURE_IDENTITIES
    assert forward["truncated"] is True


@pytest.mark.parametrize("defect", ["collection", "shared-roots", "missing-worker"])
def test_parallel_conformance_fails_closed(defect: str) -> None:
    canonical = probe.collection_identity(NODEIDS)
    data = observation(workers=2, shared_root=defect == "shared-roots")
    if defect == "collection":
        data["worker_collections"]["gw1"] = ["tests/test_other.py::test_other"]
        data["workers"]["gw1"]["collection"] = ["tests/test_other.py::test_other"]
    elif defect == "missing-worker":
        del data["workers"]["gw1"]

    with pytest.raises(probe.ProbeError):
        probe._parallel_conformance(data, canonical, 2)


def test_profile_conformance_defect_does_not_skip_later_authorized_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def runner(repository, temporary_root, *, label, workers, collect_only):
        del repository, temporary_root, collect_only
        calls.append(label)
        return 0, 0.01, observation(
            workers=0 if workers == 1 else workers,
            shared_root=label == "parallel-2",
        )

    monkeypatch.setattr(
        probe,
        "subject_identity",
        lambda _repository: {"kind": "git-commit", "head_sha": "a" * 40},
    )
    with pytest.raises(probe.ProbeError, match="parallel-2"):
        probe.measure(
            Path.cwd(),
            (2, 3),
            runner=runner,
            toolchain_loader=lambda: {},
        )
    assert calls == ["collection", "serial", "parallel-2", "parallel-3"]


def test_incompatible_toolchain_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    versions = {"pytest": "9.0.0", "pytest-xdist": "3.6.0"}
    monkeypatch.setattr(probe.importlib.metadata, "version", versions.__getitem__)
    with pytest.raises(probe.ProbeError, match="incompatible pytest version"):
        probe.load_toolchain()
