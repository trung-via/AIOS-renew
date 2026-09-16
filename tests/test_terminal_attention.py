from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from aios_renew.terminal_attention import (
    BODY_FORMAT,
    SIGNAL_PREFIX,
    TerminalAttentionError,
    admit_event,
    load_policy,
    parse_body,
    parse_signal_ref,
    publish_terminal_attention,
    selector,
    validate_remote_admission,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / ".ai/brain-terminal-attention-carriers.yaml"
PINNED_AIOS_RENEW_SHA = "26097405343150dc1b55015b94720528afad50ed"
PINNED_PROVENANCE_PATH = (
    ".agents/skills/aios-worker/requirements-aios-renew.txt"
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def make_repo(
    tmp_path: Path,
    *,
    reviewed_surface: bool = True,
    exact_pin: str | None = None,
    workflow_uses_pin: bool = True,
) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    remote = tmp_path / "upstream.git"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "config", "user.name", "Attention Test")
    git(repo, "config", "user.email", "attention@example.invalid")
    git(repo, "branch", "-M", "main")
    (repo / "subject.txt").write_text("main\n", encoding="utf-8")
    if reviewed_surface and exact_pin is not None:
        raise ValueError("test fixture cannot use local source and an exact pin together")
    if reviewed_surface:
        for relative in (
            ".github/workflows/aios-terminal-attention.yml",
            ".ai/brain-terminal-attention-carriers.yaml",
            "src/aios_renew/terminal_attention.py",
        ):
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"reviewed: {relative}\n", encoding="utf-8")
    elif exact_pin is not None:
        workflow = repo / ".github/workflows/aios-terminal-attention.yml"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text(
            "run: |\n  python -m pip install --no-input "
            f"-r {PINNED_PROVENANCE_PATH if workflow_uses_pin else 'requirements.txt'}\n",
            encoding="utf-8",
        )
        policy = repo / ".ai/brain-terminal-attention-carriers.yaml"
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text("reviewed: downstream policy\n", encoding="utf-8")
        provenance = repo / PINNED_PROVENANCE_PATH
        provenance.parent.mkdir(parents=True, exist_ok=True)
        provenance.write_text(
            "aios-renew @ git+https://github.com/trung-via/AIOS-renew.git@"
            f"{exact_pin}\n",
            encoding="utf-8",
        )
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "published main")
    main_sha = git(repo, "rev-parse", "HEAD")
    subprocess.run(("git", "init", "--bare", "--quiet", str(remote)), check=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "--quiet", "--set-upstream", "origin", "main")
    return repo, remote, main_sha


def publish_terminal(repo: Path, item) -> None:
    git(
        repo,
        "push",
        "--quiet",
        "origin",
        f"{item.artifact_sha}:{item.terminal_ref}",
    )


def artifact_selector(repo: Path, *, kind: str = "RESULT"):
    empty_tree = git(repo, "mktree")
    artifact = git(repo, "commit-tree", empty_tree, "-m", "terminal artifact")
    return selector("RUN-113-001", kind, artifact)


def write_policy(tmp_path: Path, repository: str) -> Path:
    document = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    document["github_issue"]["repository"] = repository
    path = tmp_path / "attention-policy.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def test_selector_ref_and_body_are_exact_and_strict() -> None:
    item = selector("RUN-113-001", "RESULT", "a" * 40)

    assert item.signal_ref == (
        f"{SIGNAL_PREFIX}/RESULT/RUN-113-001/{'a' * 40}"
    )
    assert parse_signal_ref(item.signal_ref) == item
    assert parse_body(item.body()) == item
    assert item.body() == (
        f"format: {BODY_FORMAT}\nversion: 1\nrun_id: RUN-113-001\n"
        f"terminal_kind: RESULT\nartifact_sha: {'a' * 40}\n"
    )
    with pytest.raises(TerminalAttentionError):
        parse_body(item.body() + "verdict: PASS\n")
    with pytest.raises(TerminalAttentionError):
        parse_signal_ref(item.signal_ref.replace("RESULT", "REVIEW"))


def test_publish_creates_one_main_pointing_ref_and_exact_replay_is_inert(
    tmp_path: Path,
) -> None:
    repo, _, main_sha = make_repo(tmp_path)
    item = artifact_selector(repo)
    publish_terminal(repo, item)

    assert publish_terminal_attention(
        repo,
        remote="origin",
        run_id=item.run_id,
        terminal_kind=item.terminal_kind,
        artifact_sha=item.artifact_sha,
    ) == "PUBLISHED"
    first = git(repo, "ls-remote", "--refs", "origin", item.signal_ref)
    assert first == f"{main_sha}\t{item.signal_ref}"
    assert publish_terminal_attention(
        repo,
        remote="origin",
        run_id=item.run_id,
        terminal_kind=item.terminal_kind,
        artifact_sha=item.artifact_sha,
    ) == "REUSED"
    assert git(repo, "ls-remote", "--refs", "origin", item.terminal_ref).startswith(
        item.artifact_sha
    )


