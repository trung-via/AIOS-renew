"""Prospective safe FAILURE pages over real, isolated Git artifacts.

These fixtures execute no verification command and create no AIOS execution.
Resource observations are test-local measurements, never lifecycle EVIDENCE.
"""

from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess
import time
import tracemalloc

import pytest

from aios_renew import review_transport as rt
from aios_renew.brain_attention import GitSources, attention, FAILURE, terminal_failure_context, terminal_failure_page
from aios_renew.verification_contract import verification_digest
from tests.test_review_transport import (
    _failure_transport_recovery_fixture, _task393_summary, _task393_summary_failure,
)


def structured_failure(count, *, complete=True, invalid=0):
    run, private, _ = _failure_transport_recovery_fixture(nodes=0, failures=0)
    reference = private["error"]["verification"][0]["verification"]
    observed = reference["candidate"]
    detail = "AssertionError: synthetic-secret C:/private/host"
    observed.update(complete=complete, unstable=not complete, exit_code=1,
        failure_count=count + invalid, reports=[dict(
            nodeid=f"tests/test_many.py::test_case[{number:05d}-synthetic-secret]",
            phase="call", outcome="FAIL", detail=detail, fingerprint=verification_digest(detail),
            profile=observed["profile"], toolchain=observed["toolchain"])
            for number in range(count)])
    observed["reports"].extend(dict(observed["reports"][0], nodeid=f"private/hidden.py::test_hidden[{n}]") for n in range(invalid))
    observed["reports"].sort(key=lambda r: (r["nodeid"], r["phase"]))
    reference["candidate_digest"] = verification_digest(observed)
    private["error"]["verification"][0]["exit_code"] = 1
    return run, private


def summary_failure(count):
    private = _task393_summary_failure(_task393_summary([
        f"FAILED tests/test_many.py::test_case[{n:05d}-synthetic-secret] - AssertionError: C:/private/host"
        for n in range(count)], footer=f"{count} failed"))
    run = dict(run_id=private["run_id"], task=private["task"], executor=private["executor"],
        base_sha=private["base_sha"], workspace="C:/private/host", status="ACTIVE", head_sha=None)
    return run, private


class GitFailure:
    """A clean reader knows only a Git remote and exact terminal attention."""

    def __init__(self, path, run, private, *, mutate_inputs=None, historical=False):
        self.path, self.run, self.private = path, run, private
        path.mkdir(parents=True)
        self.remote, self.reader = path / "canonical.git", path / "reader"
        self.calls, self.output_bytes = [], 0
        self.git(path, "init", "--bare", str(self.remote))
        self.git(self.remote, "config", "user.name", "Safe projection fixture")
        self.git(self.remote, "config", "user.email", "fixture@example.invalid")
        run_path, failure_path = path / "run.json", path / "failure.json"
        run_path.write_bytes(rt._public_bytes(run, max_bytes=rt.PUBLIC_FAILURE_MAX_BYTES))
        failure_path.write_bytes(rt._public_bytes(private, max_bytes=rt.PUBLIC_FAILURE_MAX_BYTES))
        self.public = json.loads(rt.public_failure(failure_path.read_bytes(), private_original=True))
        inputs = {".ai/transport/run.json": rt.public_run(run_path.read_bytes()),
                  ".ai/transport/failure.json": rt._public_bytes(self.public, max_bytes=rt.PUBLIC_FAILURE_MAX_BYTES)}
        self.projected = {} if historical else rt._failure_detail_projection_inputs(private, self.public)
        inputs.update(self.projected)
        if mutate_inputs:
            mutate_inputs(inputs)
        self.sha = rt._create_named_artifacts_commit(self.remote, run_path=run_path,
            artifact_path=failure_path, artifact_name="failure.json", run_id=run["run_id"], projected_inputs=inputs)
        self.ref = "refs/heads/aios/failure-artifacts/" + run["run_id"]
        self.git(self.remote, "update-ref", self.ref, self.sha)
        self.git(path, "init", str(self.reader))
        self.sources = GitSources(self.reader, self.git, str(self.remote))
        self.item = attention(FAILURE, dict(run_id=run["run_id"], artifact_sha=self.sha))
        self.calls.clear()
        self.output_bytes = 0

    def git(self, path, *args):
        result = subprocess.run(("git", "-C", str(path), *args), capture_output=True, check=True)
        self.calls.append(args)
        self.output_bytes += len(result.stdout)
        return result.stdout.decode("utf-8", errors="strict").strip()

    def discover(self):
        result = terminal_failure_context(self.item, self.sources, include_paging=True)
        rt.validate_failure_context(result)
        return result

    def page(self, selector):
        result = terminal_failure_page(selector, self.sources)
        rt.validate_failure_detail_view(result)
        return result


