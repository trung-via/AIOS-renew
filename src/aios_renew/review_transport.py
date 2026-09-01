"""Reusable operator-layer GitHub transport for terminal RUN state."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ReviewTransportError(RuntimeError):
    """Raised when post-PASS review or artifact transport fails."""


@dataclass(frozen=True)
class RemoteRemediationLineage:
    """Immutable canonical inputs resolved from one remote remediation ref."""

    ref: str
    source_run_id: str
    review: bytes
    remediation: bytes
    run: bytes
    result: bytes


def _git_cmd(repo: Path, *args: str, strip: bool = True, allow_fail: bool = False) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True,
            text=False,
            check=False,
        )
        stdout = completed.stdout.decode("utf-8", errors="strict")
        stderr = completed.stderr.decode("utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        if allow_fail:
            return 1, "", str(exc)
        raise ReviewTransportError(f"Git command failed: {exc}") from exc
    if completed.returncode != 0 and not allow_fail:
        detail = stderr.strip() or stdout.strip()
        raise ReviewTransportError(f"Git command failed: {detail}")
    return completed.returncode, stdout.strip() if strip else stdout, stderr.strip()


def resolve_transport_remote(repo: Path) -> str:
    """Resolve configured upstream remote for the current branch or fail closed."""
    code, branch, _ = _git_cmd(repo, "symbolic-ref", "--quiet", "--short", "HEAD", allow_fail=True)
    if code == 0 and branch:
        code, remote, _ = _git_cmd(repo, "config", "--get", f"branch.{branch}.remote", allow_fail=True)
        if code == 0 and remote:
            return remote
    raise ReviewTransportError("no configured upstream Git remote for current branch")


def _read_remote_blob(repo: Path, remote: str, commit_sha: str, rel_path: str) -> bytes | None:
    """Read a blob's content at commit_sha:rel_path from remote or local object DB."""
    _git_cmd(repo, "fetch", "--no-tags", remote, commit_sha, allow_fail=True)
    code, content, _ = _git_cmd(repo, "show", f"{commit_sha}:{rel_path}", strip=False, allow_fail=True)
    if code == 0:
        return content.encode("utf-8")
    return None


def resolve_remote_remediation_lineages(
    repo: Path, *, finding_id: str
) -> tuple[RemoteRemediationLineage, ...]:
    """Resolve all structurally complete remote lineages for one finding id.

    TASK binding and frozen-contract validation are deliberately performed by the
    operator, which then requires exactly one matching lineage.
    """

    if not finding_id or "/" in finding_id or "\\" in finding_id:
        raise ReviewTransportError(f"invalid finding id: {finding_id!r}")
    remote = resolve_transport_remote(repo)
    pattern = f"refs/heads/aios/remediation/*-{finding_id}"
    code, output, _ = _git_cmd(
        repo, "ls-remote", "--refs", remote, pattern, allow_fail=True
    )
    if code:
        raise ReviewTransportError(
            f"failed to query canonical REMEDIATION refs from {remote}"
        )

    resolved: list[RemoteRemediationLineage] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            raise ReviewTransportError("malformed canonical REMEDIATION ref result")
        commit_sha, ref = parts
        prefix = "refs/heads/aios/remediation/"
        suffix = f"-{finding_id}"
        if not ref.startswith(prefix) or not ref.endswith(suffix):
            raise ReviewTransportError("canonical REMEDIATION ref name mismatch")
        source_run_id = ref[len(prefix) : -len(suffix)]
        if not source_run_id:
            raise ReviewTransportError("canonical REMEDIATION ref has no source RUN")

        _git_cmd(repo, "fetch", "--no-tags", remote, commit_sha, allow_fail=True)
        tree_code, tree_output, _ = _git_cmd(
            repo,
            "ls-tree",
            "-r",
            "--name-only",
            commit_sha,
            "--",
            ".ai/reviews",
            ".ai/remediations",
            allow_fail=True,
        )
        if tree_code:
            raise ReviewTransportError(f"cannot inspect canonical lineage at {ref}")
        review_paths = [
            path for path in tree_output.splitlines()
            if path.startswith(".ai/reviews/") and path.endswith((".yaml", ".yml"))
        ]
        remediation_paths = [
            path for path in tree_output.splitlines()
            if path.startswith(".ai/remediations/") and path.endswith((".yaml", ".yml"))
        ]
        if len(review_paths) != 1 or len(remediation_paths) != 1:
            raise ReviewTransportError(
                f"canonical lineage at {ref} must contain exactly one REVIEW and REMEDIATION"
            )
        review = _read_remote_blob(repo, remote, commit_sha, review_paths[0])
        remediation = _read_remote_blob(
            repo, remote, commit_sha, remediation_paths[0]
        )

        artifacts_ref = f"refs/heads/aios/artifacts/{source_run_id}"
        artifacts_code, artifacts_output, _ = _git_cmd(
            repo, "ls-remote", "--refs", remote, artifacts_ref, allow_fail=True
        )
        artifact_lines = [item.split() for item in artifacts_output.splitlines()]
        if artifacts_code or len(artifact_lines) != 1 or len(artifact_lines[0]) != 2:
            raise ReviewTransportError(
                f"canonical source artifacts missing or ambiguous for {source_run_id}"
            )
        artifacts_sha = artifact_lines[0][0]
        run = _read_remote_blob(
            repo, remote, artifacts_sha, ".ai/transport/run.json"
        )
        result = _read_remote_blob(
            repo, remote, artifacts_sha, ".ai/transport/result.json"
        )
        if None in (review, remediation, run, result):
            raise ReviewTransportError(f"canonical lineage content missing at {ref}")
        resolved.append(
            RemoteRemediationLineage(
                ref=ref,
                source_run_id=source_run_id,
                review=review,
                remediation=remediation,
                run=run,
                result=result,
            )
        )
    return tuple(resolved)


