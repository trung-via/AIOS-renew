"""Focused regression tests for deterministic verification-contract policy."""

import pytest
import copy
import hashlib

from aios_renew.verification_contract import (
    VerificationContractError,
    normalize_verification,
    parse_bp_v4_probe_workers,
    parse_rebaseline_workers,
    parse_pytest_coverage,
    validate_v1_verification,
    MINIMUM_SUFFICIENT_V2, MAX_CANONICAL_FAILURES, MAX_CANONICAL_NODEID_CHARS,
    attribute_failures, attributed_task_passes, derive_minimum_verification,
    failed_identities, validate_observation, verification_digest,
)


V2_BASE = "a" * 40
V2_CANDIDATE = "b" * 40
V2_COMMAND = "python -m pytest -q"
V2_TOOLCHAIN = {
    "python_implementation": "CPython", "python_version": "3.14.7",
    "python_executable": "C:/Python/python.exe", "platform_system": "Windows",
    "platform_machine": "AMD64", "pytest_version": "8.4.2", "pytest_xdist_version": "3.8.0",
}


def v2_observation(sha=V2_CANDIDATE, *, outcome="FAIL", detail="AssertionError: expected 1", count=1,
                   command=V2_COMMAND, phase="call"):
    profile = {"profile": "test-profile", "workers": 12}
    reports = []
    for index in range(count):
        report = {"nodeid": f"tests/test_sample.py::test_{index:03d}", "phase": phase, "outcome": outcome}
        if outcome == "FAIL":
            report.update(detail=detail, fingerprint=verification_digest(detail), profile=profile,
                          toolchain=V2_TOOLCHAIN)
        reports.append(report)
    return dict(subject_sha=sha, command=command, profile=profile, toolchain=dict(V2_TOOLCHAIN),
                reports=reports, failure_count=count if outcome == "FAIL" else 0,
                exit_code=1 if outcome == "FAIL" else 0, complete=True, unstable=False)


def v2_binding(sha=V2_CANDIDATE, *, base=None):
    value = v2_observation(sha)
    return dict(subject_sha=sha, base_sha=V2_BASE, tree_sha="c" * 40,
                command=V2_COMMAND, profile=value["profile"], toolchain=value["toolchain"],
                envelope_digest=verification_digest([V2_COMMAND]), changed_files_digest=verification_digest([]),
                failure_set_digest=verification_digest(base if base is not None else []))


def v2_record(*, base=None, candidate=None):
    candidate = candidate or v2_observation(outcome="PASS")
    value = dict(policy=MINIMUM_SUFFICIENT_V2, binding=v2_binding(candidate["subject_sha"], base=base),
                 candidate=candidate, candidate_digest=verification_digest(candidate), evidence_id="RUN-old-V001")
    if base is not None:
        value.update(base=base, base_digest=verification_digest(base))
    return value


def v2_plan(*, records=(), bindings=None, failures=(), changed=(), scope=(), operation="PRIMARY"):
    return derive_minimum_verification((V2_COMMAND,), base_sha=V2_BASE, candidate_sha=V2_CANDIDATE,
        changed_files=changed, modification_scope=scope, operation=operation,
        bindings=bindings or {V2_COMMAND: v2_binding()}, failed_observations=failures,
        still_valid_evidence=records)


def test_v2_complete_population_is_independent_of_display_bound():
    observation = v2_observation(count=51)
    assert validate_observation(observation) is observation
    assert len(failed_identities(observation)) == 51
    observation["failure_count"] = 20
    with pytest.raises(VerificationContractError, match="population"):
        validate_observation(observation)


def test_v2_declared_canonical_bounds_fail_closed(monkeypatch):
    import aios_renew.verification_contract as contract
    monkeypatch.setattr(contract, "MAX_CANONICAL_FAILURES", 2)
    with pytest.raises(VerificationContractError, match="population"):
        validate_observation(v2_observation(count=3))
    monkeypatch.setattr(contract, "MAX_CANONICAL_BYTES", 32)
    with pytest.raises(VerificationContractError, match="byte bound"):
        validate_observation(v2_observation())


