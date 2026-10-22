"""Focused synthetic TASK-399 handoff regressions; no live lifecycle dispatch."""

from copy import deepcopy
from dataclasses import asdict, replace
from fnmatch import fnmatchcase
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from aios_renew import brain_attention, brain_context, brain_sync, operator, runtime
from aios_renew.artifacts import Claim, Result, ResultPackage
from aios_renew.review_transport import (
    FailureDiagnosticError, _failure_digest, failure_context, failure_document,
    public_failure, public_run, validate_failure_context, validate_public_failure,
)
from aios_renew.run import Run
from aios_renew.task import parse_task


EXAMPLES = (
    "AC1 missing implementation at src/surface.py; credential=synthetic-secret",
    "AC2 docs/contract.md contradicts tests/test_surface.py::test_contract[secret=synthetic-secret]",
    "AC3 conflicting contract text in docs/contract.md and .ai/tasks/TASK-399.yaml",
    "AC4 evidence absent for tests/test_surface.py::TestSurface::test_evidence; C:/private/source.json",
)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", timeout=10).strip()


def make_case(tmp_path, items=EXAMPLES, *, before_terminal=None, force_gate_failure=False):
    root = tmp_path / "candidate"
    root.mkdir(parents=True)
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Handoff fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "core.autocrlf", "false")
    task_data = dict(task_id="TASK-399", revision=1, goal="Bounded handoff.", problem="Private unresolved source.",
        assumptions=[], scope=dict(inspect=["docs/contract.md", "tests/test_surface.py", ".ai/tasks/TASK-399.yaml"],
            modify=["src/surface.py"]), non_goals=[], constraints=dict(hard=[]),
        acceptance=[dict(id="AC" + str(i), condition="Bounded fact.") for i in range(1, 5)],
        verification=dict(required=["python -m pytest -q tests/test_surface.py"], policy="minimum-sufficient-v1"))
    for name, content in {
        ".ai/tasks/TASK-399.yaml": yaml.safe_dump(task_data),
        "docs/contract.md": "# Authenticated fixture contract\n",
        "tests/test_surface.py": "def test_contract(): pass\nclass TestSurface:\n    def test_evidence(self): pass\n",
        "src/surface.py": "value = 0\n",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "fixture base")
    base = git(root, "rev-parse", "HEAD")
    (root / "src/surface.py").write_text("value = 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "fixture candidate")
    head = git(root, "rev-parse", "HEAD")
    task = parse_task(yaml.safe_dump(task_data))
    run = Run.from_task(run_id="RUN-399-901", task=task, executor="codex", base_sha=base, workspace=str(root))
    state = operator.runtime_paths(root)
    run_data = asdict(run)
    run_data.pop("return_affinity")  # Exact lawful legacy persisted RUN shape.
    run_path = state.runs / (run.run_id + ".json")
    run_path.write_text(json.dumps(run_data), encoding="utf-8")
    package = ResultPackage(Result(head_sha=head,
        claims=(Claim("C1", ("AC1", "AC2", "AC3", "AC4"), "Observed fixture.", ()),),
        changed_files=("src/surface.py",), unresolved=tuple(items)), ())
    calls = []
    completion = runtime.RuntimeCompletion(repo=root, state=state, task=task, run=run, run_path=run_path,
        verification_runner=lambda *a, **k: calls.append((a, k)), observation_tracker=None, error_type=RuntimeError)
    if force_gate_failure:
        # Exercise a contradictory terminal cause against genuinely Runtime-staged
        # empty source: a diagnosis still cannot infer PASS from count zero.
        completion._require_complete_result = lambda package: completion._raise("RESULT has unresolved items")
    # Genuine Runtime staging and the unchanged incomplete-RESULT completion gate.
    try:
        completion.complete(package, runtime.primary_completion_policy(task, base_sha=base))
    except RuntimeError as exc:
        failure = exc
    else:
        raise AssertionError("unresolved ResultPackage unexpectedly completed")
    assert str(failure) == "RESULT has unresolved items" and calls == []
    staged = state.staging / (run.run_id + ".json")
    if before_terminal:
        before_terminal(staged, failure)
    runtime.persist_failure(root, state=state, task=task, run=run, run_path=run_path,
        failure=failure, transport=False)
    failure_path = state.failures / (run.run_id + ".json")
    original = failure_path.read_bytes()
    public = failure_document(public_failure(original, private_original=True))
    context = failure_context(public, artifact_sha="e" * 40, candidate_available=True)
    return SimpleNamespace(root=root, task=task, run=run, run_data=run_data, state=state,
        completion=completion, staged=staged, original=original, failure=failure,
        failure_path=failure_path, public=public, context=context, calls=calls)


class CanonicalSources:
    def __init__(self, case):
        self.case, self.runtime_repo, self.reads, self.moved = case, case.root, 0, False

    def refs(self, *patterns):
        self.reads += 1
        values = {"refs/heads/aios/failure-artifacts/" + self.case.run.run_id: "e" * 40,
            "refs/heads/aios/failure/" + self.case.run.run_id: self.case.context["subject_sha"]}
        if self.moved and self.reads > 1:
            values["refs/heads/aios/failure-artifacts/" + self.case.run.run_id] = "f" * 40
        return {k: v for k, v in values.items() if any(fnmatchcase(k, pattern) for pattern in patterns)}

    def document(self, sha, name):
        assert sha == "e" * 40
        if name == ".ai/transport/failure.json":
            return deepcopy(self.case.public)
        if name == ".ai/transport/run.json":
            return json.loads(public_run(json.dumps(self.case.run_data).encode()))
        raise OSError("fixture document unavailable")

    def failure_detail_document(self, *args):
        return None


def diagnostic(context):
    return context["safe_detail"]["unresolved"]


def assert_safe(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    assert len(raw) <= 8192
    for forbidden in (b"synthetic-secret", b"credential=", b"C:/private", b"missing implementation",
                      b"contradicts", b"conflicting contract text", b"evidence absent"):
        assert forbidden not in raw


def test_terminal_pin_public_detail_authenticated_consumers_and_idempotence(tmp_path, monkeypatch):
    case = make_case(tmp_path)
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "self-hosted")
    pin = json.loads(case.original)["error"]["unresolved_source"]
    assert pin["unresolved_count"] == 4
    assert pin["staged_sha256"] == hashlib.sha256(case.staged.read_bytes()).hexdigest()
    assert diagnostic(case.context)["availability"] == "PINNED"
    assert diagnostic(case.context)["source"]["private_failure_digest"] == _failure_digest(json.loads(case.original))
    before = {p: p.read_bytes() for p in case.state.root.rglob("*.json")}
    observed = operator.observe_runtime_failure_detail(case.root, case.context)
    view = diagnostic(observed)
    assert view["availability"] == "AVAILABLE" and view["unresolved_count"] == 4
    identities = {a["identity"] for a in view["anchors"]}
    assert {"AC1", "AC2", "AC3", "AC4", "src/surface.py", "docs/contract.md", ".ai/tasks/TASK-399.yaml",
        "tests/test_surface.py::test_contract", "tests/test_surface.py::TestSurface::test_evidence"} <= identities
    assert "SEMANTIC_CAUSE_UNKNOWN" in view["limitations"] and "UNRESOLVED_TEXT_WITHHELD" in view["limitations"]
    assert operator.observe_runtime_failure_detail(case.root, observed) == observed
    assert operator.read_executor_unresolved_diagnostic(case.root, admitted_run=case.run, context=case.context) == view
    raw_digest = hashlib.sha256(case.original).hexdigest()
    selected = operator.read_executor_private_diagnostic(case.root, admitted_run=case.run,
        subject_sha=case.context["subject_sha"], locator="runtime-unresolved:" + case.run.run_id,
        expected_sha256=raw_digest, failure_sha256=raw_digest)
    assert json.loads(selected) == view
    sources = CanonicalSources(case)
    item = brain_attention.attention(brain_attention.FAILURE, dict(run_id=case.run.run_id, artifact_sha="e" * 40))
    attention = brain_attention.terminal_failure_context(item, sources)
    assert diagnostic(attention) == view
    monkeypatch.setattr(brain_sync, "_resolve_main_sha", lambda root: case.context["subject_sha"])
    monkeypatch.setattr(brain_sync, "_resolve_repository_name", lambda root: "fixture/AIOS-renew")
    snapshot = brain_sync.observe_brain_sync(repo=case.root, run_id=case.run.run_id, canonical_sources=CanonicalSources(case))
    assert diagnostic(snapshot.diagnostics["failure_context"]) == view
    portable = snapshot.as_dict()
    assert portable["repository"]["root"] == "protected-repository-root"
    assert portable["authority"] == "NONE" and portable["next_action"] == "NONE"
    assert all(portable[key] is False for key in ("run_created", "executor_invoked", "verification_invoked", "state_mutated"))
    work = brain_context.compose_brain_work_context(snapshot).as_dict()
    for value in (case.public, observed, attention, portable, work, view):
        assert_safe(value)
    monkeypatch.setattr(runtime, "_unresolved_test_inventory", lambda *a: pytest.fail("terminal replay repeated inventory Git"))
    runtime.persist_failure(case.root, state=case.state, task=case.task, run=case.run,
        run_path=case.state.runs / (case.run.run_id + ".json"), failure=case.failure, transport=False)
    assert case.failure_path.read_bytes() == case.original
    assert before == {p: p.read_bytes() for p in case.state.root.rglob("*.json")}
    assert not (case.state.results / (case.run.run_id + ".json")).exists() and case.calls == []


@pytest.mark.parametrize("fault,code", [("changed", "SOURCE_MISMATCH"), ("missing", "SOURCE_MISSING"),
    ("truncated", "SOURCE_MISMATCH"), ("oversized", "LIMIT_EXHAUSTED"), ("cross-run", "SOURCE_MISMATCH"),
    ("task", "SOURCE_MISMATCH"), ("private", "SOURCE_MISMATCH"), ("stale-public", "SOURCE_MISMATCH")])
def test_currentness_failures_are_bounded_and_do_not_rewrite_terminal(tmp_path, monkeypatch, fault, code):
    case = make_case(tmp_path)
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "self-hosted")
    context = deepcopy(case.context)
    if fault == "changed":
        case.staged.write_bytes(case.staged.read_bytes() + b" ")
    elif fault == "missing":
        case.staged.rename(case.staged.with_suffix(".moved"))
    elif fault == "truncated":
        case.staged.write_bytes(b'{"result":')
    elif fault == "oversized":
        with case.staged.open("wb") as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
    elif fault == "cross-run":
        data = deepcopy(case.run_data)
        data["run_id"] = "RUN-foreign-001"
        (case.state.runs / (case.run.run_id + ".json")).write_text(json.dumps(data), encoding="utf-8")
    elif fault == "task":
        path = case.root / ".ai/tasks/TASK-399.yaml"
        path.write_text(path.read_text().replace("Bounded fact.", "Changed fact."), encoding="utf-8")
    elif fault == "private":
        data = json.loads(case.original)
        data["failed_head_sha"] = "f" * 40
        case.failure_path.write_text(json.dumps(data), encoding="utf-8")
    elif fault == "stale-public":
        public = deepcopy(case.public)
        public["error"]["safe_detail"]["unresolved"]["unresolved_count"] += 1
        public["public_projection"]["projection_digest"] = _failure_digest({k: v for k, v in public.items() if k != "public_projection"})
        validate_public_failure(public)
        context = failure_context(public)
    view = diagnostic(operator.observe_runtime_failure_detail(case.root, context))
    assert view["blocker"] == code and view["availability"] == "UNAVAILABLE"
    assert view["unresolved_count"] is None and view["anchors"] == []
    assert_safe(view)
    if fault != "private":
        assert case.failure_path.read_bytes() == case.original