def _create_artifacts_commit(
    repo: Path,
    *,
    run_path: Path,
    result_path: Path,
    run_id: str,
    lineage_path: Path | None = None,
    observation_path: Path | None = None,
) -> str:
    """Create an isolated success artifact tree with optional operational state."""
    if not run_path.is_file():
        raise ReviewTransportError(f"persisted RUN JSON missing: {run_path}")
    if not result_path.is_file():
        raise ReviewTransportError(f"persisted canonical ResultPackage JSON missing: {result_path}")

    run_bytes = run_path.read_bytes()
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
            input=run_bytes,
            capture_output=True,
            check=True,
        )
        run_blob_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to hash run.json: {exc}") from exc

    result_bytes = result_path.read_bytes()
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
            input=result_bytes,
            capture_output=True,
            check=True,
        )
        result_blob_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to hash result.json: {exc}") from exc

    lineage_entry = ""
    if lineage_path is not None:
        if not lineage_path.is_file():
            raise ReviewTransportError(f"persisted REPAIR lineage JSON missing: {lineage_path}")
        try:
            proc = subprocess.run(
                ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
                input=lineage_path.read_bytes(), capture_output=True, check=True,
            )
            lineage_sha = proc.stdout.decode("utf-8", errors="strict").strip()
            lineage_entry = f"100644 blob {lineage_sha}\trepair.json\n"
        except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
            raise ReviewTransportError(f"failed to hash repair.json: {exc}") from exc

    observation_entry = ""
    if observation_path is not None:
        if not observation_path.is_file():
            raise ReviewTransportError(
                f"persisted RUN_OBSERVATION JSON missing: {observation_path}"
            )
        try:
            proc = subprocess.run(
                ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
                input=observation_path.read_bytes(), capture_output=True, check=True,
            )
            observation_sha = proc.stdout.decode("utf-8", errors="strict").strip()
            observation_entry = (
                f"100644 blob {observation_sha}\tobservation.json\n"
            )
        except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
            raise ReviewTransportError(
                f"failed to hash observation.json: {exc}"
            ) from exc

    tree_input = (
        f"100644 blob {run_blob_sha}\trun.json\n"
        f"100644 blob {result_blob_sha}\tresult.json\n"
        f"{lineage_entry}"
        f"{observation_entry}"
    )
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=tree_input.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        transport_tree_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to create transport tree: {exc}") from exc

    ai_tree_input = f"040000 tree {transport_tree_sha}\ttransport\n"
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=ai_tree_input.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        ai_tree_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to create .ai tree: {exc}") from exc

    root_tree_input = f"040000 tree {ai_tree_sha}\t.ai\n"
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=root_tree_input.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        root_tree_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to create root artifacts tree: {exc}") from exc

    commit_message = f"AIOS artifacts for {run_id}"
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo), "commit-tree", root_tree_sha, "-m", commit_message),
            capture_output=True,
            check=True,
        )
        commit_sha = proc.stdout.decode("utf-8").strip()
    except Exception as exc:
        raise ReviewTransportError(f"failed to create artifacts commit: {exc}") from exc

    return commit_sha