@pytest.mark.parametrize("defect", ["missing", "duplicate", "nodeid_bound", "detail_bound", "fingerprint", "unstable", "incomplete", "toolchain"])
def test_v2_canonical_counterexamples_fail_closed(defect):
    value = v2_observation()
    if defect == "missing":
        value["reports"] = []
    elif defect == "duplicate":
        value["reports"] *= 2
    elif defect == "nodeid_bound":
        value["reports"][0]["nodeid"] = "x" * (MAX_CANONICAL_NODEID_CHARS + 1)
    elif defect == "detail_bound":
        value["reports"][0]["detail"] = "x" * 16385
    elif defect == "fingerprint":
        value["reports"][0]["fingerprint"] = "0" * 64
    elif defect == "toolchain":
        value["toolchain"] = {"pytest_version": "8.4.2"}
    elif defect == "incomplete":
        value["complete"] = False
    else:
        value[defect] = defect == "unstable"
    with pytest.raises(VerificationContractError):
        validate_observation(value)


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_v2_exact_phase_pass_fail_proves_regression(phase):
    result = attribute_failures(v2_observation(V2_BASE, outcome="PASS", phase=phase),
                               v2_observation(phase=phase), base_sha=V2_BASE, candidate_sha=V2_CANDIDATE)
    assert result[0]["classification"] == "CANDIDATE_REGRESSION"


@pytest.mark.parametrize("defect", [None, "absent", "subject", "profile", "toolchain", "command", "detail", "phase", "unstable", "skip", "base_exit", "candidate_exit"])
def test_v2_attribution_requires_every_exact_comparison_binding(defect):
    base = v2_observation(V2_BASE)
    candidate = v2_observation()
    if defect == "absent":
        base = None
    elif defect == "subject":
        base["subject_sha"] = "d" * 40
    elif defect in {"profile", "toolchain"}:
        base[defect] = {**base[defect], "changed": "true"}
        base["reports"][0][defect] = base[defect]
    elif defect == "command":
        base["command"] = "pytest -q"
    elif defect == "detail":
        base = v2_observation(V2_BASE, detail="AssertionError: different")
    elif defect == "phase":
        base = v2_observation(V2_BASE, phase="setup")
    elif defect == "unstable":
        base["unstable"] = True
    elif defect == "skip":
        base = v2_observation(V2_BASE, outcome="SKIP")
    elif defect == "base_exit":
        base["exit_code"] = 3
    elif defect == "candidate_exit":
        candidate["exit_code"] = 2
    result = attribute_failures(base, candidate, base_sha=V2_BASE, candidate_sha=V2_CANDIDATE)
    assert result[0]["classification"] == ("PRE_EXISTING_BASELINE" if defect is None else "UNRESOLVED")


def test_v2_raw_nonzero_remains_nonzero_with_separate_baseline_outcome():
    record = v2_record(base=v2_observation(V2_BASE), candidate=v2_observation())
    assert attributed_task_passes(record, subject_sha=V2_CANDIDATE, command=V2_COMMAND, exit_code=1)
    assert record["candidate"]["exit_code"] == 1
    for raw_exit in (0, 2, 3, 4, 5):
        assert not attributed_task_passes(record, subject_sha=V2_CANDIDATE, command=V2_COMMAND, exit_code=raw_exit)
    record["base"]["reports"][0]["detail"] = "tampered"
    assert not attributed_task_passes(record, subject_sha=V2_CANDIDATE, command=V2_COMMAND, exit_code=1)


@pytest.mark.parametrize("binding", ["command", "profile", "toolchain", "tree_sha", "base_sha", "failure_set_digest", "envelope_digest", "changed_files_digest"])
def test_v2_reuse_invalidates_every_relevant_binding(binding):
    record = v2_record()
    assert v2_plan(records=(record,)).commands == ()
    target = copy.deepcopy(record["binding"])
    target[binding] = {"changed": True} if isinstance(target[binding], dict) else "d" * len(target[binding])
    result = v2_plan(records=(record,), bindings={V2_COMMAND: target})
    assert result.commands == (V2_COMMAND,)
    assert result.reused == ()
    assert result.fallback == (V2_COMMAND,)


