"""Focused contract checks for the stable-failure detail observation."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import aios_stable_failure_detail_diagnostic as diagnostic
from tests import aios_stable_failure_detail_probe_plugin as plugin


class AuthoringIngressError(ValueError):
    pass


def _raise_ingress():
    raise AuthoringIngressError("private C:/Users/name/repository")


def test_ingress_locus_is_allowlisted_and_message_is_hashed(monkeypatch):
    try:
        _raise_ingress()
    except AuthoringIngressError as exc:
        info = pytest.ExceptionInfo.from_current()
        monkeypatch.setattr(plugin, "INGRESS", Path(__file__).relative_to(plugin.ROOT).as_posix())
        fact = plugin.ingress_detail(exc, info)
        assert fact["source_locus"]["function"] == "_raise_ingress"
        assert fact["source_locus"]["line"] > 0
        assert "private" not in json.dumps(fact)
        assert fact["message_fingerprint"] == "sha256:" + hashlib.sha256(str(exc).encode()).hexdigest()
        monkeypatch.setattr(plugin, "INGRESS", "src/aios_renew/authoring_ingress.py")
        assert plugin.ingress_detail(exc, info)["source_locus"] == "UNKNOWN_LOCUS"


@pytest.mark.parametrize(("command", "family"), [
    (("git", "-C", "SECRET", "rev-parse", "PRIVATE"), "REV_PARSE"),
    (("git", "clone", "PRIVATE"), "CLONE"),
    (("git", "checkout", "PRIVATE"), "CHECKOUT"),
    (("git", "status"), "STATUS"),
    (("git", "remote", "get-url", "PRIVATE"), "REMOTE_GET_URL"),
    (("git", "fetch", "PRIVATE"), "FETCH"),
    (("git", "ls-remote", "PRIVATE"), "LS_REMOTE"),
    (("git", "show", "PRIVATE"), "SHOW"),
    (("git", "ls-tree", "PRIVATE"), "LS_TREE"),
    (("git", "merge-base", "PRIVATE"), "MERGE_BASE"),
    (("git", "update-ref", "PRIVATE"), "UPDATE_REF"),
    (("git", "cat-file", "PRIVATE"), "CAT_FILE"),
    (("git", "push", "PRIVATE"), "PUSH"),
    (("git", "unknown", "PRIVATE"), "OTHER"),
])
def test_static_command_grammar_redacts_operands(command, family):
    assert plugin.command_family(command) == family
    assert "PRIVATE" not in plugin.command_family(command)


@pytest.mark.parametrize(("stderr", "category"), [
    (b"fatal: not a git repository: PRIVATE", "NOT_A_REPOSITORY"),
    (b"fatal: unknown revision PRIVATE", "UNKNOWN_REVISION_OR_OBJECT"),
    (b"fatal: couldn't find remote ref PRIVATE", "MISSING_REF"),
    (b"fatal: cannot lock ref PRIVATE", "LOCK_OR_REF_UPDATE"),
    (b"error: you have unmerged files PRIVATE", "REPOSITORY_STATE"),
    (b"fatal: No such file or directory PRIVATE", "PATH_OR_FILENAME"),
    (b"fatal: Permission denied PRIVATE", "ACCESS_OR_PERMISSION"),
    (b"fatal: Could not resolve host PRIVATE", "TRANSPORT_OR_REMOTE"),
    (b"unmapped PRIVATE", "OTHER"),
])
def test_stderr_category_grammar_redacts_detail(stderr, category):
    assert plugin.stderr_category(stderr) == category
    assert "PRIVATE" not in plugin.stderr_category(stderr)


def test_native_delegation_once_preserves_arguments_and_result():
    calls = []
    marker = object()
    env = {"SECRET": "opaque"}
    command = ("git", "-C", "PRIVATE", "rev-parse", "PRIVATE")
    result = subprocess.CompletedProcess(command, 9, b"PRIVATE", b"fatal: bad object PRIVATE")

    def original(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    observed = []
    assert plugin.native_git_call(original, observed, inspect.currentframe(), command, cwd=marker, env=env) is result
    assert calls == [((command,), {"cwd": marker, "env": env})]
    assert observed == []


def test_native_git_observation_is_bounded_and_delegates_once(monkeypatch):
    monkeypatch.setattr(plugin, "OPERATOR", Path(__file__).relative_to(plugin.ROOT).as_posix())
    command = ("git", "-C", "PRIVATE_PATH", "rev-parse", "PRIVATE_REF")
    calls = []
    observed = []

    def original(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(command, 128, b"PRIVATE_STDOUT", b"fatal: bad object PRIVATE_REF")

    def _git():
        return plugin.native_git_call(original, observed, inspect.currentframe(), command, env={"PRIVATE": "VALUE"})

    assert _git().returncode == 128
    assert len(calls) == len(observed) == 1
    assert calls[0][0] == (command,)
    assert calls[0][1] == {"env": {"PRIVATE": "VALUE"}}
    assert observed[0][1] == {"command_family": "REV_PARSE", "return_code": 128,
                              "stderr_category": "UNKNOWN_REVISION_OR_OBJECT"}
    assert "PRIVATE" not in json.dumps(observed[0][1])


def _hash(path):
    return "sha256:" + hashlib.sha256(os.path.normcase(str(path.resolve())).encode()).hexdigest()


def _observation(root, label, pid, detail=None):
    targets = list(diagnostic.TARGETS if label == "collection" else (diagnostic.TARGETS[int(label[-1])],))
    fail = detail is not None
    return {"schema": plugin.SCHEMA, "version": 1, "exit_status": int(fail),
            "collection": targets, "executed": [] if label == "collection" else targets,
            "failures": [{"nodeid": targets[0], "phase": "call", "detail": detail}] if fail else [],
            "process": {"pid": pid, "basetemp": _hash(root / label / "pytest"),
                        "cache": _hash(root / label / "fixture-cache"), "cache_in_profile": True}}


INGRESS_DETAIL = {"exception_type": "AuthoringIngressError", "message_fingerprint": "sha256:" + "a" * 64,
                  "cause_chain": [], "source_locus": {"path": plugin.INGRESS, "function": "ingest_carrier", "line": 42},
                  "detail_status": "RESOLVED"}
GIT_DETAIL = {"exception_type": "OperatorError", "guard_category": "GIT_COMMAND_FAILED",
              "detail_status": "RESOLVED", "git": {"command_family": "REV_PARSE", "return_code": 128,
                                                 "stderr_category": "UNKNOWN_REVISION_OR_OBJECT"}}


def _run(tmp_path, details, mutate=None, identity=None):
    calls = []

    def runner(repo, root, *, label, selectors):
        calls.append((label, selectors))
        detail = None if label == "collection" else details[int(label[-1])]
        value = _observation(root, label, 100 + len(calls), detail)
        if mutate:
            mutate(value, label)
        return value["exit_status"], value

    stable = {"kind": "git-commit", "head_sha": "a" * 40, "worktree_clean": True}
    result = diagnostic.diagnose(tmp_path, runner=runner, identity=identity or (lambda repo: stable))
    return result, calls


def test_exact_binding_and_resolved_classification(tmp_path):
    result, calls = _run(tmp_path, [INGRESS_DETAIL, GIT_DETAIL, GIT_DETAIL])
    assert calls == [("collection", diagnostic.TARGETS)] + [(f"target-{i}", (node,)) for i, node in enumerate(diagnostic.TARGETS)]
    assert result["classification"] == "DETAILS_RESOLVED"
    assert len(result["targets"]) == 3


def test_ambiguous_and_unknown_locus_are_partial(tmp_path):
    ingress = dict(INGRESS_DETAIL, source_locus="UNKNOWN_LOCUS", detail_status="UNKNOWN_LOCUS")
    git = dict(GIT_DETAIL, detail_status="AMBIGUOUS", git=None)
    result, _ = _run(tmp_path, [ingress, git, GIT_DETAIL])
    assert result["classification"] == "PARTIAL_DETAILS"


def test_git_failure_association_requires_one_frame():
    class Info:
        def __init__(self, exc):
            self.value = exc
    try:
        raise RuntimeError("PRIVATE")
    except RuntimeError as exc:
        frame = exc.__traceback__.tb_frame
        detail = {"command_family": "STATUS", "return_code": 1, "stderr_category": "OTHER"}
        assert plugin.git_detail(Info(exc), []) == {"detail_status": "AMBIGUOUS", "git": None}
        assert plugin.git_detail(Info(exc), [(frame, detail)]) == {"detail_status": "RESOLVED", "git": detail}
        assert plugin.git_detail(Info(exc), [(frame, detail), (frame, detail)])["detail_status"] == "AMBIGUOUS"


@pytest.mark.parametrize(("details", "classification"), [
    ([None, None, None], "PASS_DRIFT"),
    ([INGRESS_DETAIL, None, GIT_DETAIL], "MIXED_PASS_FAIL"),
])
def test_pass_drift_and_mixed_outcomes(tmp_path, details, classification):
    result, _ = _run(tmp_path, details)
    assert result["classification"] == classification


@pytest.mark.parametrize("mutation", [
    lambda value: value["collection"].append(value["collection"][0]),
    lambda value: value["process"].update(cache_in_profile=False),
    lambda value: value["process"].update(basetemp="sha256:" + "0" * 64),
    lambda value: value["process"].update(pid=101),
])
def test_identity_or_root_failure_is_closed(tmp_path, mutation):
    with pytest.raises(diagnostic.DiagnosticError):
        _run(tmp_path, [None, None, None], mutate=lambda value, label: mutation(value) if label == "target-0" else None)


def test_subject_mutation_stops_after_collection(tmp_path):
    calls = 0

    def changed(repo):
        nonlocal calls
        calls += 1
        return {"kind": "git-commit", "head_sha": ("b" if calls >= 4 else "a") * 40, "worktree_clean": True}

    with pytest.raises(diagnostic.DiagnosticError):
        _run(tmp_path, [None, None, None], identity=changed)


def test_reused_fixture_cache_root_is_rejected(tmp_path):
    cache = None

    def mutate(value, label):
        nonlocal cache
        if label == "collection":
            cache = value["process"]["cache"]
        elif label == "target-0":
            value["process"]["cache"] = cache

    with pytest.raises(diagnostic.DiagnosticError):
        _run(tmp_path, [None, None, None], mutate=mutate)


def test_rejects_duplicate_json_keys_and_private_detail(tmp_path):
    path = tmp_path / "observation.json"
    path.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic._read(path)
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic._detail(dict(GIT_DETAIL, private="secret"), 1)


def test_arguments_rejected_before_probe(monkeypatch):
    monkeypatch.setattr(diagnostic, "diagnose", lambda repo: pytest.fail("probe ran"))
    assert diagnostic.main(["--anything"]) == 2
    assert diagnostic.main(["unexpected"]) == 2