@pytest.mark.parametrize("count", [33, 64, 1000, 10000])
def test_complete_population_real_git_pagination_and_resource_bounds(tmp_path, count, record_property):
    run, private = structured_failure(count)
    tracemalloc.start()
    cpu, wall = time.process_time(), time.perf_counter()
    fixture = GitFailure(tmp_path / "scale", run, private)
    build_cpu = time.process_time() - cpu
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    read_cpu = time.process_time()
    context = fixture.discover()
    command = context["paging"]["commands"][0]
    assert command["coverage"] == "COMPLETE" and command["failure_count"] == count
    assert context["safe_detail"]["version"] == (3 if count > 4096 else 2)
    selector, digests, pages, output = command["first_selector"], [], 0, 0
    while selector is not None:
        calls_before = len(fixture.calls)
        view = fixture.page(selector)
        assert len(fixture.calls) - calls_before <= 10
        assert view["availability"] == "AVAILABLE" and view["blocker"] is None
        page = view["page"]
        assert page["start"] == pages * 32 and len(page["failed_nodes"]) <= 32
        assert page["failure_count"] == count and page["observed_count"] == count
        assert all(n["identity"] == "tests/test_many.py::test_case" and n["parameters_withheld"] for n in page["failed_nodes"])
        digests.extend(n["identity_digest"] for n in page["failed_nodes"])
        encoded = rt._public_bytes(view, max_bytes=rt.FAILURE_VIEW_MAX_BYTES)
        assert all(s not in encoded for s in (b"synthetic-secret", b"C:/private", b"traceback", b"raw_path", b"python_executable"))
        output += len(encoded)
        selector = view["next_selector"]
        assert page["exhausted"] is (selector is None)
        assert page["nodes_truncated"] is (selector is not None)
        pages += 1
    expected = [verification_digest(r["nodeid"]) for r in private["error"]["verification"][0]["verification"]["candidate"]["reports"]]
    assert digests == expected and len(set(digests)) == count
    assert pages == math.ceil(count / 32)
    # One immutable fetch per observation backend, bounded reads and safe bytes.
    assert sum(c[0] == "fetch" for c in fixture.calls) == 1
    assert len(fixture.calls) <= 12 + 10 * pages
    assert sum(map(len, fixture.projected.values())) <= rt.FAILURE_PROJECTION_MAX_BYTES
    assert output <= rt.FAILURE_VIEW_MAX_BYTES * pages
    assert fixture.output_bytes <= (rt.PUBLIC_FAILURE_MAX_BYTES + 2 * rt.FAILURE_MANIFEST_MAX_BYTES) * (pages + 1)
    disk = sum(p.stat().st_size for root in (fixture.remote, fixture.reader) for p in root.rglob("*") if p.is_file())
    assert disk < 64 * 1024 * 1024
    assert peak < 160 * 1024 * 1024 and build_cpu < 90
    read_cpu = time.process_time() - read_cpu
    assert read_cpu < 5 + 2 * pages
    record_property("bounded_resources", json.dumps(dict(count=count, build_cpu_seconds=build_cpu,
        read_cpu_seconds=read_cpu, wall_seconds=time.perf_counter() - wall, peak_bytes=peak, git_calls=len(fixture.calls),
        git_output_bytes=fixture.output_bytes, view_bytes=output, projection_bytes=sum(map(len, fixture.projected.values())), disk_bytes=disk)))