@pytest.mark.parametrize("fault", ["receipt-missing", "staging-swapped", "receipt-cross-sha", "receipt-cross-run"])
def test_original_pin_never_authenticates_local_staging_alone(tmp_path, fault):
    def substitute(path, failure):
        if fault == "receipt-missing":
            del failure._runtime_staged_result
        elif fault == "staging-swapped":
            path.write_bytes(path.read_bytes() + b" ")
        elif fault == "receipt-cross-sha":
            failure._runtime_staged_result = replace(failure._runtime_staged_result, head_sha="f" * 40)
        else:
            failure._runtime_staged_result = replace(failure._runtime_staged_result, run_digest="0" * 64)
    case = make_case(tmp_path, before_terminal=substitute)
    view = diagnostic(case.context)
    assert view["availability"] == "UNAVAILABLE" and view["blocker"] == "SOURCE_MISMATCH"
    assert view["unresolved_count"] is None
    assert_safe(case.public)


@pytest.mark.parametrize("raw,code", [
    (b'{"result":{},"result":{},"evidence":[]}', "SOURCE_INVALID"),
    (b'{"result":', "SOURCE_INVALID"),
    (b'{"result":{},"evidence":[],"source":"synthetic-secret"}', "SOURCE_INVALID"),
    (b"[1,2,3]", "SOURCE_INVALID"),
    (b"{" + b'"x":[' * 80 + b"0" + b"]}" * 80, "SOURCE_INVALID"),
    (b" " * (16 * 1024 * 1024 + 1), "LIMIT_EXHAUSTED"),
])
def test_structural_parser_duplicate_schema_and_resource_guards(tmp_path, raw, code):
    case = make_case(tmp_path)
    with pytest.raises(FailureDiagnosticError) as caught:
        runtime._unresolved_package(raw, case.task, case.run, case.context["subject_sha"])
    assert caught.value.code == code and "synthetic-secret" not in str(caught.value)