def test_v2_cross_candidate_reuse_requires_equal_complete_tree_and_preserves_origin():
    record = v2_record(candidate=v2_observation("d" * 40, outcome="PASS"))
    plan = v2_plan(records=(record,))
    assert plan.commands == () and plan.reused == (record,)
    reused = {**record, "reuse": {"binding": v2_binding(), "source_evidence_id": record["evidence_id"]}}
    assert attributed_task_passes(reused, subject_sha=V2_CANDIDATE, command=V2_COMMAND, exit_code=0)
    assert reused["candidate"]["subject_sha"] == "d" * 40
    reused["reuse"]["binding"]["tree_sha"] = "e" * 40
    assert not attributed_task_passes(reused, subject_sha=V2_CANDIDATE, command=V2_COMMAND, exit_code=0)


def test_v2_correction_probe_keeps_scope_and_full_guard_and_is_deterministic():
    failures = v2_observation(V2_BASE, count=51)
    scope = ("tests/test_sample.py",)
    one = v2_plan(failures=(failures,), scope=scope, operation="REPAIR", changed=scope)
    two = v2_plan(failures=(failures,), scope=scope, operation="REPAIR", changed=scope)
    assert one == two
    assert one.commands == (V2_COMMAND,)
    assert len(one.affected_first) == 1 and "test_050" in one.affected_first[0]
    assert scope == ("tests/test_sample.py",)
    unrelated = v2_plan(failures=(failures,), scope=("src/sample.py",), operation="REPAIR", changed=("src/sample.py",))
    assert unrelated.affected_first == () and unrelated.commands == (V2_COMMAND,)
    unauthorized = v2_plan(failures=(failures,), scope=scope, operation="REPAIR", changed=("src/outside.py",))
    assert unauthorized.affected_first == () and unauthorized.commands == (V2_COMMAND,)


def test_v2_correction_delta_is_separate_from_original_task_attribution_base():
    failed_head = "d" * 40
    scope = ("tests/test_sample.py",)
    plan = derive_minimum_verification((V2_COMMAND,), base_sha=V2_BASE, candidate_sha=V2_CANDIDATE,
        changed_files=("src/original-task.py", *scope), modification_scope=scope, operation="REPAIR",
        bindings={V2_COMMAND: v2_binding()}, failed_observations=(v2_observation(failed_head),),
        correction_base_sha=failed_head, correction_changed_files=scope)
    assert plan.affected_first and plan.commands == (V2_COMMAND,)
    # A failure already present on the failed correction subject is still a
    # candidate regression if the original authored base passed that phase.
    record = v2_record(base=v2_observation(V2_BASE, outcome="PASS"), candidate=v2_observation())
    assert not attributed_task_passes(record, subject_sha=V2_CANDIDATE, command=V2_COMMAND, exit_code=1)


def test_v2_authored_focused_requirement_prevents_duplicate_probe_execution():
    focused = "python -m pytest -q tests/test_sample.py"
    binding = {**v2_binding(), "command": focused}
    plan = derive_minimum_verification((focused, V2_COMMAND), base_sha=V2_BASE, candidate_sha=V2_CANDIDATE,
        changed_files=("tests/test_sample.py",), modification_scope=("tests/test_sample.py",), operation="REPAIR",
        bindings={focused: binding, V2_COMMAND: v2_binding()}, failed_observations=(v2_observation(V2_BASE),))
    assert plan.affected_first == (focused,)
    assert plan.commands == (V2_COMMAND,)


def test_v2_unknown_or_filtered_coverage_falls_back_without_dependency_inference():
    commands = ("opaque-check", "python -m pytest -q -k sample")
    result = derive_minimum_verification(commands, base_sha=V2_BASE, candidate_sha=V2_CANDIDATE,
        changed_files=("src/sample.py",), modification_scope=("src/sample.py",), operation="REPAIR",
        bindings={}, failed_observations=(v2_observation(V2_BASE),))
    assert result.commands == commands and result.affected_first == () and result.reused == ()


def test_selected_wrapper_grammar_and_subsumption() -> None:
    selected = "python scripts/aios_parallel_full_suite.py"
    ordinary = "python -m pytest -q"
    probe = "python scripts/bp_v4_parallel_probe.py --workers 4"
    coverage = parse_pytest_coverage(selected)
    assert coverage is not None and coverage.is_full_suite and coverage.selected_parallel
    assert normalize_verification((ordinary, selected)) == (selected,)
    assert normalize_verification((selected, ordinary)) == (selected,)
    assert normalize_verification((probe, selected)) == (probe, selected)
    with pytest.raises(VerificationContractError, match="full_suite_reason"):
        validate_v1_verification((selected,), full_suite_reason=None, path="verification.required")
    for malformed in (
        selected + " --workers 4", selected + " && echo done",
        "py scripts/aios_parallel_full_suite.py",
        "python .\\scripts\\aios_parallel_full_suite.py",
        "python scripts/AIOS_PARALLEL_FULL_SUITE.py",
    ):
        with pytest.raises(VerificationContractError, match="malformed selected"):
            validate_v1_verification((malformed,), full_suite_reason=None, path="verification.required")