def test_text_population_is_partial_and_exact_parameter_instances_survive(tmp_path):
    fixture = GitFailure(tmp_path / "text", *summary_failure(64))
    context = fixture.discover()
    entry = context["paging"]["commands"][0]
    assert entry["coverage"] == "PARTIAL" and entry["failure_count"] is None
    first = fixture.page(entry["first_selector"])
    second = fixture.page(first["next_selector"])
    assert first["availability"] == second["availability"] == "PARTIAL"
    assert second["page"]["limitation"] == "TEXT_POPULATION_UNKNOWN"
    assert second["next_selector"] is None
    assert fixture.page(first["next_selector"]) == second
    assert {n["identity_digest"] for n in first["page"]["failed_nodes"]}.isdisjoint(
        n["identity_digest"] for n in second["page"]["failed_nodes"])


@pytest.mark.parametrize("fault,code", [
    ("digest", "SELECTOR_INVALID"), ("run", "FAILURE_SOURCE_INTEGRITY_INVALID"),
    ("task", "SELECTOR_EXPIRED"), ("subject", "SELECTOR_EXPIRED"),
    ("command", "PAGE_OUT_OF_RANGE"), ("source", "SELECTOR_INVALID"),
    ("manifest", "SELECTOR_EXPIRED"), ("range", "PAGE_OUT_OF_RANGE"),
    ("unaligned", "SELECTOR_INVALID"), ("extra", "SELECTOR_INVALID"),
    ("expired", "FAILURE_SOURCE_INTEGRITY_INVALID"),
])
def test_forged_cross_source_and_out_of_range_selectors_fail_closed(tmp_path, fault, code):
    fixture = GitFailure(tmp_path / fault, *summary_failure(64))
    selector = deepcopy(fixture.discover()["paging"]["commands"][0]["first_selector"])
    if fault == "digest": selector["page_digest"] = "0" * 64
    if fault == "run": selector["binding"]["run_id"] = "RUN-foreign-001"
    if fault == "task": selector["binding"]["task"]["revision"] += 1
    if fault == "subject": selector["binding"]["subject_sha"] = "f" * 40
    if fault == "command": selector["binding"]["index"] = 1
    if fault == "source": selector["binding"]["source_digest"] = "0" * 64
    if fault == "manifest": selector["manifest_digest"] = "0" * 64
    if fault == "range": selector.update(start=96, stop=128)
    if fault == "unaligned": selector.update(start=1, stop=33)
    if fault == "extra": selector["source"] = "caller supplied raw synthetic-secret"
    if fault == "expired": fixture.git(fixture.remote, "update-ref", "-d", fixture.ref)
    selector["selector_digest"] = verification_digest({k: v for k, v in selector.items() if k != "selector_digest"})
    view = fixture.page(selector)
    assert view == rt.failure_page_unavailable(code)