def _create_named_artifacts_commit(
    repo: Path,
    *,
    run_path: Path,
    artifact_path: Path,
    artifact_name: str,
    run_id: str,
    observation_path: Path | None = None,
) -> str:
    """Create an isolated artifacts commit without touching the worktree."""

    if not run_path.is_file():
        raise ReviewTransportError(f"persisted RUN JSON missing: {run_path}")
    if not artifact_path.is_file():
        raise ReviewTransportError(f"persisted {artifact_name} JSON missing: {artifact_path}")

    blobs: dict[str, str] = {}
    inputs = [("run.json", run_path), (artifact_name, artifact_path)]
    if observation_path is not None:
        if not observation_path.is_file():
            raise ReviewTransportError(
                f"persisted RUN_OBSERVATION JSON missing: {observation_path}"
            )
        inputs.append(("observation.json", observation_path))
    for name, path in inputs:
        try:
            proc = subprocess.run(
                ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
                input=path.read_bytes(), capture_output=True, check=True,
            )
            blobs[name] = proc.stdout.decode("utf-8", errors="strict").strip()
        except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
            raise ReviewTransportError(f"failed to hash {name}: {exc}") from exc
    tree_input = "".join(
        f"100644 blob {sha}\t{name}\n" for name, sha in sorted(blobs.items())
    )
    try:
        transport = subprocess.run(
            ("git", "-C", str(repo), "mktree"), input=tree_input.encode(),
            capture_output=True, check=True,
        ).stdout.decode().strip()
        ai = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=f"040000 tree {transport}\ttransport\n".encode(),
            capture_output=True, check=True,
        ).stdout.decode().strip()
        root = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=f"040000 tree {ai}\t.ai\n".encode(),
            capture_output=True, check=True,
        ).stdout.decode().strip()
        return subprocess.run(
            ("git", "-C", str(repo), "commit-tree", root, "-m", f"AIOS {artifact_name} for {run_id}"),
            capture_output=True, check=True,
        ).stdout.decode().strip()
    except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
        raise ReviewTransportError(f"failed to create {artifact_name} artifacts commit: {exc}") from exc


def transport_failure(
    repo: Path, *, run_id: str, head_sha: str, run_path: Path, failure_path: Path,
    publish_candidate: bool = True,
    observation_path: Path | None = None,
) -> None:
    """Publish an immutable, authority-checked failed candidate and its facts."""

    remote = resolve_transport_remote(repo)
    candidate_ref = f"refs/heads/aios/failure/{run_id}"
    artifacts_ref = f"refs/heads/aios/failure-artifacts/{run_id}"
    expected = {
        ".ai/transport/run.json": run_path.read_bytes(),
        ".ai/transport/failure.json": failure_path.read_bytes(),
    }
    expected_observation = (
        observation_path.read_bytes() if observation_path is not None else None
    )
    queried_refs = (candidate_ref, artifacts_ref) if publish_candidate else (artifacts_ref,)
    code, output, _ = _git_cmd(repo, "ls-remote", remote, *queried_refs, allow_fail=True)
    if code:
        raise ReviewTransportError(f"failed to query remote refs from {remote}")
    refs = {parts[1]: parts[0] for line in output.splitlines() if len(parts := line.split()) >= 2}
    specs: list[str] = []
    if publish_candidate:
        if candidate_ref in refs:
            if refs[candidate_ref] != head_sha:
                raise ReviewTransportError(f"remote failure ref {candidate_ref} conflict")
        else:
            specs.append(f"{head_sha}:{candidate_ref}")
    if artifacts_ref in refs:
        for path, content in expected.items():
            if _read_remote_blob(repo, remote, refs[artifacts_ref], path) != content:
                raise ReviewTransportError(
                    f"remote failure artifacts ref {artifacts_ref} exists with different artifact content"
                )
        remote_observation = _read_remote_blob(
            repo, remote, refs[artifacts_ref], ".ai/transport/observation.json"
        )
        if (
            expected_observation is not None
            and remote_observation not in (None, expected_observation)
        ):
            raise ReviewTransportError(
                f"remote failure artifacts ref {artifacts_ref} exists with different observation content"
            )
    else:
        commit = _create_named_artifacts_commit(
            repo, run_path=run_path, artifact_path=failure_path,
            artifact_name="failure.json", run_id=run_id,
            observation_path=observation_path,
        )
        specs.append(f"{commit}:{artifacts_ref}")
    if specs:
        code, _, stderr = _git_cmd(repo, "push", "--no-tags", remote, *specs, allow_fail=True)
        if code:
            raise ReviewTransportError(f"failed to push transport refs to {remote}: {stderr}")