def test_supported_grammar_keeps_launcher_families_separate() -> None:
    direct = parse_pytest_coverage("pytest -q tests/test_task.py -k contract")
    module = parse_pytest_coverage(
        "python -m pytest --quiet tests/test_task.py -k contract"
    )

    assert direct is not None and direct.launcher == "pytest"
    assert module is not None and module.launcher == "python -m pytest"
    validate_v1_verification(
        (
            "pytest -q tests/test_task.py -k contract",
            "python -m pytest --quiet tests/test_task.py -k contract",
        ),
        full_suite_reason=None,
        path="verification.required",
    )


@pytest.mark.parametrize(
    "commands",
    [
        ("pytest tests/test_task.py", "pytest tests/test_task.py"),
        ("pytest -q tests/test_task.py", "pytest tests/test_task.py --quiet"),
        ("pytest tests", "pytest tests/test_task.py"),
        ("pytest tests/test_task.py", "pytest tests/test_task.py -k valid"),
    ],
)
def test_v1_rejects_duplicate_equivalent_and_subsumed_commands(commands) -> None:
    with pytest.raises(VerificationContractError):
        validate_v1_verification(
            commands,
            full_suite_reason=None,
            path="verification.required",
        )


def test_opaque_commands_have_no_inferred_relation() -> None:
    commands = (
        "pytest tests/test_task.py --maxfail=1",
        "pytest tests/test_task.py --maxfail=2",
        "pytest tests/test_task.py && echo done",
    )

    assert all(parse_pytest_coverage(command) is None for command in commands)
    validate_v1_verification(
        commands, full_suite_reason=None, path="verification.required"
    )


def test_full_suite_reason_is_required_exactly_for_recognized_full_suite() -> None:
    with pytest.raises(VerificationContractError, match="is required"):
        validate_v1_verification(
            ("pytest -q",), full_suite_reason=None, path="verification.required"
        )
    with pytest.raises(VerificationContractError, match="allowed only"):
        validate_v1_verification(
            ("pytest tests/test_task.py",),
            full_suite_reason="Not actually a full suite.",
            path="verification.required",
        )
    with pytest.raises(VerificationContractError, match="at most 512"):
        validate_v1_verification(
            ("pytest",),
            full_suite_reason="x" * 513,
            path="verification.required",
        )


def test_normalization_keeps_earliest_equivalent_and_broader_command() -> None:
    commands = (
        "pytest -q tests/test_task.py",
        "opaque --one",
        "pytest tests/test_task.py",
        "pytest tests",
        "opaque --one",
        "python -m pytest tests/test_task.py",
    )

    assert normalize_verification(commands) == (
        "opaque --one",
        "pytest tests",
        "python -m pytest tests/test_task.py",
    )


@pytest.mark.parametrize(
    ("command", "workers"),
    [
        ("python scripts/bp_v4_parallel_probe.py --workers 2", (2,)),
        ("python scripts/bp_v4_parallel_probe.py --workers 2 3", (2, 3)),
        ("python scripts/bp_v4_parallel_probe.py --workers 2 3 4", (2, 3, 4)),
        ("python scripts/bp_v4_parallel_probe.py --workers 3 4", (3, 4)),
    ],
)
def test_bp_v4_probe_exact_grammar_is_recognized(command, workers) -> None:
    assert parse_bp_v4_probe_workers(command) == workers
    coverage = parse_pytest_coverage(command)
    assert coverage is not None
    assert coverage.launcher == "python -m pytest"
    assert coverage.is_full_suite
    assert coverage.measurement
    validate_v1_verification(
        (command,),
        full_suite_reason="One bounded same-subject BP-V4 measurement experiment.",
        path="verification.required",
    )