@pytest.mark.parametrize("fault", ["page", "missing-page", "manifest", "missing-manifest", "oversized-page"])
def test_malformed_or_missing_projected_source_never_uses_caller_or_local_raw(tmp_path, fault):
    fixture = GitFailure(tmp_path / "original", *summary_failure(64))
    context = fixture.discover()
    selector = context["paging"]["commands"][0]["first_selector"]
    page_name = rt._page_name(0, 0)
    def corrupt(inputs):
        if fault == "page":
            page = json.loads(inputs[page_name]); page["failed_nodes"][0]["identity"] = "C:/private/secret"
            inputs[page_name] = rt._public_bytes(page)
        if fault == "missing-page": inputs.pop(page_name)
        if fault == "manifest":
            manifest = json.loads(inputs[rt.FAILURE_DETAIL_MANIFEST]); manifest["binding"]["subject_sha"] = "d" * 40
            inputs[rt.FAILURE_DETAIL_MANIFEST] = rt._public_bytes(manifest)
        if fault == "missing-manifest": inputs.pop(rt.FAILURE_DETAIL_MANIFEST)
    altered = GitFailure(tmp_path / "altered", *summary_failure(64), mutate_inputs=corrupt)
    if fault == "oversized-page":
        # Exercise the reader's predecode size guard independently of writer,
        # which already refuses to store such a blob.
        original = altered.sources.git
        def oversized(path, *args):
            if args[:2] == ("ls-tree", "-l") and args[-1] == page_name:
                return f"100644 blob {'a' * 40} {rt.FAILURE_PAGE_MAX_BYTES + 1}\t{page_name}"
            return original(path, *args)
        altered.sources.git = oversized
    selector["artifact_sha"] = altered.sha
    selector["selector_digest"] = verification_digest({k: v for k, v in selector.items() if k != "selector_digest"})
    result = altered.page(selector)
    assert result["availability"] == "UNAVAILABLE" and result["page"] is None
    assert result["blocker"] in {"PAGE_INVALID", "PROJECTION_INVALID", "PROJECTION_UNAVAILABLE", "PROJECTION_BUDGET_EXHAUSTED"}


def test_ref_change_during_page_read_fails_closed(tmp_path):
    fixture = GitFailure(tmp_path / "movement", *summary_failure(33))
    selector = fixture.discover()["paging"]["commands"][0]["first_selector"]
    reader = fixture.sources.failure_detail_document
    def moved(sha, name, limit):
        result = reader(sha, name, limit)
        if name != rt.FAILURE_DETAIL_MANIFEST:
            fixture.git(fixture.remote, "update-ref", "-d", fixture.ref)
        return result
    fixture.sources.failure_detail_document = moved
    assert fixture.page(selector) == rt.failure_page_unavailable("FAILURE_SOURCE_MOVED")


def test_each_page_validates_authoritative_failure_once(tmp_path, monkeypatch):
    fixture = GitFailure(tmp_path / "once", *summary_failure(33))
    selector = fixture.discover()["paging"]["commands"][0]["first_selector"]
    calls = []
    validate = rt.validate_public_failure
    def observed(value):
        calls.append(value["run_id"])
        return validate(value)
    monkeypatch.setattr(rt, "validate_public_failure", observed)
    assert fixture.page(selector)["availability"] == "PARTIAL"
    assert calls == [fixture.run["run_id"]]


@pytest.mark.parametrize("fault", ["withheld", "node", "extra"])
def test_provider_decoder_rejects_raw_fields_even_after_caller_rehash(tmp_path, fault):
    fixture = GitFailure(tmp_path / "decoder", *summary_failure(1))
    view = fixture.page(fixture.discover()["paging"]["commands"][0]["first_selector"])
    if fault == "withheld": view["page"]["withheld_count"] = "C:/private/synthetic-secret"
    if fault == "node": view["page"]["failed_nodes"][0]["identity"] = "tests/test_many.py::test_case[synthetic-secret]"
    if fault == "extra": view["page"]["native_output"] = "synthetic-secret"
    selector = view["selector"]
    selector["page_digest"] = verification_digest(view["page"])
    selector["selector_digest"] = verification_digest({k: v for k, v in selector.items() if k != "selector_digest"})
    with pytest.raises(rt.FailureDiagnosticError): rt.validate_failure_detail_view(view)


