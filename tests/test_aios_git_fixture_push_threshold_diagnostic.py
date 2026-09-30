"""Primitive regressions only: no live six-case threshold experiment.

Protocol fixtures below are validator inputs, never substituted Git results.
Transport tests use real Git in short disposable repositories; spies delegate
every subprocess unchanged. No current-main 326/327/328 outcome is recorded.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import aios_git_fixture_push_threshold_diagnostic as diagnostic
from tests import aios_git_fixture_push_threshold_probe as probe
from tests.git_fixture_support import materialize_git_baseline


def case(family, length, failed=False):
    value = {"family": family, "authored_lock_path_length": length,
             "observed_lock_path_length": length,
             "outcome": "failure" if failed else "success", "return_code": int(failed)}
    if failed:
        value.update(stderr_category="OTHER", stderr_signatures=["OTHER"])
    return value


def cases(vectors=((False, False, False), (False, False, False))):
    return [case(family, length, failed)
            for family, vector in zip(diagnostic.FAMILIES, vectors)
            for length, failed in zip(diagnostic.LENGTHS, vector)]


def envelope(values=None):
    values = cases() if values is None else values
    return {"format": diagnostic.FORMAT, "version": 1,
            "subject_unchanged": True, "worktree_clean": True,
            "cases": values, "relationships": diagnostic.relationships(values)}


def baseline(root):
    return materialize_git_baseline(
        root, files={"README.md": "# Fixture repository\n"},
        user_name="AIOS Operator Test", user_email="operator@example.invalid",
        commit_message="baseline", remote_head_main=True,
    )


@pytest.mark.parametrize("args", [["--help"], ["--"], [""], ["--length", "327"],
                                  ["--family", "integration"], ["some-target"], ["--retry"]])
def test_arguments_rejected_before_any_experiment(monkeypatch, capsys, args):
    def forbidden(*_):
        pytest.fail("experiment began")
    monkeypatch.setattr(diagnostic, "diagnose", forbidden)
    monkeypatch.setattr(diagnostic.subprocess, "run", forbidden)
    assert diagnostic.main(args) == 2
    output = capsys.readouterr()
    assert not output.out and "accepts no arguments" in output.err


def test_exact_fixed_six_case_binding(monkeypatch):
    expected = (("integration", 326), ("integration", 327), ("integration", 328),
                ("admission-failure", 326), ("admission-failure", 327),
                ("admission-failure", 328))
    assert diagnostic.CASES == expected
    calls, identities = [], []

    def identity(_):
        identities.append(True)
        return "a" * 40

    # Exercise orchestration with bounded protocol fixtures, not mocked Git.
    def observe(root, family, length):
        calls.append((root, family, length))
        return case(family, length, failed=length == 327)

    monkeypatch.setattr(diagnostic, "subject_identity", identity)
    monkeypatch.setattr(probe, "run_case", observe)
    result = diagnostic.diagnose(Path.cwd())
    assert tuple((f, n) for _, f, n in calls) == expected
    assert len({root for root, _, _ in calls}) == 1
    assert len(identities) == 8  # Before, before each case, finally after all six.
    assert result == envelope(cases(((False, True, False), (False, True, False))))
    assert "subject_sha" not in result


@pytest.mark.parametrize("family,length", diagnostic.CASES)
def test_exact_authored_geometry_without_git(tmp_path, family, length):
    sandbox, remote = probe.case_layout(tmp_path, family, length)
    ref = probe.canonical_ref(family)
    assert ref == f"refs/heads/aios/{family}/{hashlib.sha256(f'{family}-0'.encode()).hexdigest()}"
    assert len(ref.rsplit("/", 1)[1]) == 64
    assert len(str(remote / f"{ref}.lock")) == length
    assert remote == sandbox / "upstream.git"
    assert not sandbox.exists()  # Geometry regression creates no Git experiment.


def test_geometry_constructions_are_independent(tmp_path):
    layouts = [probe.case_layout(tmp_path, family, length) for family, length in diagnostic.CASES]
    assert len({sandbox for sandbox, _ in layouts}) == 6
    assert len({remote for _, remote in layouts}) == 6
    assert all(remote.parent == sandbox for sandbox, remote in layouts)


@pytest.mark.parametrize("family,authored,observed", [
    ("integration", 326, 325), ("admission-failure", 327, 328),
    ("integration", 328, 329), ("integration", 325, 325),
    ("integration", True, True), ("integration", 326, 326.0),
    ("other", 326, 326), (None, 326, 326), ("integration", "326", 326),
])
def test_geometry_mismatch_fails_closed(family, authored, observed):
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.validate_geometry(family, authored, observed)


def test_unconstructible_geometry_does_not_approximate(tmp_path):
    with pytest.raises(diagnostic.DiagnosticError):
        probe.case_layout(tmp_path / ("x" * 230), "integration", 326)
    for family, length in (("other", 326), ("integration", 329)):
        with pytest.raises(diagnostic.DiagnosticError):
            probe.case_layout(tmp_path, family, length)


@pytest.mark.parametrize("vector", list(itertools.product((False, True), repeat=3)))
def test_every_monotonic_and_non_monotonic_vector(vector):
    result = diagnostic.relationships(cases((vector, vector)))
    non_monotonic = any(vector[i] and not vector[j]
                        for i in range(3) for j in range(i + 1, 3))
    for family, facts in zip(diagnostic.FAMILIES, result["families"]):
        assert facts == {
            "family": family,
            "outcome_vector": ["failure" if failed else "success" for failed in vector],
            "first_failed_length": next((n for n, failed in zip(diagnostic.LENGTHS, vector) if failed), None),
            "relationship": "NON_MONOTONIC" if non_monotonic else "MONOTONIC",
        }
    assert result["same_length_agreement"] == [
        {"lock_path_length": n, "agrees": True} for n in diagnostic.LENGTHS]


@pytest.mark.parametrize("left,right", list(itertools.product(
    itertools.product((False, True), repeat=3), repeat=2)))
def test_cross_family_comparison_is_same_length_only(left, right):
    comparisons = diagnostic.relationships(cases((left, right)))["same_length_agreement"]
    assert comparisons == [{"lock_path_length": n, "agrees": a == b}
                           for n, a, b in zip(diagnostic.LENGTHS, left, right)]


@pytest.mark.parametrize("category,patterns", probe.STDERR_PATTERNS)
def test_every_category_pattern(category, patterns):
    for pattern in patterns:
        observed, _ = probe.stderr_facts(f"fatal: {pattern}", ())
        assert observed == category


@pytest.mark.parametrize("signature,patterns", probe.SIGNATURE_PATTERNS)
def test_every_signature_pattern(signature, patterns):
    for pattern in patterns:
        _, signatures = probe.stderr_facts(f"remote: {pattern}", ())
        assert signatures == [signature]


def test_overlapping_signatures_sorted_and_category_precedence():
    message = ("remote unpack failed\ncannot lock ref 'private'\nunable to create 'private'\n"
               "Filename too long\nfailed to push some refs to 'private'\nAccess is denied")
    category, signatures = probe.stderr_facts(message, ())
    assert category == "LOCK_OR_REF_UPDATE"
    assert signatures == sorted(diagnostic.SIGNATURES - {"OTHER"})
    assert probe.stderr_facts("remote unpack failed\nfailed to push some refs", ()) == (
        "REPOSITORY_STATE", ["FAILED_TO_PUSH_REFS", "REMOTE_UNPACK_FAILED"])


@pytest.mark.parametrize("message", [None, b"secret", "", "unclassified private message"])
def test_other_category_and_signature(message):
    assert probe.stderr_facts(message, ()) == ("OTHER", ["OTHER"])


@pytest.mark.parametrize("operand", [
    "C:\\private\\remote unpack failed\\repo", "/private/cannot lock ref/repo",
    "refs/heads/aios/failed to push some refs/" + "a" * 64,
    "https://private/unable to create", "relative name access denied",
    "C:\\private\\" + "x" * 9000 + " filename too long",
])
def test_operands_removed_before_bounding_and_matching(operand):
    for variant in (operand, operand.replace("\\", "/"), operand.replace("/", "\\"), operand.upper()):
        assert probe.stderr_facts(f"fatal: '{variant}'", (operand,)) == ("OTHER", ["OTHER"])
        assert probe.stderr_facts(f"fatal: {variant}", (operand,)) == ("OTHER", ["OTHER"])


@pytest.mark.parametrize("message", [
    "fatal: 'private remote unpack failed path'", 'fatal: "private cannot lock ref path"',
    "fatal: C:\\private\\remote_unpack_failed", "fatal: https://host/access_denied",
    "fatal: /private/unable_to_create", "fatal: refs/heads/private/filename_too_long",
    "fatal: " + "a" * 40,
])
def test_unknown_operands_do_not_supply_signatures(message):
    assert probe.stderr_facts(message, ()) == ("OTHER", ["OTHER"])


def test_stderr_is_bounded_and_never_published():
    assert probe.stderr_facts("x" * probe.MAX_STDERR + "cannot lock ref", ()) == ("OTHER", ["OTHER"])
    category, signatures = probe.stderr_facts("remote unpack failed 'private-secret'", ())
    value = case("integration", 326, True)
    value.update(stderr_category=category, stderr_signatures=signatures)
    serialized = json.dumps(diagnostic.validate_case(value, ("integration", 326)))
    assert "private-secret" not in serialized
    assert "remote unpack failed" not in serialized


@pytest.mark.parametrize("field,value", [
    ("path", "private"), ("ref", "private"), ("url", "private"), ("stdout", "private"),
    ("stderr", "private"), ("object_id", "a" * 40), ("environment", {"secret": "private"}),
    ("root_cause", "private"), ("recommended_fix", "private"),
])
def test_unknown_case_fields_rejected(field, value):
    fact = case("integration", 326, True)
    fact[field] = value
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.validate_case(fact, ("integration", 326))


@pytest.mark.parametrize("field,value", [
    ("return_code", True), ("return_code", 1.0), ("return_code", 0),
    ("return_code", 2**32), ("return_code", -(2**31) - 1),
    ("stderr_category", "private"), ("stderr_category", []),
    ("stderr_signatures", []), ("stderr_signatures", ["private"]),
    ("stderr_signatures", ["OTHER", "OTHER"]),
    ("stderr_signatures", ["OTHER", "CANNOT_LOCK_REF"]),
    ("stderr_signatures", ["REMOTE_UNPACK_FAILED", "CANNOT_LOCK_REF"]),
    ("stderr_signatures", "OTHER"), ("stderr_signatures", [None]),
    ("observed_lock_path_length", 327), ("authored_lock_path_length", 328),
    ("family", "admission-failure"), ("outcome", "private"),
])
def test_malformed_failure_facts_rejected(field, value):
    fact = case("integration", 326, True)
    fact[field] = value
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.validate_case(fact, ("integration", 326))


def test_success_cannot_carry_failure_fields_or_code():
    for edits in ({"return_code": 1}, {"stderr_category": "OTHER"},
                  {"stderr_signatures": ["OTHER"]}):
        with pytest.raises(diagnostic.DiagnosticError):
            diagnostic.validate_case({**case("integration", 326), **edits}, ("integration", 326))


@pytest.mark.parametrize("code", [-(2**31), -1, 1, 2**32 - 1])
def test_bounded_integer_failure_codes(code):
    value = case("integration", 326, True)
    value["return_code"] = code
    assert diagnostic.validate_case(value, ("integration", 326)) == value


def test_missing_duplicate_reordered_extra_and_malformed_cases_rejected():
    values = cases()
    invalid = [None, {}, values[:-1], values + [values[0]],
               [values[0]] * 6, list(reversed(values)), values[:1] + [None] + values[2:]]
    missing = deepcopy(values)
    del missing[0]["observed_lock_path_length"]
    invalid.append(missing)
    for facts in invalid:
        with pytest.raises(diagnostic.DiagnosticError):
            diagnostic.validate_cases(facts)


@pytest.mark.parametrize("field,value", [
    ("format", "other"), ("version", True), ("version", 2),
    ("worktree_clean", False), ("subject_unchanged", False),
    ("root_cause", "private"), ("selected_correction", "private"),
    ("safe_worker", 4), ("winner", "private"), ("score", 1),
    ("path_threshold_causal", True), ("subject_sha", "a" * 40),
])
def test_unsafe_envelopes_rejected(field, value):
    facts = envelope()
    facts[field] = value
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.validate_envelope(facts)


def test_relationship_contradiction_and_privacy_rejected():
    for edit in ("first_failed_length", "outcome_vector", "relationship", "secret"):
        facts = envelope()
        facts["relationships"]["families"][0][edit] = "private"
        with pytest.raises(diagnostic.DiagnosticError):
            diagnostic.validate_envelope(facts)
    facts = envelope()
    facts["relationships"]["same_length_agreement"][0]["agrees"] = 1
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.validate_envelope(facts)
    facts = envelope()
    facts["relationships"]["families"][0]["outcome_vector"] = ("success",) * 3
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.validate_envelope(facts)


def test_successful_main_including_observed_push_failure(monkeypatch, capsys):
    value = envelope(cases(((False, False, True), (False, True, True))))
    monkeypatch.setattr(diagnostic, "diagnose", lambda _: value)
    assert diagnostic.main([]) == 0
    output = capsys.readouterr()
    assert not output.err and json.loads(output.out) == value


@pytest.mark.parametrize("error", [diagnostic.DiagnosticError, OSError, RuntimeError,
                                  TypeError, subprocess.SubprocessError, KeyboardInterrupt])
def test_main_failure_never_discloses_exception(monkeypatch, capsys, error):
    def fail(_):
        raise error("private path/ref/url/credential")
    monkeypatch.setattr(diagnostic, "diagnose", fail)
    assert diagnostic.main([]) == 2
    output = capsys.readouterr()
    assert not output.out and "private" not in output.err


def test_main_revalidates_before_durable_output(monkeypatch, capsys):
    value = envelope()
    value["cases"][0]["stderr"] = "private-secret"
    monkeypatch.setattr(diagnostic, "diagnose", lambda _: value)
    assert diagnostic.main([]) == 2
    assert "private-secret" not in str(capsys.readouterr())


@pytest.mark.parametrize("when", [1, 3, 7])
def test_subject_mutation_fails_closed(monkeypatch, when):
    calls, invoked = [], []

    def identity(_):
        index = len(calls)
        calls.append(index)
        return ("a" if index < when else "b") * 40

    def observe(root, family, length):
        invoked.append((family, length))
        return case(family, length)

    monkeypatch.setattr(diagnostic, "subject_identity", identity)
    monkeypatch.setattr(probe, "run_case", observe)
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.diagnose(Path.cwd())
    assert len(invoked) <= 6


def test_instrumentation_failure_stops_without_retry_and_checks_subject(monkeypatch):
    calls, identities = [], []

    def fail(root, family, length):
        calls.append((family, length))
        raise RuntimeError("private")

    monkeypatch.setattr(probe, "run_case", fail)
    monkeypatch.setattr(diagnostic, "subject_identity",
                        lambda _: identities.append(True) or "a" * 40)
    with pytest.raises(RuntimeError):
        diagnostic.diagnose(Path.cwd())
    assert calls == [diagnostic.CASES[0]]
    assert len(identities) == 3


def test_actual_subject_dirty_and_root_checks(tmp_path):
    repo, _, head = baseline(tmp_path / "subject")
    assert diagnostic.subject_identity(repo.resolve()) == head
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.subject_identity((repo / ".git").resolve())
    (repo / "private-untracked").write_text("private", encoding="ascii")
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.subject_identity(repo.resolve())


def test_git_environment_excludes_inherited_redirection(monkeypatch):
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                "GIT_CONFIG_COUNT", "GIT_CONFIG_SYSTEM", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        monkeypatch.setenv(key, "private")
    env = diagnostic.git_environment()
    assert {key for key in env if key.upper().startswith("GIT_")} == {
        "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_TERMINAL_PROMPT"}
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull


def spy_real_git(monkeypatch):
    original = subprocess.run
    calls = []

    def observe(command, **kwargs):
        calls.append((command, kwargs))
        return original(command, **kwargs)

    monkeypatch.setattr(diagnostic.subprocess, "run", observe)
    return calls


def test_real_checked_push_and_exact_success_resolution(tmp_path, monkeypatch):
    repo, remote, head = baseline(tmp_path / "short-success")
    ref = probe.canonical_ref("integration")
    # This is deliberately a short transport regression, not a threshold case.
    assert len(str(remote / f"{ref}.lock")) < 326
    probe.check_longpaths(repo)
    probe.check_longpaths(remote)
    diagnostic.git(repo, "update-ref", ref, head)
    calls = spy_real_git(monkeypatch)
    assert probe.checked_push(repo, remote, ref, head) == (0, "OTHER", [])
    assert [command[3:] for command, _ in calls] == [
        ["push", "--quiet", "origin", f"{ref}:{ref}"],
        ["show-ref", "--verify", ref], ["ls-remote", "--refs", "origin", ref],
    ]
    assert all(kwargs["check"] is True and kwargs["capture_output"] is True
               for _, kwargs in calls)
    assert diagnostic.git(remote, "show-ref", "--verify", ref).stdout.strip() == f"{head} {ref}"


def test_real_failed_checked_push_is_observation_without_retry(tmp_path, monkeypatch):
    repo, remote, head = baseline(tmp_path / "short-rejection")
    ref = probe.canonical_ref("admission-failure")
    assert len(str(remote / f"{ref}.lock")) < 326
    # An actual unrelated upstream commit makes a real non-fast-forward refusal.
    for key, value in (("user.name", "Fixture"), ("user.email", "fixture@example.invalid")):
        diagnostic.git(remote, "config", "--local", key, value)
    tree = diagnostic.git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    other = diagnostic.git(remote, "commit-tree", tree, "-m", "unrelated root").stdout.strip()
    assert other != head
    diagnostic.git(remote, "update-ref", ref, other)
    diagnostic.git(repo, "update-ref", ref, head)
    calls = spy_real_git(monkeypatch)
    code, category, signatures = probe.checked_push(repo, remote, ref, head)
    assert code != 0 and category in diagnostic.CATEGORIES
    assert signatures == ["FAILED_TO_PUSH_REFS"]
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == ["git", "-C", str(repo), "push", "--quiet", "origin", f"{ref}:{ref}"]
    assert kwargs["check"] is True


@pytest.mark.parametrize("surface", ["show-ref", "ls-remote"])
def test_actual_resolution_contradiction_fails_closed(tmp_path, surface):
    repo, remote, head = baseline(tmp_path / surface)
    ref = probe.canonical_ref("integration")
    diagnostic.git(repo, "update-ref", ref, head)
    probe.checked_push(repo, remote, ref, head)
    target = remote if surface == "show-ref" else repo
    args = ("show-ref", "--verify", ref) if surface == "show-ref" else ("ls-remote", "--refs", "origin", ref)
    with pytest.raises(diagnostic.DiagnosticError):
        probe.check_resolution(target, *args, expected=f"{'0' * 40} {ref}")


@pytest.mark.parametrize("bare", [False, True])
def test_repository_local_longpaths_required(tmp_path, bare):
    repo, remote, _ = baseline(tmp_path / "longpaths")
    target = remote if bare else repo
    diagnostic.git(target, "config", "--local", "core.longpaths", "false")
    with pytest.raises(diagnostic.DiagnosticError):
        probe.check_longpaths(target)


def test_no_reuse_of_existing_case_sandbox(tmp_path):
    sandbox, _ = probe.case_layout(tmp_path, "integration", 326)
    sandbox.parent.mkdir(parents=True)
    with pytest.raises(diagnostic.DiagnosticError):
        probe.run_case(tmp_path, "integration", 326)


def test_invalid_case_geometry_precedes_materialization(tmp_path):
    with pytest.raises(diagnostic.DiagnosticError):
        probe.run_case(tmp_path, "integration", 325)
    assert not list(tmp_path.iterdir())