def test_publish_fails_closed_for_competing_terminal_or_attention(
    tmp_path: Path,
) -> None:
    repo, remote, main_sha = make_repo(tmp_path)
    item = artifact_selector(repo)
    publish_terminal(repo, item)
    conflicting = selector(item.run_id, "FAILURE", "b" * 40)
    git(remote, "update-ref", item.opposite_ref, item.artifact_sha)
    git(remote, "update-ref", conflicting.signal_ref, main_sha)

    with pytest.raises(TerminalAttentionError, match="competing"):
        publish_terminal_attention(
            repo,
            remote="origin",
            run_id=item.run_id,
            terminal_kind=item.terminal_kind,
            artifact_sha=item.artifact_sha,
        )


def test_publish_rejects_same_run_rebound_to_another_artifact(
    tmp_path: Path,
) -> None:
    repo, remote, main_sha = make_repo(tmp_path)
    item = artifact_selector(repo)
    publish_terminal(repo, item)
    conflicting = selector(item.run_id, item.terminal_kind, "b" * 40)
    git(remote, "update-ref", conflicting.signal_ref, main_sha)

    with pytest.raises(TerminalAttentionError, match="conflicting attention"):
        publish_terminal_attention(
            repo,
            remote="origin",
            run_id=item.run_id,
            terminal_kind=item.terminal_kind,
            artifact_sha=item.artifact_sha,
        )


def test_pre_carrier_main_is_compatible_and_emits_no_privileged_signal(
    tmp_path: Path,
) -> None:
    repo, _, _ = make_repo(tmp_path, reviewed_surface=False)
    item = artifact_selector(repo)
    publish_terminal(repo, item)

    assert publish_terminal_attention(
        repo,
        remote="origin",
        run_id=item.run_id,
        terminal_kind=item.terminal_kind,
        artifact_sha=item.artifact_sha,
    ) == "UNAVAILABLE"
    assert git(repo, "ls-remote", "--refs", "origin", f"{SIGNAL_PREFIX}/*") == ""


@pytest.mark.parametrize("terminal_kind", ["RESULT", "FAILURE"])
def test_exact_pin_downstream_without_vendored_source_publishes_and_reuses_signal(
    tmp_path: Path, terminal_kind: str
) -> None:
    repo, _, main_sha = make_repo(
        tmp_path, reviewed_surface=False, exact_pin=PINNED_AIOS_RENEW_SHA
    )
    assert not (repo / "src/aios_renew/terminal_attention.py").exists()
    item = artifact_selector(repo, kind=terminal_kind)
    publish_terminal(repo, item)

    assert publish_terminal_attention(
        repo,
        remote="origin",
        run_id=item.run_id,
        terminal_kind=item.terminal_kind,
        artifact_sha=item.artifact_sha,
    ) == "PUBLISHED"
    assert git(repo, "ls-remote", "--refs", "origin", item.signal_ref) == (
        f"{main_sha}\t{item.signal_ref}"
    )
    assert publish_terminal_attention(
        repo,
        remote="origin",
        run_id=item.run_id,
        terminal_kind=item.terminal_kind,
        artifact_sha=item.artifact_sha,
    ) == "REUSED"


@pytest.mark.parametrize(
    "pin",
    ["main", "26097405343150dc1b55015b94720528afad50e"],
)
def test_downstream_without_exact_immutable_provenance_remains_unavailable(
    tmp_path: Path, pin: str
) -> None:
    repo, _, _ = make_repo(tmp_path, reviewed_surface=False, exact_pin=pin)
    item = artifact_selector(repo)
    publish_terminal(repo, item)

    assert publish_terminal_attention(
        repo,
        remote="origin",
        run_id=item.run_id,
        terminal_kind=item.terminal_kind,
        artifact_sha=item.artifact_sha,
    ) == "UNAVAILABLE"
    assert git(repo, "ls-remote", "--refs", "origin", f"{SIGNAL_PREFIX}/*") == ""


def test_downstream_workflow_must_bind_the_exact_pin_provenance(
    tmp_path: Path,
) -> None:
    repo, _, _ = make_repo(
        tmp_path,
        reviewed_surface=False,
        exact_pin=PINNED_AIOS_RENEW_SHA,
        workflow_uses_pin=False,
    )
    item = artifact_selector(repo)
    publish_terminal(repo, item)

    assert publish_terminal_attention(
        repo,
        remote="origin",
        run_id=item.run_id,
        terminal_kind=item.terminal_kind,
        artifact_sha=item.artifact_sha,
    ) == "UNAVAILABLE"