def transport_admission_failure(
    repo: Path, *, identity: str, diagnostic_path: Path
) -> None:
    """Publish one immutable, content-addressed pre-RUN diagnostic."""

    if len(identity) != 64 or any(
        character not in "0123456789abcdef" for character in identity
    ):
        raise ReviewTransportError("invalid admission-failure identity")
    if not diagnostic_path.is_file():
        raise ReviewTransportError(
            f"persisted admission-failure JSON missing: {diagnostic_path}"
        )
    if hashlib.sha256(diagnostic_path.read_bytes()).hexdigest() != identity:
        raise ReviewTransportError(
            "admission-failure identity does not match diagnostic content"
        )
    remote = resolve_transport_remote(repo)
    ref = f"refs/heads/aios/admission-failure/{identity}"
    code, output, _ = _git_cmd(
        repo, "ls-remote", "--refs", remote, ref, allow_fail=True
    )
    if code:
        raise ReviewTransportError(f"failed to query remote refs from {remote}")
    lines = [line.split() for line in output.splitlines() if line.strip()]
    if lines:
        if len(lines) != 1 or len(lines[0]) != 2 or lines[0][1] != ref:
            raise ReviewTransportError("malformed admission-failure ref result")
        remote_content = _read_remote_blob(
            repo,
            remote,
            lines[0][0],
            ".ai/transport/admission-failure.json",
        )
        if remote_content != diagnostic_path.read_bytes():
            raise ReviewTransportError(
                f"remote admission-failure ref {ref} exists with different content"
            )
        return

    commit = _create_admission_failure_commit(
        repo, identity=identity, diagnostic_path=diagnostic_path
    )
    code, _, stderr = _git_cmd(
        repo,
        "push",
        "--no-tags",
        remote,
        f"{commit}:{ref}",
        allow_fail=True,
    )
    if code:
        raise ReviewTransportError(
            f"failed to push admission-failure ref to {remote}: {stderr}"
        )


def _create_admission_failure_commit(
    repo: Path, *, identity: str, diagnostic_path: Path
) -> str:
    """Create an isolated diagnostic commit without touching the worktree."""

    try:
        blob = subprocess.run(
            ("git", "-C", str(repo), "hash-object", "-w", "--stdin"),
            input=diagnostic_path.read_bytes(),
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="strict").strip()
        transport_tree = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=(
                f"100644 blob {blob}\tadmission-failure.json\n"
            ).encode("utf-8"),
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="strict").strip()
        ai_tree = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=f"040000 tree {transport_tree}\ttransport\n".encode("utf-8"),
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="strict").strip()
        root_tree = subprocess.run(
            ("git", "-C", str(repo), "mktree"),
            input=f"040000 tree {ai_tree}\t.ai\n".encode("utf-8"),
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="strict").strip()
        return subprocess.run(
            (
                "git",
                "-C",
                str(repo),
                "commit-tree",
                root_tree,
                "-m",
                f"AIOS admission failure {identity}",
            ),
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="strict").strip()
    except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
        raise ReviewTransportError(
            f"failed to create admission-failure commit: {exc}"
        ) from exc