def test_unauthorized_read_legacy_history_and_unknown_are_distinct_from_zero(tmp_path, monkeypatch):
    case = make_case(tmp_path, items=("synthetic-secret /etc/private.md tests/test_unknown.py::secret_token AC999",))
    monkeypatch.delenv("RUNNER_ENVIRONMENT", raising=False)
    assert diagnostic(operator.observe_runtime_failure_detail(case.root, case.context))["blocker"] == "ACCESS_DENIED"
    denied = operator.read_executor_unresolved_diagnostic(case.root,
        admitted_run=replace(case.run, run_id="RUN-other-001"), context=case.context)
    assert denied["blocker"] == "ACCESS_DENIED" and denied["unresolved_count"] is None
    # Active persisted admission can use the same safe view without a host-env bypass.
    view = operator.read_executor_unresolved_diagnostic(case.root, admitted_run=case.run, context=case.context)
    assert view["availability"] == "AVAILABLE"
    assert [a["type"] for a in view["anchors"]] == ["OPAQUE_ITEM"]
    assert_safe(view)
    legacy = json.loads(case.original)
    del legacy["error"]["unresolved_source"]
    case.failure_path.write_text(json.dumps(legacy), encoding="utf-8")
    context = failure_context(failure_document(public_failure(json.dumps(legacy).encode(), private_original=True)))
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "self-hosted")
    limited = diagnostic(operator.observe_runtime_failure_detail(case.root, context))
    assert limited["blocker"] == "LEGACY_UNBOUND" and limited["unresolved_count"] is None
    assert limited["source"]["staged_sha256"] is None
    # The parser can actually observe zero, whereas legacy absence cannot.
    raw = json.loads(case.staged.read_bytes())
    raw["result"]["unresolved"] = []
    assert runtime._unresolved_package(json.dumps(raw).encode(), case.task, case.run, case.context["subject_sha"]).result.unresolved == ()