@pytest.mark.parametrize(
    "command",
    [
        "python scripts/bp_v4_parallel_probe.py",
        "python scripts/bp_v4_parallel_probe.py --workers",
        "python scripts/bp_v4_parallel_probe.py --workers auto",
        "python scripts/bp_v4_parallel_probe.py --workers 1",
        "python scripts/bp_v4_parallel_probe.py --workers 5",
        "python scripts/bp_v4_parallel_probe.py --workers 2 2",
        "python scripts/bp_v4_parallel_probe.py --workers 3 2",
        "python scripts/bp_v4_parallel_probe.py --workers 2 --extra",
        "python scripts/bp_v4_parallel_probe.py --workers 2 && echo injected",
        "python scripts/bp_v4_parallel_probe.py --workers '2",
        'python scripts/bp_v4_parallel_probe.py --workers "2',
        "py scripts/bp_v4_parallel_probe.py --workers 2",
        "python .\\scripts\\bp_v4_parallel_probe.py --workers 2",
        "python scripts/BP_V4_PARALLEL_PROBE.py --workers 2",
    ],
)
def test_malformed_probe_family_fails_closed(command) -> None:
    assert parse_bp_v4_probe_workers(command) is None
    with pytest.raises(VerificationContractError, match="malformed BP-V4 probe"):
        validate_v1_verification(
            (command,), full_suite_reason=None, path="verification.required"
        )


def test_probe_subsumes_module_full_suite_but_not_direct_launcher() -> None:
    probe_command = "python scripts/bp_v4_parallel_probe.py --workers 2 4"
    module_full_suite = "python -m pytest -q"
    direct_full_suite = "pytest -q"

    with pytest.raises(VerificationContractError, match="subsumed"):
        validate_v1_verification(
            (module_full_suite, probe_command),
            full_suite_reason="The bounded probe contains the module full-suite proof.",
            path="verification.required",
        )
    assert normalize_verification((module_full_suite, probe_command)) == (
        probe_command,
    )
    assert normalize_verification((probe_command, module_full_suite)) == (
        probe_command,
    )
    assert normalize_verification((direct_full_suite, probe_command)) == (
        direct_full_suite,
        probe_command,
    )


def test_multiple_probe_commands_cannot_authorize_duplicate_measurement() -> None:
    commands = (
        "python scripts/bp_v4_parallel_probe.py --workers 2",
        "python scripts/bp_v4_parallel_probe.py --workers 2 3",
    )
    with pytest.raises(VerificationContractError, match="coverage-equivalent"):
        validate_v1_verification(
            commands,
            full_suite_reason="Only one measurement is permitted.",
            path="verification.required",
        )
    assert normalize_verification(commands) == (commands[0],)


@pytest.mark.parametrize(
    "command",
    [
        "python scripts/unrelated_probe.py --workers auto && echo opaque",
        "echo scripts/bp_v4_parallel_probe.py",
        'python -c "print(\'scripts/bp_v4_parallel_probe.py\')"',
        'python -c "value = \'bp_v4_parallel_probe.py\'"',
    ],
)
def test_unrelated_opaque_behavior_remains_compatible(command) -> None:
    assert parse_pytest_coverage(command) is None
    validate_v1_verification(
        (command,), full_suite_reason=None, path="verification.required"
    )
    assert normalize_verification((command,)) == (command,)


def test_rebaseline_exact_family_has_full_suite_measurement_coverage() -> None:
    rebaseline = "python scripts/bp_v4_parallel_rebaseline.py --workers 4 8 12 16"
    ordinary = "python -m pytest -q"
    old_probe = "python scripts/bp_v4_parallel_probe.py --workers 2 3 4"
    selected = "python scripts/aios_parallel_full_suite.py"
    assert parse_rebaseline_workers(rebaseline) == (4, 8, 12, 16)
    coverage = parse_pytest_coverage(rebaseline)
    assert coverage is not None and coverage.measurement and coverage.is_full_suite
    with pytest.raises(VerificationContractError, match="full_suite_reason"):
        validate_v1_verification((rebaseline,), full_suite_reason=None, path="verification.required")
    validate_v1_verification(
        (rebaseline,), full_suite_reason="Compare the four explicit profiles.",
        path="verification.required",
    )
    with pytest.raises(VerificationContractError, match="subsumed"):
        validate_v1_verification(
            (ordinary, rebaseline), full_suite_reason="Same full suite.",
            path="verification.required",
        )
    assert normalize_verification((ordinary, rebaseline)) == (rebaseline,)
    assert normalize_verification((rebaseline, selected)) == (rebaseline, selected)
    # The distinct historical and new measurement commands cannot replace each other.
    assert normalize_verification((old_probe, rebaseline)) == (old_probe, rebaseline)
    assert parse_bp_v4_probe_workers(old_probe) == (2, 3, 4)


