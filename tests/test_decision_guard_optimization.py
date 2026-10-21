"""Native-Git guard regressions and paired, local-transport measurements.

The comparison loads the exact pre-G1 source from an immutable Git commit. Fixture
creation/warming is outside the timer. These are subprocess counts and raw local
elapsed times, never GitHub latency or canonical verification evidence.
Cold readers contain the prerequisite main ancestry and proposed writer commit;
warm readers additionally contain every decision object. Both use the same refs.

Executor baseline capture (before changing production helpers)::

    python tests/test_decision_guard_optimization.py --capture-baseline PATH

Reproduce the paired comparison with ``--compare PATH`` or the focused tests.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from aios_renew import publication as candidate


BASELINE_SHA = "ce498546c68dce7196cc821a9d895dedce5b2f54"
REPETITIONS = 5
DECISION = "refs/heads/aios/review-decision/"
SOURCE = "refs/heads/aios/review/"
REMOTE_CATEGORIES = ("ls-remote", "fetch", "push")
MEASURED_SURFACE = "_pending_review_guard + mandatory _recheck_binding; no main write"


def git(repo: Path, *args: str, data: bytes | None = None) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args), input=data, capture_output=True,
        check=True, timeout=60,
    ).stdout.decode("utf-8").strip()


def baseline_module() -> ModuleType:
    source = git(ROOT, "show", f"{BASELINE_SHA}:src/aios_renew/publication.py")
    name = "aios_renew._task398_pre_g1_publication"
    module = ModuleType(name)
    module.__package__ = "aios_renew"
    sys.modules[name] = module
    exec(compile(source, f"{BASELINE_SHA}:publication.py", "exec"), module.__dict__)
    return module


def _data(content: bytes) -> bytes:
    return f"data {len(content)}\n".encode() + content + b"\n"


def population(root: Path, count: int, *, padding: int = 0) -> Path:
    """Distinct historical source/decision commits, all sources included by main."""
    remote = root / "remote.git"
    remote.mkdir(parents=True)
    git(remote, "init", "--bare", "--quiet")
    stream = bytearray(b"blob\nmark :1\ndata 5\nbase\n\n")
    previous = None
    for index in range(count + 1):
        ref = SOURCE + f"RUN-900-{index:04}" + "x" * padding if index < count else "refs/heads/main"
        stream.extend(f"commit {ref}\nmark :{index + 2}\n".encode())
        stream.extend(b"committer Guard Fixture <guard@example.invalid> 1700000000 +0000\n")
        stream.extend(_data(f"source {index}\n".encode()))
        if previous is not None:
            stream.extend(f"from :{previous}\n".encode())
        stream.extend(b"M 100644 :1 product.txt\n\n")
        previous = index + 2
    git(remote, "fast-import", "--quiet", data=bytes(stream))
    sources = dict(line.split(" ", 1) for line in git(
        remote, "for-each-ref", "--format=%(refname) %(objectname)", SOURCE,
    ).splitlines())
    stream = bytearray()
    for index, (ref, sha) in enumerate(sorted(sources.items())):
        run = ref[len(SOURCE):]
        review = (f"review_id: REVIEW-900-{index:04}\nreviewed_sha: {sha}\n"
                  "mode: PRIMARY\nverdict: PASS\nacceptance: {AC1: PASS}\nfindings: []\n")
        stream.extend(f"blob\nmark :{index + 1}\n".encode())
        stream.extend(_data(review.encode()))
        stream.extend(f"commit {DECISION}{run}\n".encode())
        stream.extend(b"committer Guard Fixture <guard@example.invalid> 1700000000 +0000\n")
        stream.extend(_data(f"decision {index}\n".encode()))
        stream.extend(f"from {sha}\nM 100644 :{index + 1} .ai/reviews/REVIEW-900-{index:04}.yaml\n\n".encode())
    if stream:
        git(remote, "fast-import", "--quiet", data=bytes(stream))
    return remote


def seed_included_decisions(remote: Path, main: str, count: int) -> None:
    """Add historical exact review metadata to an existing native-Git fixture."""
    stream = bytearray()
    for index in range(count):
        run = f"RUN-900-{index:04}"
        review = (f"review_id: REVIEW-900-{index:04}\nreviewed_sha: {main}\n"
                  "mode: PRIMARY\nverdict: PASS\nacceptance: {AC1: PASS}\nfindings: []\n")
        stream.extend(f"reset {SOURCE}{run}\nfrom {main}\n\nblob\nmark :{index + 1}\n".encode())
        stream.extend(_data(review.encode()))
        stream.extend(f"commit {DECISION}{run}\n".encode())
        stream.extend(b"committer Guard Fixture <guard@example.invalid> 1700000000 +0000\n")
        stream.extend(_data(f"included decision {index}\n".encode()))
        stream.extend(f"from {main}\nM 100644 :{index + 1} .ai/reviews/REVIEW-900-{index:04}.yaml\n\n".encode())
    if stream:
        git(remote, "fast-import", "--quiet", data=bytes(stream))


def reader(root: Path, remote: Path, *, warm: bool) -> tuple[Path, str, str]:
    """Prepare identical main/candidate prerequisites, then optionally warm metadata."""
    root.mkdir(parents=True)
    git(root, "init", "--quiet")
    git(root, "config", "user.name", "Guard Fixture")
    git(root, "config", "user.email", "guard@example.invalid")
    git(root, "remote", "add", "origin", str(remote))
    git(root, "fetch", "--quiet", "--no-tags", "origin", "refs/heads/main")
    main = git(root, "rev-parse", "FETCH_HEAD")
    if warm:
        git(root, "fetch", "--quiet", "--no-tags", "origin",
            "+refs/heads/aios/*:refs/remotes/fixture/*")
    tree = git(root, "rev-parse", f"{main}^{{tree}}")
    proposed = git(root, "commit-tree", tree, "-p", main, data=b"proposed main writer\n")
    return root, main, proposed


def measure(module: ModuleType, repo: Path, main: str, proposed: str) -> dict:
    counts = dict.fromkeys(REMOTE_CATEGORIES, 0)
    original = subprocess.Popen

    def record(command, *args, **kwargs):
        # Count real native subprocess launches, including any helper's remote calls.
        if isinstance(command, (tuple, list)) and Path(command[0]).name.lower() in {"git", "git.exe"}:
            for category in REMOTE_CATEGORIES:
                if category in command[1:]:
                    counts[category] += 1
                    break
        return original(command, *args, **kwargs)

    started = time.perf_counter()
    with patch.object(subprocess, "Popen", record):
        decisions, binding = module._pending_review_guard(
            repo, "origin", run_id="TASK-398", reviewed_sha=proposed, main_sha=main,
        )
        module._recheck_binding(repo, "origin", binding, run_id="TASK-398",
                                reviewed_sha=proposed, main_sha=main, decisions=decisions)
    elapsed = time.perf_counter() - started
    return {
        "remote_calls": counts, "remote_total": sum(counts.values()),
        "elapsed_seconds": elapsed,
        "outcome": {"status": "ALLOWED", "decisions": decisions, "sources": binding},
    }


def capture_baseline(path: Path) -> None:
    root = Path(tempfile.mkdtemp(prefix="task398-paired-"))
    old = baseline_module()
    report = {"baseline_sha": BASELINE_SHA, "fixture_root": str(root),
              "transport": "native Git local bare remote", "repetitions": REPETITIONS,
              "measured_surface": MEASURED_SURFACE,
              "timing_conclusion": "INCONCLUSIVE: local fixtures do not establish production latency",
              "samples": []}
    for count in (0, 32, 128):
        remote = population(root / f"n{count}", count)
        for warm in (False, True):
            for repetition in range(REPETITIONS):
                repo, main, proposed = reader(root / f"baseline-{count}-{warm}-{repetition}", remote, warm=warm)
                sample = dict(count=count, warm=warm, repetition=repetition,
                              baseline=measure(old, repo, main, proposed))
                report["samples"].append(sample)
                path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                print(json.dumps({"phase": "baseline", "count": count, "warm": warm,
                                  "repetition": repetition, **{k: v for k, v in sample["baseline"].items() if k != "outcome"}}), flush=True)


def compare(path: Path) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["baseline_sha"] == BASELINE_SHA and report["repetitions"] >= REPETITIONS
    for count in (0, 32, 128):
        for warm in (False, True):
            repetitions = [sample["repetition"] for sample in report["samples"]
                           if sample["count"] == count and sample["warm"] == warm]
            assert len(repetitions) >= REPETITIONS and len(repetitions) == len(set(repetitions))
    report["measured_surface"] = MEASURED_SURFACE
    root = Path(report["fixture_root"])
    comparison_root = Path(tempfile.mkdtemp(prefix="candidate-", dir=root))
    for sample in report["samples"]:
        count, warm, repetition = (sample[key] for key in ("count", "warm", "repetition"))
        repo, main, proposed = reader(comparison_root / f"{count}-{warm}-{repetition}", root / f"n{count}" / "remote.git", warm=warm)
        sample["candidate"] = measure(candidate, repo, main, proposed)
        sample["identical_outcomes"] = sample["baseline"]["outcome"] == sample["candidate"]["outcome"]
        assert sample["identical_outcomes"]
        if warm and count in (32, 128):
            assert sample["candidate"]["remote_total"] <= sample["baseline"]["remote_total"] * .25
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"phase": "candidate", "count": count, "warm": warm,
                          "repetition": repetition, **{k: v for k, v in sample["candidate"].items() if k != "outcome"}}), flush=True)


@pytest.mark.parametrize("count", [0, 32, 128])
@pytest.mark.parametrize("warm", [False, True])
def test_paired_native_git_remote_subprocess_measurements(tmp_path, count, warm):
    """Five independent matched pairs, with setup excluded from both timers."""
    remote = population(tmp_path / "population", count)
    old = baseline_module()
    samples = []
    for repetition in range(REPETITIONS):
        pair = {}
        for name, module in (("baseline", old), ("candidate", candidate)):
            repo, main, proposed = reader(tmp_path / f"{name}-{repetition}", remote, warm=warm)
            pair[name] = measure(module, repo, main, proposed)
        assert pair["baseline"]["outcome"] == pair["candidate"]["outcome"]
        assert len(pair["candidate"]["outcome"]["decisions"]) == count
        assert len(pair["candidate"]["outcome"]["sources"]) == count
        for measured in pair.values():
            assert measured["remote_total"] == sum(measured["remote_calls"].values())
            assert measured["remote_calls"]["push"] == 0
            assert measured["elapsed_seconds"] > 0
        if warm and count:
            assert pair["candidate"]["remote_total"] <= pair["baseline"]["remote_total"] * .25
            assert pair["candidate"]["remote_calls"]["fetch"] == 0
        samples.append({"repetition": repetition, "identical_outcomes": True, **pair})
    print(json.dumps({"baseline_sha": BASELINE_SHA, "count": count, "warm": warm,
                      "transport": "native Git local bare remote", "samples": samples,
                      "measured_surface": MEASURED_SURFACE,
                      "timing_conclusion": "INCONCLUSIVE: no production wall-time inference"}))


@pytest.mark.parametrize("count", [0, 1, 32, 128, candidate._MAX_DECISIONS])
@pytest.mark.parametrize("warm", [False, True])
def test_complete_native_snapshot_source_binding_and_bounds(tmp_path, monkeypatch, count, warm):
    remote = population(tmp_path / "population", count)
    repo, main, proposed = reader(tmp_path / "reader", remote, warm=warm)
    observed = []
    actual_git = candidate._git

    def record(path, *args, **kwargs):
        result = actual_git(path, *args, **kwargs)
        if args[0] in REMOTE_CATEGORIES:
            command = subprocess.list2cmdline(("git", "-C", str(path), *args))
            assert len(command.encode("utf-16-le")) // 2 <= candidate._MAX_REMOTE_ARG_CHARS
            if args[0] == "ls-remote":
                assert len(result[1].encode()) <= candidate._MAX_REMOTE_BYTES
            observed.append(args)
        return result

    monkeypatch.setattr(candidate, "_git", record)
    actual = measure(candidate, repo, main, proposed)
    expected = dict(line.split(" ", 1) for line in git(
        remote, "for-each-ref", "--format=%(refname) %(objectname)", DECISION,
    ).splitlines())
    assert actual["outcome"]["decisions"] == expected
    expected_sources = dict(line.split(" ", 1) for line in git(
        remote, "for-each-ref", "--format=%(refname) %(objectname)", SOURCE,
    ).splitlines())
    assert actual["outcome"]["sources"] == expected_sources
    assert sum(args[-1] == DECISION + "*" for args in observed) == 2
    assert observed[-1] == ("ls-remote", "--refs", "origin", "refs/heads/main")
    if warm:
        assert actual["remote_calls"]["fetch"] == 0
    assert git(remote, "rev-parse", "refs/heads/main") == main


@pytest.mark.parametrize("warm", [False, True])
def test_native_over_capacity_never_fetches_or_silently_truncates(tmp_path, monkeypatch, warm):
    remote = population(tmp_path / "population", candidate._MAX_DECISIONS + 1)
    repo, main, proposed = reader(tmp_path / "reader", remote, warm=warm)
    actual_git = candidate._git

    def no_fetch(path, *args, **kwargs):
        assert args[0] not in {"fetch", "push"}
        return actual_git(path, *args, **kwargs)

    monkeypatch.setattr(candidate, "_git", no_fetch)
    with pytest.raises(candidate.PublicationError, match="over capacity"):
        candidate._pending_review_guard(repo, "origin", run_id="TASK-398",
                                        reviewed_sha=proposed, main_sha=main)
    assert git(remote, "rev-parse", "refs/heads/main") == main


@pytest.mark.parametrize("fault", ["duplicate", "bad_sha", "foreign_ref", "invalid_run", "truncated", "blank", "unavailable", "oversized"])
def test_native_decision_observation_rejects_partial_or_ambiguous_data(tmp_path, monkeypatch, fault):
    remote = population(tmp_path / "population", 2)
    repo, main, proposed = reader(tmp_path / "reader", remote, warm=True)
    actual_git = candidate._git

    def damage(path, *args, **kwargs):
        code, output, error = actual_git(path, *args, **kwargs)
        if args[0] == "ls-remote" and args[-1] == DECISION + "*":
            if fault == "duplicate":
                output += output.splitlines()[0] + "\n"
            elif fault == "bad_sha":
                output = "z" + output[1:]
            elif fault == "foreign_ref":
                output = output.replace(DECISION, "refs/heads/other/")
            elif fault == "invalid_run":
                output = output.replace("RUN-", "TASK-")
            elif fault == "truncated":
                output = output.rstrip("\n")
            elif fault == "blank":
                output += "\n"
            elif fault == "unavailable":
                code = 128
            else:
                output = "x" * (candidate._MAX_REMOTE_BYTES + 1)
        return code, output, error

    monkeypatch.setattr(candidate, "_git", damage)
    with pytest.raises(candidate.PublicationError):
        candidate._pending_review_guard(repo, "origin", run_id="TASK-398", reviewed_sha=proposed, main_sha=main)
    assert git(remote, "rev-parse", "refs/heads/main") == main


@pytest.mark.parametrize("fault", ["missing", "duplicate", "truncated", "unexpected", "unavailable"])
def test_native_exact_source_batch_rejects_incomplete_observation(tmp_path, monkeypatch, fault):
    remote = population(tmp_path / "population", 3)
    repo, main, proposed = reader(tmp_path / "reader", remote, warm=True)
    actual_git = candidate._git

    def damage(path, *args, **kwargs):
        code, output, error = actual_git(path, *args, **kwargs)
        if args[0] == "ls-remote" and any(ref.startswith(SOURCE) for ref in args[3:]):
            lines = output.splitlines(keepends=True)
            if fault == "missing":
                output = "".join(lines[:-1])
            elif fault == "duplicate":
                output += lines[0]
            elif fault == "truncated":
                output = output[:-1]
            elif fault == "unexpected":
                output = output.replace(SOURCE, "refs/heads/unexpected/")
            else:
                code = 128
        return code, output, error

    monkeypatch.setattr(candidate, "_git", damage)
    with pytest.raises(candidate.PublicationError):
        candidate._pending_review_guard(repo, "origin", run_id="TASK-398", reviewed_sha=proposed, main_sha=main)
    assert git(remote, "rev-parse", "refs/heads/main") == main


@pytest.mark.parametrize("namespace", [DECISION, SOURCE])
@pytest.mark.parametrize("change", ["new", "deleted", "moved", "swapped"])
def test_warm_fresh_recheck_detects_every_material_ref_change(tmp_path, namespace, change):
    remote = population(tmp_path / "population", 2)
    repo, main, proposed = reader(tmp_path / "reader", remote, warm=True)
    decisions, binding = candidate._pending_review_guard(repo, "origin", run_id="TASK-398",
                                                        reviewed_sha=proposed, main_sha=main)
    ref, other = namespace + "RUN-900-0000", namespace + "RUN-900-0001"
    old, second = git(remote, "rev-parse", ref), git(remote, "rev-parse", other)
    # A warm local ref deliberately remains stale and cannot be live authority.
    git(repo, "update-ref", ref, old)
    if change == "new":
        if namespace == SOURCE:
            # A new material source is introduced with its new decision.
            git(remote, "update-ref", DECISION + "RUN-900-new", next(iter(decisions.values())))
        git(remote, "update-ref", namespace + "RUN-900-new", old)
    elif change == "deleted":
        git(remote, "update-ref", "-d", ref)
    else:
        git(remote, "update-ref", ref, second, old)
        if change == "swapped":
            git(remote, "update-ref", other, old, second)
    with pytest.raises(candidate.PublicationError) as raised:
        candidate._recheck_binding(repo, "origin", binding, run_id="TASK-398",
                                  reviewed_sha=proposed, main_sha=main, decisions=decisions)
    assert raised.value.report.cause in {"DECISION_SET_CHANGED", "STALE_REVIEW_BINDING", "PUBLICATION_GATE_FAILED"}
    assert raised.value.report.prior_main_sha == main
    assert git(remote, "rev-parse", "refs/heads/main") == main


def test_batching_bounds_windows_arguments_with_long_exact_refs(tmp_path, monkeypatch):
    remote = population(tmp_path / "population", 128, padding=170)
    repo, main, proposed = reader(tmp_path / "reader", remote, warm=True)
    calls = []
    actual = candidate._git

    def record(path, *args, **kwargs):
        if args[0] in REMOTE_CATEGORIES:
            assert len(subprocess.list2cmdline(("git", "-C", str(path), *args)).encode("utf-16-le")) // 2 <= candidate._MAX_REMOTE_ARG_CHARS
            calls.append(args)
        return actual(path, *args, **kwargs)

    monkeypatch.setattr(candidate, "_git", record)
    result = measure(candidate, repo, main, proposed)
    assert len(result["outcome"]["sources"]) == 128
    assert sum(any(ref.startswith(SOURCE) for ref in args[3:]) for args in calls) > 4
    assert result["remote_calls"]["fetch"] == 0


@pytest.mark.parametrize("warm", [False, True])
def test_native_remote_response_ceiling_is_enforced_before_acceptance(tmp_path, monkeypatch, warm):
    remote = population(tmp_path / "population", 32)
    repo, _, _ = reader(tmp_path / "reader", remote, warm=warm)
    monkeypatch.setattr(candidate, "_MAX_REMOTE_BYTES", 128)
    with pytest.raises(candidate.PublicationError):
        candidate._decision_snapshot(repo, "origin", run_id="TASK-398")


@pytest.mark.parametrize("change", ["missing_source", "moved_source", "swapped_sources", "missing_review"])
def test_initial_snapshot_cannot_rebind_or_omit_historical_decisions(tmp_path, change):
    remote = population(tmp_path / "population", 2)
    refs = [SOURCE + f"RUN-900-{index:04}" for index in range(2)]
    first, second = (git(remote, "rev-parse", ref) for ref in refs)
    main = git(remote, "rev-parse", "refs/heads/main")
    if change == "missing_source":
        git(remote, "update-ref", "-d", refs[0])
    elif change == "moved_source":
        git(remote, "update-ref", refs[0], main, first)
    elif change == "swapped_sources":
        git(remote, "update-ref", refs[0], second, first)
        git(remote, "update-ref", refs[1], first, second)
    else:
        # A commit object with no REVIEW is still invalid decision content.
        git(remote, "update-ref", DECISION + "RUN-900-0000", main)
    repo, observed_main, proposed = reader(tmp_path / "reader", remote, warm=True)
    with pytest.raises(candidate.PublicationError):
        candidate._pending_review_guard(repo, "origin", run_id="TASK-398",
                                        reviewed_sha=proposed, main_sha=observed_main)
    assert git(remote, "rev-parse", "refs/heads/main") == main


@pytest.mark.parametrize("warm", [False, True])
def test_native_wrong_type_remote_decision_is_not_a_local_commit(tmp_path, warm):
    remote = population(tmp_path / "population", 1)
    blob = git(remote, "hash-object", "-w", "--stdin", data=b"invalid branch target\n")
    repo, main, proposed = reader(tmp_path / "reader", remote, warm=warm)
    if warm:
        assert git(repo, "hash-object", "-w", "--stdin", data=b"invalid branch target\n") == blob
    # Native Git refuses update-ref of a branch to a blob. This deliberately
    # malformed bare-remote fixture tests the actual ls-remote/fetch boundary.
    ref = remote / (DECISION + "RUN-900-0000")
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(blob + "\n", encoding="ascii")
    with pytest.raises(candidate.PublicationError):
        candidate._pending_review_guard(repo, "origin", run_id="TASK-398",
                                        reviewed_sha=proposed, main_sha=main)
    assert git(remote, "rev-parse", "refs/heads/main") == main


def test_missing_exact_commit_is_fetched_once_and_revalidated(tmp_path, monkeypatch):
    remote = population(tmp_path / "population", 1)
    repo, _, _ = reader(tmp_path / "reader", remote, warm=False)
    sha = git(remote, "rev-parse", DECISION + "RUN-900-0000")
    calls = []
    actual = candidate._git

    def record(path, *args, **kwargs):
        if args[0] == "fetch":
            calls.append(args)
        return actual(path, *args, **kwargs)

    monkeypatch.setattr(candidate, "_git", record)
    candidate._fetch_object(repo, "origin", sha, run_id="TASK-398")
    candidate._fetch_object(repo, "origin", sha, run_id="TASK-398")
    assert len(calls) == 1 and calls[0][-1] == sha
    assert git(repo, "cat-file", "-t", sha) == "commit"


@pytest.mark.parametrize("kind", ["blob", "tree"])
def test_wrong_type_local_object_never_avoids_commit_validation(tmp_path, monkeypatch, kind):
    remote = population(tmp_path / "population", 1)
    repo, main, _ = reader(tmp_path / "reader", remote, warm=True)
    sha = (git(repo, "hash-object", "-w", "--stdin", data=b"not a commit\n") if kind == "blob"
           else git(repo, "rev-parse", f"{main}^{{tree}}"))
    with pytest.raises(candidate.PublicationError, match="commit"):
        candidate._fetch_object(repo, "origin", sha, run_id="TASK-398")
    assert git(remote, "rev-parse", "refs/heads/main") == main


@pytest.mark.parametrize("corruption", ["valid_wrong_content", "truncated_zlib"])
def test_native_local_commit_integrity_is_not_just_cat_file_type(tmp_path, corruption):
    import zlib
    remote = population(tmp_path / "population", 1)
    repo, main, _ = reader(tmp_path / "reader", remote, warm=True)
    sha = git(remote, "rev-parse", DECISION + "RUN-900-0000")
    content = subprocess.run(("git", "-C", str(repo), "cat-file", "commit", sha),
                             capture_output=True, check=True).stdout
    loose = repo / ".git/objects" / sha[:2] / sha[2:]
    loose.parent.mkdir(parents=True, exist_ok=True)
    if corruption == "valid_wrong_content":
        damaged = content.replace(b"decision 0", b"decision X")
        assert damaged != content
        loose.write_bytes(zlib.compress(f"commit {len(damaged)}\0".encode() + damaged))
        assert git(repo, "cat-file", "-t", sha) == "commit"
    else:
        loose.write_bytes(b"\x78\x9c")
    with pytest.raises(candidate.PublicationError):
        candidate._fetch_object(repo, "origin", sha, run_id="TASK-398")
    assert git(remote, "rev-parse", "refs/heads/main") == main


def test_native_over_size_commit_never_authorizes_local_reuse(tmp_path):
    remote = population(tmp_path / "population", 1)
    repo, main, _ = reader(tmp_path / "reader", remote, warm=True)
    tree = git(repo, "rev-parse", f"{main}^{{tree}}")
    huge = git(repo, "commit-tree", tree, "-p", main, data=b"x" * (candidate._MAX_COMMIT_BYTES + 1))
    with pytest.raises(candidate.PublicationError):
        candidate._fetch_object(repo, "origin", huge, run_id="TASK-398")


def test_native_review_blob_response_is_bounded_even_for_small_commit(tmp_path):
    remote = population(tmp_path / "population", 1)
    source = git(remote, "rev-parse", SOURCE + "RUN-900-0000")
    review = (f"review_id: REVIEW-900-0000\nreviewed_sha: {source}\nmode: PRIMARY\n"
              "verdict: PASS\nacceptance: {AC1: PASS}\nfindings: []\n#").encode()
    review += b"x" * candidate._MAX_COMMIT_BYTES + b"\n"
    git(remote, "update-ref", "-d", DECISION + "RUN-900-0000")
    stream = b"blob\nmark :1\n" + _data(review)
    stream += (f"commit {DECISION}RUN-900-0000\n"
               "committer Guard Fixture <guard@example.invalid> 1700000000 +0000\n").encode()
    stream += _data(b"small commit, oversized review blob\n")
    stream += f"from {source}\nM 100644 :1 .ai/reviews/REVIEW-900-0000.yaml\n\n".encode()
    git(remote, "fast-import", "--quiet", data=stream)
    repo, main, proposed = reader(tmp_path / "reader", remote, warm=True)
    with pytest.raises(candidate.PublicationError):
        candidate._pending_review_guard(repo, "origin", run_id="TASK-398",
                                        reviewed_sha=proposed, main_sha=main)


@pytest.mark.parametrize("other_run", ["RUN-000-001", "RUN-999-001"])
@pytest.mark.parametrize("warm", [False, True])
def test_eligible_historical_pass_is_a_blocker_in_either_ref_order(tmp_path, other_run, warm):
    from tests.test_publication import make_lineage, add_sibling_pass
    primary = make_lineage(tmp_path / "full")
    other = add_sibling_pass(primary, run_id=other_run)
    repo, main, _ = reader(tmp_path / "reader", primary["remote"], warm=warm)
    with pytest.raises(candidate.PublicationError) as raised:
        candidate._pending_review_guard(repo, "origin", run_id=primary["run_id"],
                                        reviewed_sha=primary["candidate_sha"], main_sha=main)
    report = raised.value.report
    assert report.outcome == "RECOVERY_BLOCKED" and report.cause == "COMPETING_REVIEWED_SOURCE"
    assert report.blocker.source_run == other_run
    assert report.blocker.decision_sha == other["decision_sha"]
    assert report.blocker.reviewed_sha == other["candidate_sha"]
    assert git(primary["remote"], "rev-parse", "refs/heads/main") == main


def test_local_replacement_ref_cannot_hide_an_exact_competing_pass(tmp_path):
    from tests.test_publication import make_lineage, add_sibling_pass
    primary = make_lineage(tmp_path / "full")
    other = add_sibling_pass(primary)
    repo, main, _ = reader(tmp_path / "reader", primary["remote"], warm=True)
    git(repo, "replace", other["candidate_sha"], main)
    with pytest.raises(candidate.PublicationError) as raised:
        candidate._pending_review_guard(repo, "origin", run_id=primary["run_id"],
                                        reviewed_sha=primary["candidate_sha"], main_sha=main)
    assert raised.value.report.cause == "COMPETING_REVIEWED_SOURCE"
    assert raised.value.report.blocker.reviewed_sha == other["candidate_sha"]
    assert git(primary["remote"], "rev-parse", "refs/heads/main") == main


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-baseline", type=Path)
    parser.add_argument("--compare", type=Path)
    arguments = parser.parse_args()
    if arguments.capture_baseline:
        capture_baseline(arguments.capture_baseline)
    elif arguments.compare:
        compare(arguments.compare)
    else:
        parser.error("choose --capture-baseline PATH or --compare PATH")