def read_remote_repair(repo: Path, run_id: str) -> bytes:
    """Read the single ChatGPT-authored repair bound to a failed RUN."""

    remote = resolve_transport_remote(repo)
    ref = f"refs/heads/aios/repair/{run_id}"
    code, output, _ = _git_cmd(repo, "ls-remote", remote, ref, allow_fail=True)
    if code or not output.strip():
        raise ReviewTransportError(f"remote REPAIR not found for {run_id}")
    lines = [line.split() for line in output.splitlines() if len(line.split()) >= 2]
    if len(lines) != 1:
        raise ReviewTransportError(f"remote REPAIR is ambiguous for {run_id}")
    content = _read_remote_blob(repo, remote, lines[0][0], ".ai/transport/repair.json")
    if content is None:
        raise ReviewTransportError(f"remote REPAIR JSON missing for {run_id}")
    return content


def transport_post_pass(
    repo: Path,
    *,
    run_id: str,
    head_sha: str,
    run_path: Path,
    result_path: Path,
    lineage_path: Path | None = None,
    observation_path: Path | None = None,
) -> None:
    """Publish aios/review/<RUN_ID> and aios/artifacts/<RUN_ID> to upstream remote."""
    remote = resolve_transport_remote(repo)
    review_ref = f"refs/heads/aios/review/{run_id}"
    artifacts_ref = f"refs/heads/aios/artifacts/{run_id}"

    if not run_path.is_file():
        raise ReviewTransportError(f"persisted RUN JSON missing: {run_path}")
    if not result_path.is_file():
        raise ReviewTransportError(f"persisted canonical ResultPackage JSON missing: {result_path}")

    expected_run_bytes = run_path.read_bytes()
    expected_result_bytes = result_path.read_bytes()
    expected_lineage_bytes = lineage_path.read_bytes() if lineage_path is not None else None
    expected_observation_bytes = (
        observation_path.read_bytes() if observation_path is not None else None
    )

    code, ls_out, _ = _git_cmd(repo, "ls-remote", remote, review_ref, artifacts_ref, allow_fail=True)
    if code != 0:
        raise ReviewTransportError(f"failed to query remote refs from {remote}")

    existing_remote_refs: dict[str, str] = {}
    for line in ls_out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            existing_remote_refs[parts[1]] = parts[0]

    push_review = True
    if review_ref in existing_remote_refs:
        existing_review_sha = existing_remote_refs[review_ref]
        if existing_review_sha == head_sha:
            push_review = False
        else:
            raise ReviewTransportError(
                f"remote review ref {review_ref} conflict: points to {existing_review_sha}, expected {head_sha}"
            )

    push_artifacts = True
    if artifacts_ref in existing_remote_refs:
        existing_artifacts_sha = existing_remote_refs[artifacts_ref]
        remote_run_bytes = _read_remote_blob(repo, remote, existing_artifacts_sha, ".ai/transport/run.json")
        remote_result_bytes = _read_remote_blob(repo, remote, existing_artifacts_sha, ".ai/transport/result.json")
        remote_lineage_bytes = _read_remote_blob(
            repo, remote, existing_artifacts_sha, ".ai/transport/repair.json"
        )
        remote_observation_bytes = _read_remote_blob(
            repo, remote, existing_artifacts_sha, ".ai/transport/observation.json"
        )

        if (
            remote_run_bytes == expected_run_bytes
            and remote_result_bytes == expected_result_bytes
            and remote_lineage_bytes == expected_lineage_bytes
            and (
                expected_observation_bytes is None
                or remote_observation_bytes in (None, expected_observation_bytes)
            )
        ):
            push_artifacts = False
        else:
            raise ReviewTransportError(
                f"remote artifacts ref {artifacts_ref} exists with different artifact content"
            )

    if not push_review and not push_artifacts:
        return

    artifacts_commit_sha = _create_artifacts_commit(
        repo,
        run_path=run_path,
        result_path=result_path,
        run_id=run_id,
        lineage_path=lineage_path,
        observation_path=observation_path,
    )

    push_specs: list[str] = []
    if push_review:
        push_specs.append(f"{head_sha}:{review_ref}")
    if push_artifacts:
        push_specs.append(f"{artifacts_commit_sha}:{artifacts_ref}")

    if push_specs:
        code, _, stderr = _git_cmd(repo, "push", "--no-tags", remote, *push_specs, allow_fail=True)
        if code != 0:
            raise ReviewTransportError(f"failed to push transport refs to {remote}: {stderr}")