@pytest.mark.parametrize("command", [
    "python scripts/bp_v4_parallel_rebaseline.py",
    "python scripts/bp_v4_parallel_rebaseline.py --workers",
    "python scripts/bp_v4_parallel_rebaseline.py --workers 4 8 12",
    "python scripts/bp_v4_parallel_rebaseline.py --workers 8 4 12 16",
    "python scripts/bp_v4_parallel_rebaseline.py --workers 4 8 8 16",
    "python scripts/bp_v4_parallel_rebaseline.py --workers 4 8 12 16 20",
    "python scripts/bp_v4_parallel_rebaseline.py --workers auto",
    "python scripts/bp_v4_parallel_rebaseline.py --workers 4 8 12 16 && echo injected",
    "python scripts/bp_v4_parallel_rebaseline.py --workers '4",
    "py scripts/bp_v4_parallel_rebaseline.py --workers 4 8 12 16",
    "python .\\scripts\\bp_v4_parallel_rebaseline.py --workers 4 8 12 16",
    "python scripts/BP_V4_PARALLEL_REBASELINE.py --workers 4 8 12 16",
])
def test_malformed_rebaseline_family_fails_closed(command) -> None:
    assert parse_rebaseline_workers(command) is None
    assert parse_pytest_coverage(command) is None
    with pytest.raises(VerificationContractError, match="malformed BP-V4 rebaseline"):
        validate_v1_verification((command,), full_suite_reason=None, path="verification.required")
    with pytest.raises(VerificationContractError, match="malformed BP-V4 rebaseline"):
        normalize_verification((command,))


@pytest.mark.parametrize("defect", ["rule", "count_bool", "count_negative", "count_bound",
                                  "digest", "empty_digest", "nonempty_digest", "missing", "extra"])
def test_compact_collection_identity_is_strict_and_bounded(defect):
    from aios_renew.verification_contract import (
        pytest_collection_identity, MAX_CANONICAL_REPORTS,
    )
    identity = pytest_collection_identity(["tests/test_sample.py::test_example"])
    if defect == "rule":
        identity["rule"] = "unowned-rule"
    elif defect == "count_bool":
        identity["count"] = True
    elif defect == "count_negative":
        identity["count"] = -1
    elif defect == "count_bound":
        identity["count"] = MAX_CANONICAL_REPORTS + 1
    elif defect == "digest":
        identity["digest"] = "G" * 64
    elif defect == "empty_digest":
        identity["count"] = 0
    elif defect == "nonempty_digest":
        identity["digest"] = hashlib.sha256(b"").hexdigest()
    elif defect == "missing":
        del identity["digest"]
    else:
        identity["nodeids"] = ["unbounded-list"]
    with pytest.raises(VerificationContractError, match="collection identity"):
        pytest_collection_identity(identity)


def test_compact_collection_identity_keeps_historical_rule_and_full_identity():
    from aios_renew.verification_contract import pytest_collection_identity
    nodeids = ["tests\\test_sample.py::test_b[" + "x" * 500 + "]",
               "tests/test_sample.py::test_a"]
    expected = hashlib.sha256((nodeids[1] + "\n" + nodeids[0].replace("\\", "/") + "\n").encode("utf-8")).hexdigest()
    identity = pytest_collection_identity(nodeids)
    assert identity == {"rule": "sorted-posix-nodeid-lf-sha256-v1", "count": 2, "digest": expected}
    assert pytest_collection_identity(identity) == identity
    assert pytest_collection_identity(list(reversed(nodeids))) == identity
    assert pytest_collection_identity([])["digest"] == hashlib.sha256(b"").hexdigest()
    with pytest.raises(VerificationContractError, match="duplicate"):
        pytest_collection_identity(["tests\\test_sample.py::test_a", "tests/test_sample.py::test_a"])