@pytest.mark.parametrize("summary,command,blocker", [
    ("", "python -m pytest", "SOURCE_ABSENT"),
    ("native arbitrary output", "python -m pytest", "SUMMARY_ABSENT"),
    (_task393_summary(["FAILED tests/test_spoof.py::test_false"]) +
     "==== short test summary info ====\nFAILED tests/test_actual.py::test_real\n", "python -m pytest", "FINAL_SUMMARY_UNCLOSED"),
    (_task393_summary(["FAILED tests/test_many.py::test_real"]), "git diff --check", "UNSUPPORTED_COMMAND"),
    (_task393_summary(["ERROR C:/private/hidden.py::test_secret"], footer="1 error"), "python -m pytest", "NO_PARSEABLE_IDENTITIES"),
])
def test_bounded_typed_inability_for_text_sources(tmp_path, summary, command, blocker):
    run, private = summary_failure(1)
    private["error"]["verification"][0].update(summary=summary, command=command)
    fixture = GitFailure(tmp_path / blocker, run, private)
    entry = fixture.discover()["paging"]["commands"][0]
    assert entry["blocker"] == blocker and entry["first_selector"] is None
    assert entry["failure_count"] is None and entry["coverage"] == "PARTIAL"


def test_partial_structured_and_allowlist_limitations_remain_explicit(tmp_path):
    fixture = GitFailure(tmp_path / "partial", *structured_failure(33, complete=False))
    entry = fixture.discover()["paging"]["commands"][0]
    view = fixture.page(entry["first_selector"])
    assert view["availability"] == "PARTIAL" and view["page"]["failure_count"] is None
    assert view["page"]["limitation"] == "OBSERVATION_INCOMPLETE"
    complete = GitFailure(tmp_path / "withheld", *structured_failure(33, invalid=1))
    entry = complete.discover()["paging"]["commands"][0]
    assert entry["failure_count"] == 34 and entry["observed_count"] == 33 and entry["withheld_count"] == 1


def test_projection_population_and_output_limits_return_blockers(tmp_path, monkeypatch):
    fixture = GitFailure(tmp_path / "population", *summary_failure(10001))
    entry = fixture.discover()["paging"]["commands"][0]
    assert entry["blocker"] == "OBSERVED_POPULATION_LIMIT" and entry["first_selector"] is None
    monkeypatch.setattr(rt, "FAILURE_PROJECTION_MAX_BYTES", rt.FAILURE_MANIFEST_MAX_BYTES)
    fixture = GitFailure(tmp_path / "budget", *summary_failure(33))
    entry = fixture.discover()["paging"]["commands"][0]
    assert entry["blocker"] == "PROJECTION_BUDGET_EXHAUSTED" and entry["first_selector"] is None


def test_scan_exhaustion_and_zero_observed_nodes_never_imply_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "_PYTEST_SUMMARY_SCAN_BYTES", 64)
    fixture = GitFailure(tmp_path / "exhausted", *summary_failure(33))
    entry = fixture.discover()["paging"]["commands"][0]
    assert entry["blocker"] == "SCAN_BUDGET_EXHAUSTED" and entry["first_selector"] is None
    run, private = structured_failure(0, complete=False)
    fixture = GitFailure(tmp_path / "empty", run, private)
    entry = fixture.discover()["paging"]["commands"][0]
    view = fixture.page(entry["first_selector"])
    assert view["availability"] == "PARTIAL" and view["page"]["failed_nodes"] == []
    assert view["page"]["failure_count"] is None and view["page"]["exit_code"] == 1
    assert view["page"]["exhausted"] and "PASS" not in json.dumps(view).replace("NON_PASS_DIAGNOSTIC", "")