def test_pin_zero_requires_observed_source_and_blockers_cannot_retain_count(tmp_path, monkeypatch):
    from aios_renew.review_transport import validate_unresolved_diagnostic
    case = make_case(tmp_path, items=(), force_gate_failure=True)
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "self-hosted")
    zero = diagnostic(operator.observe_runtime_failure_detail(case.root, case.context))
    assert zero["unresolved_count"] == 0 and zero["proof"] == "NON_PASS_DIAGNOSTIC"
    case.staged.unlink()
    unavailable = diagnostic(operator.observe_runtime_failure_detail(case.root, case.context))
    assert unavailable["unresolved_count"] is None
    owner = dict(case.context, private_failure_digest=case.context["source"]["private_failure_digest"])
    with pytest.raises(ValueError):
        validate_unresolved_diagnostic(dict(unavailable, unresolved_count=0), owner)


def test_private_link_is_denied_before_source_open_and_admission_is_not_a_selector(tmp_path, monkeypatch):
    case = make_case(tmp_path)
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "self-hosted")
    actual_lstat = Path.lstat
    def linked(path, *args, **kwargs):
        info = actual_lstat(path, *args, **kwargs)
        if path == case.staged:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, "lstat", linked)
    assert diagnostic(operator.observe_runtime_failure_detail(case.root, case.context))["blocker"] == "ACCESS_DENIED"
    monkeypatch.setattr(Path, "lstat", actual_lstat)
    monkeypatch.delenv("RUNNER_ENVIRONMENT", raising=False)
    def no_private_reads(*args):
        raise AssertionError("unauthenticated admission opened protected source")
    original_read = runtime._read_unresolved_bytes
    def admission_only(path, limit):
        if path.parent == case.state.failures:
            return no_private_reads()
        return original_read(path, limit)
    monkeypatch.setattr(runtime, "_read_unresolved_bytes", admission_only)
    forged = replace(case.run, executor="antigravity")
    denied = operator.observe_runtime_failure_detail(case.root, case.context, _admitted_run=forged)
    assert diagnostic(denied)["blocker"] == "ACCESS_DENIED"


