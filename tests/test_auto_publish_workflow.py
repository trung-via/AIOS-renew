"""Execute the hosted Bash/Git contract against local fixture remotes only."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from aios_renew import operator, publication
from tests.test_publication import (
    TASK_SOURCE,
    add_sibling_pass,
    advance_divergent_main,
    git,
    make_lineage,
    publication_workflow,
    remote_main,
)


class RecoveryEligibilityReached(Exception):
    """Stop before Runtime can admit a RUN, execute proof or publish a source."""


@pytest.fixture
def eligibility_boundary(monkeypatch):
    prepared = []
    real_prepare = publication.prepare_publication_recovery

    def stop_after_eligibility(*args, **kwargs):
        plan, *_ = real_prepare(*args, **kwargs)
        prepared.append(plan)
        raise RecoveryEligibilityReached

    # A temporary checkout is not the installed canonical Runtime. Keep every
    # source/currentness/competition gate real, stopping before any admission.
    monkeypatch.setattr(operator, "_require_publication_recovery_activation", lambda *_: None)
    monkeypatch.setattr(operator, "_persist_and_transport_admission_failure", lambda *_, **__: None)
    monkeypatch.setattr(publication, "prepare_publication_recovery", stop_after_eligibility)
    return prepared


@pytest.fixture
def bash():
    if os.name == "nt":
        git_root = Path(shutil.which("git")).resolve().parent.parent
        executable = git_root / "bin" / "bash.exe"
        if executable.is_file():
            return str(executable)
    executable = shutil.which("bash")
    if executable is None:
        pytest.skip("The hosted workflow contract requires Bash")
    return executable


def checkout_main(root, lineage):
    repo = root / "hosted-checkout"
    subprocess.run(("git", "clone", "--quiet", "--branch", "main", str(lineage["remote"]), str(repo)),
                   check=True, capture_output=True, text=True)
    git(repo, "config", "user.name", "Workflow Fixture")
    git(repo, "config", "user.email", "workflow@example.invalid")
    return repo


def workflow_step(step_id):
    parsed, _ = publication_workflow()
    return next(step for step in parsed["jobs"]["publish"]["steps"] if step.get("id") == step_id)


def run_attempt(bash, repo, root, lineage, *, event="push", script=None):
    attempt = root / "attempt"
    attempt.mkdir(exist_ok=True)
    invocation = attempt / "invocation"
    invocation.unlink(missing_ok=True)
    outputs = attempt / "outputs"
    outputs.write_text("", encoding="utf-8")
    report = attempt / "report.json"
    env = dict(os.environ, AIOS_EVENT_NAME=event,
               AIOS_DECISION_REF="refs/heads/aios/review-decision/" + lineage["run_id"],
               AIOS_DECISION_SHA=lineage["decision_sha"], AIOS_REPLAY_RUN_ID=lineage["run_id"],
               AIOS_REPOSITORY_URL=str(lineage["remote"]),
               AIOS_TEST_INVOCATION=invocation.as_posix(), GITHUB_OUTPUT=outputs.as_posix(),
               AIOS_PUBLICATION_REPORT=report.as_posix())
    # Capture the exact CLI boundary instead of externally invoking publication.
    # Tests then call the existing Python API against this local bare remote.
    capture = 'python() { printf "%s\\0" "$@" > "$AIOS_TEST_INVOCATION"; }\n'
    process = subprocess.run((bash, "--noprofile", "--norc", "-c",
                              capture + (workflow_step("publication")["run"] if script is None else script)),
                             cwd=repo, env=env, capture_output=True, text=True)
    arguments = invocation.read_bytes().decode().rstrip("\0").split("\0") if invocation.exists() else None
    return process, arguments, outputs.read_text(encoding="utf-8")


def continue_attempt(repo, arguments):
    assert arguments[:2] == ["-m", "aios_renew.publication"]
    selectors = publication._parser().parse_args(arguments[2:])
    assert selectors.repo == "." and selectors.remote == "origin" and selectors.recover_clean
    return publication.continue_publication(repo, run_id=selectors.run_id,
        decision_sha=selectors.decision_sha, control_sha=selectors.control_sha, remote=selectors.remote)


def commit_main_file(lineage, path, contents):
    repo = lineage["repo"]
    git(repo, "checkout", "--quiet", "--detach", remote_main(lineage))
    target = repo / path
    if contents is None:
        target.unlink()
    else:
        target.write_text(contents, encoding="utf-8")
    git(repo, "add", path)
    git(repo, "commit", "--quiet", "-m", "advance fixture canonical main")
    git(repo, "push", "--quiet", "origin", "HEAD:refs/heads/main")
    return remote_main(lineage)


def assert_no_recovery_publication(lineage, refs):
    assert git(lineage["remote"], "show-ref") == refs
    assert git(lineage["remote"], "for-each-ref", "--format=%(refname)",
               "refs/heads/aios/publication-recovery/", "refs/heads/aios/publication-source/") == ""


def test_exact_detached_fetch_checkout_reproduces_runtime_upstream_failure(
    tmp_path, bash, eligibility_boundary,
):
    lineage = make_lineage(tmp_path)
    main = advance_divergent_main(lineage)
    repo = checkout_main(tmp_path, lineage)
    # The production failure shape: FETCH_HEAD is correct, but detach discards
    # the current branch's upstream authority despite origin/main still existing.
    old_script = '''set -euo pipefail
git fetch --no-tags origin refs/heads/main
control_sha="$(git rev-parse FETCH_HEAD)"
git checkout --detach "$control_sha"
python -m aios_renew.publication --repo . --remote origin \\
  --run-id "$AIOS_REPLAY_RUN_ID" --decision-sha "$AIOS_DECISION_SHA" \\
  --control-sha "$control_sha" --recover-clean
'''
    refs = git(lineage["remote"], "show-ref")
    process, arguments, _ = run_attempt(bash, repo, tmp_path, lineage, script=old_script)
    assert process.returncode == 0, process.stderr
    assert git(repo, "rev-parse", "HEAD") == main
    assert git(repo, "branch", "--show-current") == ""
    assert git(repo, "config", "branch.main.remote") == "origin"
    with pytest.raises(publication.PublicationError, match=(
        "Runtime publication continuation failed: no configured upstream Git remote for current branch"
    )) as raised:
        continue_attempt(repo, arguments)
    assert raised.value.report.prior_main_sha == main
    assert eligibility_boundary == []
    assert_no_recovery_publication(lineage, refs)


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
@pytest.mark.parametrize("checkout_timing", ["current", "behind"])
def test_attached_runner_checkout_reaches_existing_recovery_eligibility(
    tmp_path, bash, eligibility_boundary, event, checkout_timing,
):
    lineage = make_lineage(tmp_path)
    if checkout_timing == "behind":
        repo = checkout_main(tmp_path, lineage)
    main = advance_divergent_main(lineage)
    if checkout_timing == "current":
        repo = checkout_main(tmp_path, lineage)
    refs = git(lineage["remote"], "show-ref")
    process, arguments, outputs = run_attempt(bash, repo, tmp_path, lineage, event=event)
    assert process.returncode == 0, process.stderr
    assert git(repo, "symbolic-ref", "HEAD") == "refs/heads/main"
    assert git(repo, "config", "--get-all", "branch.main.remote") == "origin"
    assert git(repo, "config", "--get-all", "branch.main.merge") == "refs/heads/main"
    assert git(repo, "rev-parse", "@{upstream}") == git(repo, "rev-parse", "HEAD") == main
    assert git(repo, "status", "--porcelain") == ""
    assert arguments[arguments.index("--control-sha") + 1] == main
    assert outputs == f"run_id={lineage['run_id']}\ndecision_sha={lineage['decision_sha']}\n"
    with pytest.raises(RecoveryEligibilityReached):
        continue_attempt(repo, arguments)
    plan, = eligibility_boundary
    assert (plan.main_sha, plan.reviewed_sha, plan.decision_sha) == (
        main, lineage["candidate_sha"], lineage["decision_sha"])
    assert not list(operator.runtime_paths(repo).runs.glob("*.json"))
    assert_no_recovery_publication(lineage, refs)


@pytest.mark.parametrize("mutation,message", [
    ("detached", "attached to canonical main"),
    ("missing-upstream", "exactly one upstream"),
    ("duplicate-remote", "exactly one upstream"),
    ("duplicate-merge", "exactly one upstream"),
    ("wrong-remote", "exactly one upstream"),
    ("wrong-merge", "exactly one upstream"),
    ("wrong-tracking-map", "exactly one upstream"),
    ("substituted-origin", "canonical repository fetch/push URL"),
    ("split-push-url", "canonical repository fetch/push URL"),
    ("duplicate-url", "canonical repository fetch/push URL"),
    ("dirty", "clean main checkout"),
    ("local-ahead", "exact fetched control SHA"),
    ("divergent-local", "fast-forward"),
    ("missing-remote-main", "remote ref refs/heads/main"),
])
def test_invalid_runner_context_blocks_without_reset_or_alternative_remote(
    tmp_path, bash, mutation, message,
):
    lineage = make_lineage(tmp_path)
    advance_divergent_main(lineage)
    repo = checkout_main(tmp_path, lineage)
    if mutation == "detached":
        git(repo, "checkout", "--quiet", "--detach")
    elif mutation == "missing-upstream":
        git(repo, "config", "--unset-all", "branch.main.remote")
    elif mutation.startswith("duplicate-") and mutation != "duplicate-url":
        key, value = ("remote", "origin") if mutation == "duplicate-remote" else ("merge", "refs/heads/main")
        git(repo, "config", "--add", "branch.main." + key, value)
    elif mutation == "wrong-remote":
        git(repo, "remote", "add", "other", str(lineage["remote"]))
        git(repo, "config", "branch.main.remote", "other")
    elif mutation == "wrong-merge":
        git(repo, "config", "branch.main.merge", "refs/heads/other")
    elif mutation == "wrong-tracking-map":
        git(repo, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/other/*")
    elif mutation in {"substituted-origin", "split-push-url", "duplicate-url"}:
        substitute = tmp_path / "substituted.git"
        subprocess.run(("git", "clone", "--quiet", "--bare", str(lineage["remote"]), str(substitute)),
                       check=True, capture_output=True)
        if mutation == "substituted-origin":
            git(repo, "remote", "set-url", "origin", str(substitute))
        elif mutation == "split-push-url":
            git(repo, "remote", "set-url", "--push", "origin", str(substitute))
        else:
            git(repo, "config", "--add", "remote.origin.url", str(substitute))
    elif mutation == "dirty":
        (repo / "dirty.txt").write_text("untracked material\n", encoding="utf-8")
    elif mutation in {"local-ahead", "divergent-local"}:
        (repo / "local.txt").write_text("local material\n", encoding="utf-8")
        git(repo, "add", "local.txt")
        git(repo, "commit", "--quiet", "-m", "local control commit")
        if mutation == "divergent-local":
            commit_main_file(lineage, "remote.txt", "remote material\n")
    elif mutation == "missing-remote-main":
        git(lineage["remote"], "update-ref", "-d", "refs/heads/main")
    head = git(repo, "rev-parse", "HEAD")
    refs = git(lineage["remote"], "show-ref")
    process, arguments, outputs = run_attempt(bash, repo, tmp_path, lineage)
    assert process.returncode != 0
    assert message in process.stderr
    assert arguments is None
    assert git(repo, "rev-parse", "HEAD") == head
    assert f"decision_sha={lineage['decision_sha']}" in outputs
    assert_no_recovery_publication(lineage, refs)


@pytest.mark.parametrize("moved_ref,cause", [
    ("main", "STALE_CONTROL_BINDING"), ("decision", "STALE_REVIEW_BINDING"),
])
def test_runtime_rechecks_live_exact_selectors_after_workflow_snapshot(
    tmp_path, bash, eligibility_boundary, moved_ref, cause,
):
    lineage = make_lineage(tmp_path)
    advance_divergent_main(lineage)
    repo = checkout_main(tmp_path, lineage)
    process, arguments, _ = run_attempt(bash, repo, tmp_path, lineage)
    assert process.returncode == 0, process.stderr
    if moved_ref == "main":
        commit_main_file(lineage, "newer.txt", "canonical main moved after snapshot\n")
    else:
        git(lineage["remote"], "update-ref", "refs/heads/aios/review-decision/" + lineage["run_id"],
            lineage["base_sha"])
    refs = git(lineage["remote"], "show-ref")
    with pytest.raises(publication.PublicationError) as raised:
        continue_attempt(repo, arguments)
    assert raised.value.report.cause == cause
    assert "upstream" not in raised.value.report.detail
    assert eligibility_boundary == []
    assert_no_recovery_publication(lineage, refs)


@pytest.mark.parametrize("mutation,cause", [
    ("changed-task", "RECOVERY_TASK_CHANGED"),
    ("missing-task", "RECOVERY_TASK_CHANGED"),
    ("overlap", "MERGE_CONFLICT"),
])
def test_attached_context_keeps_existing_task_and_overlap_blockers(
    tmp_path, bash, eligibility_boundary, mutation, cause,
):
    lineage = make_lineage(tmp_path)
    advance_divergent_main(lineage)
    path = "product.txt" if mutation == "overlap" else ".ai/tasks/TASK-063.yaml"
    contents = ("overlapping main material\n" if mutation == "overlap" else
                None if mutation == "missing-task" else TASK_SOURCE.replace("revision: 2", "revision: 3"))
    commit_main_file(lineage, path, contents)
    repo = checkout_main(tmp_path, lineage)
    refs = git(lineage["remote"], "show-ref")
    process, arguments, _ = run_attempt(bash, repo, tmp_path, lineage)
    assert process.returncode == 0, process.stderr
    with pytest.raises(publication.PublicationError) as raised:
        continue_attempt(repo, arguments)
    assert raised.value.report.cause == cause
    assert raised.value.report.reviewed_sha == lineage["candidate_sha"]
    assert "upstream" not in raised.value.report.detail
    assert eligibility_boundary == []
    assert_no_recovery_publication(lineage, refs)


@pytest.mark.parametrize("diverged", [False, True], ids=["pending-pass", "divergent-pass"])
@pytest.mark.parametrize("selected", ["first", "sibling"])
def test_attached_workflow_cannot_choose_or_strand_a_competing_pass(
    tmp_path, bash, eligibility_boundary, diverged, selected,
):
    first = make_lineage(tmp_path)
    sibling = add_sibling_pass(first)
    if diverged:
        advance_divergent_main(first)
    lineage, other = (first, sibling) if selected == "first" else (sibling, first)
    repo = checkout_main(tmp_path, lineage)
    refs = git(lineage["remote"], "show-ref")
    process, arguments, _ = run_attempt(bash, repo, tmp_path, lineage)
    assert process.returncode == 0, process.stderr
    with pytest.raises(publication.PublicationError) as raised:
        continue_attempt(repo, arguments)
    report = raised.value.report
    assert report.cause == "COMPETING_REVIEWED_SOURCE"
    assert report.classification == "COMPETING_SOURCE"
    assert report.blocker.source_run == other["run_id"]
    assert eligibility_boundary == []
    assert_no_recovery_publication(lineage, refs)


def test_exact_local_publication_replay_is_idempotent_and_ancestor_inclusion_is_distinct(
    tmp_path, bash, eligibility_boundary,
):
    lineage = make_lineage(tmp_path)
    repo = checkout_main(tmp_path, lineage)
    for event, expected in (("push", "PUBLISHED"), ("workflow_dispatch", "ALREADY_PUBLISHED")):
        refs = git(lineage["remote"], "show-ref")
        process, arguments, _ = run_attempt(bash, repo, tmp_path, lineage, event=event)
        assert process.returncode == 0, process.stderr
        report = continue_attempt(repo, arguments)
        assert report.outcome == expected
        assert report.reviewed_sha == remote_main(lineage) == lineage["candidate_sha"]
        if expected == "ALREADY_PUBLISHED":
            assert git(lineage["remote"], "show-ref") == refs
    commit_main_file(lineage, "newer.txt", "unrelated later main\n")
    refs = git(lineage["remote"], "show-ref")
    process, arguments, _ = run_attempt(bash, repo, tmp_path, lineage, event="workflow_dispatch")
    assert process.returncode == 0, process.stderr
    assert continue_attempt(repo, arguments).outcome == "ALREADY_INCLUDED"
    assert eligibility_boundary == []
    assert_no_recovery_publication(lineage, refs)


@pytest.mark.parametrize("outcome", ["PUBLISHED", "ALREADY_PUBLISHED", "AWAITING_REVIEW", "RECOVERY_BLOCKED", "FAILED"])
def test_hosted_status_preserves_runtime_outcome_with_separate_async_delivery(tmp_path, monkeypatch, outcome):
    parsed, _ = publication_workflow()
    assert set(parsed["jobs"]) == {"publish"}
    assert "continue-on-error" not in workflow_step("publication")
    report, summary = tmp_path / "report.json", tmp_path / "summary.md"
    report.write_text(json.dumps({"outcome": outcome}), encoding="utf-8")
    environment = dict(AIOS_PUBLICATION_REPORT=str(report), GITHUB_STEP_SUMMARY=str(summary),
        AIOS_PUBLICATION_STEP_OUTCOME="failure" if outcome in {"FAILED", "RECOVERY_BLOCKED"} else "success",
        AIOS_CHAT_WAKE_ENABLED="true", AIOS_REPOSITORY="trung-via/AIOS-renew", AIOS_SERVER_URL="https://github.com")
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    script = workflow_step("publication-status")["run"]
    code = script.removeprefix("python - <<'PY'\n").removesuffix("PY\n")
    exec(compile(code, "<workflow-publication-summary>", "exec"), {})
    text = summary.read_text(encoding="utf-8")
    assert f"Runtime publication outcome: **{outcome}**" in text
    assert "Local chat wake: asynchronous" in text
    assert "/actions/workflows/aios-local-chat-wake.yml" in text
    assert "Delivery status does not change the Publisher outcome" in text
    if outcome == "AWAITING_REVIEW":
        assert "PUBLISHED" not in text