def test_command_order_and_aggregate_population_are_source_bound(tmp_path):
    run, private = summary_failure(33)
    first = private["error"]["verification"][0]
    private["error"]["verification"] = [dict(command="git diff --check", exit_code=0, summary=""), first,
        dict(first, command="python -m pytest -q tests/test_other.py")]
    fixture = GitFailure(tmp_path / "ordered", run, private)
    entries = fixture.discover()["paging"]["commands"]
    assert [e["index"] for e in entries] == [1, 2]
    selector = deepcopy(entries[0]["first_selector"])
    selector["binding"]["index"] = 2
    selector["selector_digest"] = verification_digest({k: v for k, v in selector.items() if k != "selector_digest"})
    assert fixture.page(selector) == rt.failure_page_unavailable("SELECTOR_INVALID")
    run, private = summary_failure(6000)
    private["error"]["verification"][0]["summary"] = _task393_summary([
        f"FAILED tests/test_many.py::test_case[{n}]" for n in range(6000)], footer="6000 failed")
    private["error"]["verification"].append(deepcopy(private["error"]["verification"][0]))
    fixture = GitFailure(tmp_path / "aggregate", run, private)
    entries = fixture.discover()["paging"]["commands"]
    assert entries[0]["observed_count"] == 6000
    assert entries[1]["blocker"] == "OBSERVED_POPULATION_LIMIT" and entries[1]["first_selector"] is None


def test_historical_failure_is_unchanged_and_cannot_mint_pages(tmp_path):
    fixture = GitFailure(tmp_path / "historical", *summary_failure(33), historical=True)
    old = terminal_failure_context(fixture.item, fixture.sources)
    assert fixture.discover() == old and "paging" not in old
    prospective = GitFailure(tmp_path / "prospective", *summary_failure(33))
    selector = prospective.discover()["paging"]["commands"][0]["first_selector"]
    selector.update(artifact_sha=fixture.sha)
    selector["selector_digest"] = verification_digest({k: v for k, v in selector.items() if k != "selector_digest"})
    assert fixture.page(selector) == rt.failure_page_unavailable("PROJECTION_UNAVAILABLE")


def test_versioned_count_ceiling_preserves_v1_v2_decoders():
    _, private = structured_failure(1)
    public = json.loads(rt.public_failure(rt._public_bytes(private), private_original=True))
    owner = dict(run_id=private["run_id"], subject_sha=private["failed_head_sha"], phase="VERIFICATION",
        private_failure_digest=verification_digest(private), verification=public["error"]["verification"])
    for version in (1, 2, 3):
        detail = rt._safe_failure_detail(private, version=version)
        rt.validate_safe_failure_detail(detail, owner)
        detail["verification"][0]["failure_count"] = 10000
        if version < 3:
            with pytest.raises(ValueError): rt.validate_safe_failure_detail(detail, owner)
        else:
            rt.validate_safe_failure_detail(detail, owner)


@pytest.mark.parametrize("state", ["denied", "missing", "changed"])
def test_optional_private_detail_still_requires_owner_and_exact_original(tmp_path, monkeypatch, state):
    from aios_renew import operator
    fixture = GitFailure(tmp_path / "private", *summary_failure(1), historical=True)
    context = fixture.discover()
    context["safe_detail"]["availability"] = "UNAVAILABLE"
    context["safe_detail"]["verification"] = []
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "self-hosted" if state != "denied" else "github-hosted")
    failure_dir = tmp_path / "private-originals"
    failure_dir.mkdir()
    class Paths:
        failures = failure_dir
    monkeypatch.setattr(operator, "_runtime_paths_readonly", lambda repo: Paths())
    if state == "changed":
        original = deepcopy(fixture.private); original["error"]["verification"][0]["summary"] += "changed"
        (failure_dir / (context["run_id"] + ".json")).write_bytes(rt._public_bytes(original))
    observed = operator.observe_runtime_failure_detail(tmp_path, context)
    expected = {"denied": "PRIVATE_DETAIL_ACCESS_DENIED", "missing": "PRIVATE_DETAIL_UNAVAILABLE", "changed": "PRIVATE_DETAIL_INTEGRITY_INVALID"}
    assert observed["detail_blocker"]["code"] == expected[state]
    assert "paging" not in observed and "synthetic-secret" not in json.dumps(observed)