def test_versioned_legacy_bytes_and_historical_unbound_identity_are_preserved(tmp_path, monkeypatch):
    from aios_renew import review_transport as transport
    case = make_case(tmp_path)
    private = json.loads(case.original)
    for projection in (transport.LEGACY_PUBLIC_FAILURE_FORMAT, transport.PUBLIC_FAILURE_FORMAT):
        for version in (1, 2, 3):
            public = transport._project_failure(private, projection_format=projection, _detail_version=version)
            encoded = transport._public_bytes(public)
            assert transport.public_failure(encoded) == encoded
    historical = deepcopy(case.public)
    historical.update(run_id="RUN-398-004", task=dict(id="TASK-398", revision=1),
        failed_head_sha="2e97623c2ac283e151ffdc41485a90b8df14b19d")
    historical["public_projection"]["private_failure_digest"] = "b2e60c61f1d6885802e68c81612355445b290c936754f7eda6faba77b17b0455"
    detail = historical["error"]["safe_detail"]
    detail.pop("unresolved")
    detail.update(version=2, run_id=historical["run_id"], subject_sha=historical["failed_head_sha"],
        private_failure_digest=historical["public_projection"]["private_failure_digest"])
    historical["public_projection"]["projection_digest"] = _failure_digest({k: v for k, v in historical.items() if k != "public_projection"})
    original = transport._public_bytes(historical)
    validate_public_failure(historical)
    context = failure_context(historical, artifact_sha="70fec2ab3f681f51c3bdfa40f51bcba50fa33852")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "self-hosted")
    view = diagnostic(operator.observe_runtime_failure_detail(case.root, context))
    assert view["blocker"] == "LEGACY_UNBOUND" and view["unresolved_count"] is None
    assert view["source"]["staged_sha256"] is None
    assert transport.public_failure(original) == original and case.failure_path.read_bytes() == case.original


def test_anchor_caps_source_swap_after_available_and_ref_race(tmp_path, monkeypatch):
    case = make_case(tmp_path, items=tuple("AC1 src/surface.py item " + str(i) for i in range(80)))
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "self-hosted")
    observed = operator.observe_runtime_failure_detail(case.root, case.context)
    view = diagnostic(observed)
    assert view["unresolved_count"] == 80 and len(view["anchors"]) <= 32
    assert "ANCHORS_TRUNCATED" in view["limitations"]
    assert_safe(observed)
    case.staged.write_bytes(case.staged.read_bytes() + b" ")
    assert diagnostic(operator.observe_runtime_failure_detail(case.root, observed))["blocker"] == "SOURCE_MISMATCH"
    sources = CanonicalSources(case)
    sources.moved = True
    item = brain_attention.attention(brain_attention.FAILURE, dict(run_id=case.run.run_id, artifact_sha="e" * 40))
    with pytest.raises(brain_attention.AttentionError, match="FAILURE_SOURCE_MOVED"):
        brain_attention.terminal_failure_context(item, sources)


def test_public_schema_is_closed_and_portable_consumers_fail_closed_at_cap(tmp_path):
    case = make_case(tmp_path)
    for field, value in (("raw_text", "synthetic-secret"), ("unresolved_count", True), ("unresolved_count", None)):
        forged = deepcopy(case.public)
        forged["error"]["safe_detail"]["unresolved"][field] = value
        forged["public_projection"]["projection_digest"] = _failure_digest({k: v for k, v in forged.items() if k != "public_projection"})
        with pytest.raises(ValueError):
            validate_public_failure(forged)
    contexts = dict(failure_context=case.context, operational_context=None, attributed_failure_context=None, blocker=None)
    snapshot = brain_sync.BrainSyncSnapshot(repository=dict(root=str(case.root), name="fixture/AIOS-renew"),
        main_sha=case.context["subject_sha"], roadmap={}, selection_status="EXACT_DIAGNOSTIC_SUBJECT",
        lifecycle_state="OBSERVATION", next_action="NONE", authority="NONE", selected_task=case.context["task"], diagnostics=contexts)
    assert_safe(snapshot.as_dict())
    with pytest.raises(brain_sync.BrainSyncError, match="LIMIT_EXHAUSTED"):
        replace(snapshot, roadmap={"note": "x" * 8192}).render()
    work = brain_context.compose_brain_work_context(snapshot, current_request={"human_input": "x" * 8192})
    with pytest.raises(brain_context.BrainContextError, match="LIMIT_EXHAUSTED"):
        work.render()