def test_push_and_dispatch_admission_revalidate_remote_binding(
    tmp_path: Path,
) -> None:
    repo, _, main_sha = make_repo(tmp_path)
    item = artifact_selector(repo)
    publish_terminal(repo, item)
    publish_terminal_attention(
        repo,
        remote="origin",
        run_id=item.run_id,
        terminal_kind=item.terminal_kind,
        artifact_sha=item.artifact_sha,
    )
    policy = load_policy(POLICY_PATH)
    push_event = {
        "ref": item.signal_ref,
        "before": "0" * 40,
        "after": main_sha,
        "created": True,
        "deleted": False,
        "forced": False,
        "repository": {"full_name": policy.repository},
    }
    admitted, ref, push_sha, delivery = admit_event(
        event_name="push",
        event=push_event,
        repository=policy.repository,
        event_sha=main_sha,
        policy=policy,
    )
    assert (admitted, ref, push_sha, delivery) == (
        item,
        item.signal_ref,
        main_sha,
        "REF_EVENT",
    )
    assert validate_remote_admission(
        repo,
        remote="origin",
        item=item,
        signal_ref=item.signal_ref,
        push_signal_sha=main_sha,
        event_sha=main_sha,
        policy=policy,
    ).selector == item

    dispatch_event = {
        "ref": "main",
        "inputs": {
            "run_id": item.run_id,
            "terminal_kind": item.terminal_kind,
            "artifact_sha": item.artifact_sha,
        },
        "repository": {"full_name": policy.repository},
    }
    replay, replay_ref, push_sha, delivery = admit_event(
        event_name="workflow_dispatch",
        event=dispatch_event,
        repository=policy.repository,
        event_sha=main_sha,
        policy=policy,
    )
    assert replay == item
    assert replay_ref == item.signal_ref
    assert push_sha is None
    assert delivery == "WORKFLOW_DISPATCH_REPLAY"


def test_downstream_policy_binds_exact_dispatch_event_without_changing_semantics(
    tmp_path: Path,
) -> None:
    repository = "trung-via/python_complete_agent"
    policy = load_policy(write_policy(tmp_path, repository))
    item = selector("RUN-207-001", "FAILURE", "a" * 40)
    push_event = {
        "ref": item.signal_ref,
        "before": "0" * 40,
        "after": "b" * 40,
        "created": True,
        "deleted": False,
        "forced": False,
        "repository": {"full_name": repository},
    }
    pushed, signal_ref, push_sha, delivery = admit_event(
        event_name="push",
        event=push_event,
        repository=repository,
        event_sha="b" * 40,
        policy=policy,
    )
    assert (pushed, signal_ref, push_sha, delivery) == (
        item,
        item.signal_ref,
        "b" * 40,
        "REF_EVENT",
    )

    event = {
        "ref": "main",
        "inputs": {
            "run_id": item.run_id,
            "terminal_kind": item.terminal_kind,
            "artifact_sha": item.artifact_sha,
        },
        "repository": {"full_name": repository},
    }

    replay, signal_ref, push_sha, delivery = admit_event(
        event_name="workflow_dispatch",
        event=event,
        repository=repository,
        event_sha="b" * 40,
        policy=policy,
    )

    assert replay == item
    assert signal_ref == item.signal_ref
    assert push_sha is None
    assert delivery == "WORKFLOW_DISPATCH_REPLAY"
    with pytest.raises(TerminalAttentionError, match="repository"):
        admit_event(
            event_name="workflow_dispatch",
            event=event,
            repository="trung-via/AIOS-renew",
            event_sha="b" * 40,
            policy=policy,
        )


@pytest.mark.parametrize("repository", ["missing-owner", "owner/", "owner/repo/name"])
def test_terminal_attention_rejects_malformed_policy_repository(
    tmp_path: Path, repository: str
) -> None:
    with pytest.raises(TerminalAttentionError, match="repository"):
        load_policy(write_policy(tmp_path, repository))


def test_event_repository_and_dispatch_fields_are_not_extensible() -> None:
    policy = load_policy(POLICY_PATH)
    event = {
        "ref": "main",
        "inputs": {
            "run_id": "RUN-113-001",
            "terminal_kind": "RESULT",
            "artifact_sha": "a" * 40,
            "executor": "codex",
        },
        "repository": {"full_name": policy.repository},
    }
    with pytest.raises(TerminalAttentionError, match="inputs"):
        admit_event(
            event_name="workflow_dispatch",
            event=event,
            repository=policy.repository,
            event_sha="b" * 40,
            policy=policy,
        )
    event["inputs"].pop("executor")
    event["repository"]["full_name"] = "attacker/fork"
    with pytest.raises(TerminalAttentionError, match="repository"):
        admit_event(
            event_name="workflow_dispatch",
            event=event,
            repository=policy.repository,
            event_sha="b" * 40,
            policy=policy,
        )
