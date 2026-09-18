"""Authoritative Correction Integration boundary for AIOS-renew.

Provides explicit, content-addressed, conflict-free integration of divergent
canonical main into a prospective cumulative REMEDIATION execution base.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID_PATTERN = re.compile(r"^RUN-[A-Za-z0-9][A-Za-z0-9._-]*$")
_TASK_ID_PATTERN = re.compile(r"^TASK-[A-Za-z0-9][A-Za-z0-9._-]*$")


class CorrectionIntegrationError(RuntimeError):
    """Raised when correction integration cannot be cleanly and deterministically materialized."""


def derive_integration_identity(
    task_id: str,
    task_revision: int,
    cumulative_tip_run_id: str,
    cumulative_tip_candidate_sha: str,
    authorized_main_sha: str,
) -> str:
    """Derive one deterministic, content-addressed integration identity from the exact binding."""
    material = "\0".join([
        str(task_id).strip(),
        str(task_revision),
        str(cumulative_tip_run_id).strip(),
        str(cumulative_tip_candidate_sha).strip().lower(),
        str(authorized_main_sha).strip().lower(),
    ])
    return hashlib.sha256(material.encode("ascii")).hexdigest()


def integration_ref_name(integration_id: str) -> str:
    """Return the dedicated content-addressed Git ref for an integration identity."""
    return f"refs/heads/aios/integration/{integration_id}"


@dataclass(frozen=True)
class CorrectionIntegrationResult:
    """Exact deterministic result of one authorized correction integration."""

    task_id: str
    task_revision: int
    cumulative_tip_run_id: str
    cumulative_tip_candidate_sha: str
    authorized_main_sha: str
    integration_id: str
    integration_candidate_sha: str
    integration_ref: str
    tree_sha: str
    parents: tuple[str, str]
    format: str = "AIOS_CORRECTION_INTEGRATION"
    version: int = 1
    kind: str = "CORRECTION_INTEGRATION"

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "kind": self.kind,
            "task_id": self.task_id,
            "task_revision": self.task_revision,
            "cumulative_tip_run_id": self.cumulative_tip_run_id,
            "cumulative_tip_candidate_sha": self.cumulative_tip_candidate_sha,
            "authorized_main_sha": self.authorized_main_sha,
            "integration_id": self.integration_id,
            "integration_candidate_sha": self.integration_candidate_sha,
            "integration_ref": self.integration_ref,
            "tree_sha": self.tree_sha,
            "parents": list(self.parents),
        }

    def render(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))

    def render_human(self) -> str:
        return (
            f"AIOS CORRECTION INTEGRATION SUCCEEDED\n"
            f"task: {self.task_id} (r{self.task_revision})\n"
            f"cumulative_tip: {self.cumulative_tip_run_id} ({self.cumulative_tip_candidate_sha[:8]})\n"
            f"authorized_main: {self.authorized_main_sha[:8]}\n"
            f"integration_id: {self.integration_id}\n"
            f"integration_ref: {self.integration_ref}\n"
            f"candidate_sha: {self.integration_candidate_sha}"
        )


def _git_cmd(
    repo: Path, *args: str, allow_fail: bool = False, strip: bool = True
) -> tuple[int, str, str]:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = completed.stdout.strip() if strip else completed.stdout
    stderr = completed.stderr.strip() if strip else completed.stderr
    if completed.returncode != 0 and not allow_fail:
        raise CorrectionIntegrationError(f"Git command failed: {stderr or stdout}")
    return completed.returncode, stdout, stderr


def _commit_tree(
    repo: Path,
    tree_sha: str,
    parent_shas: Sequence[str],
    message: str,
) -> str:
    args = ["commit-tree", tree_sha]
    for p in parent_shas:
        args.extend(["-p", p])
    args.extend(["-m", message])
    env = dict(os.environ)
    env.setdefault("GIT_AUTHOR_NAME", "AIOS Integration")
    env.setdefault("GIT_AUTHOR_EMAIL", "aios-integration@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "AIOS Integration")
    env.setdefault("GIT_COMMITTER_EMAIL", "aios-integration@example.invalid")
    proc = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise CorrectionIntegrationError(f"failed to create commit-tree: {detail}")
    return proc.stdout.strip()


def resolve_valid_integration(
    repo: Path,
    *,
    task_id: str,
    task_revision: int,
    cumulative_tip_run_id: str,
    cumulative_tip_candidate_sha: str,
    authorized_main_sha: str,
) -> CorrectionIntegrationResult | None:
    """Validate and return the exact integration candidate if present and clean, else None."""
    integration_id = derive_integration_identity(
        task_id,
        task_revision,
        cumulative_tip_run_id,
        cumulative_tip_candidate_sha,
        authorized_main_sha,
    )
    ref = integration_ref_name(integration_id)

    # 1. Look up ref locally
    code, out, _ = _git_cmd(
        repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", allow_fail=True
    )
    if code != 0 or not out:
        # Check remote-tracking branch refs/remotes/origin/...
        code, out, _ = _git_cmd(
            repo,
            "rev-parse",
            "--verify",
            "-q",
            f"refs/remotes/origin/aios/integration/{integration_id}^{{commit}}",
            allow_fail=True,
        )
    if code != 0 or not out:
        # Check ls-remote if a remote is configured
        code_remote, remote, _ = _git_cmd(
            repo, "config", "--get", "remote.origin.url", allow_fail=True
        )
        if code_remote == 0 and remote:
            code_ls, ls_out, _ = _git_cmd(
                repo, "ls-remote", "--refs", "origin", ref, allow_fail=True
            )
            if code_ls == 0 and ls_out.strip():
                remote_sha = ls_out.strip().split()[0]
                _git_cmd(repo, "fetch", "--no-tags", "origin", remote_sha, allow_fail=True)
                out = remote_sha
                code = 0
    if code != 0 or not out:
        return None

    candidate_sha = out.strip()
    if not _SHA_PATTERN.fullmatch(candidate_sha):
        return None

    # 2. Check commit type
    code, kind, _ = _git_cmd(repo, "cat-file", "-t", candidate_sha, allow_fail=True)
    if code != 0 or kind != "commit":
        return None

    # 3. Check parents: must be exactly [cumulative_tip_candidate_sha, authorized_main_sha]
    code, parents_out, _ = _git_cmd(repo, "rev-parse", f"{candidate_sha}^@", allow_fail=True)
    if code != 0:
        return None
    parents = parents_out.split()
    if (
        len(parents) != 2
        or parents[0] != cumulative_tip_candidate_sha
        or parents[1] != authorized_main_sha
    ):
        return None

    # 4. Check merge base: must be exactly 1
    code, mb_out, _ = _git_cmd(
        repo, "merge-base", "--all", cumulative_tip_candidate_sha, authorized_main_sha, allow_fail=True
    )
    if code != 0:
        return None
    mb_list = [line.strip() for line in mb_out.splitlines() if line.strip()]
    if len(mb_list) != 1:
        return None

    # 5. Check clean merge tree
    code, tree_out, _ = _git_cmd(
        repo, "merge-tree", "--write-tree", cumulative_tip_candidate_sha, authorized_main_sha, allow_fail=True
    )
    if code != 0:
        return None
    expected_tree = tree_out.strip().splitlines()[0]
    code, actual_tree, _ = _git_cmd(repo, "rev-parse", f"{candidate_sha}^{{tree}}", allow_fail=True)
    if code != 0 or actual_tree != expected_tree:
        return None

    return CorrectionIntegrationResult(
        task_id=task_id,
        task_revision=task_revision,
        cumulative_tip_run_id=cumulative_tip_run_id,
        cumulative_tip_candidate_sha=cumulative_tip_candidate_sha,
        authorized_main_sha=authorized_main_sha,
        integration_id=integration_id,
        integration_candidate_sha=candidate_sha,
        integration_ref=ref,
        tree_sha=expected_tree,
        parents=(cumulative_tip_candidate_sha, authorized_main_sha),
    )


def integrate_correction(
    task_id: str,
    *,
    task_revision: int,
    cumulative_tip_run_id: str,
    cumulative_tip_candidate_sha: str,
    authorized_main_sha: str | None = None,
    current_main_sha: str | None = None,
    expected_main_sha: str | None = None,
    repo: str | Path | None = None,
) -> CorrectionIntegrationResult:
    """Materialize one authorized deterministic correction integration candidate."""
    main_sha = authorized_main_sha or current_main_sha or expected_main_sha
    if not main_sha:
        raise CorrectionIntegrationError("authorized main SHA is required")
    if not _SHA_PATTERN.fullmatch(main_sha):
        raise CorrectionIntegrationError(f"invalid authorized main SHA: {main_sha}")
    if not _SHA_PATTERN.fullmatch(cumulative_tip_candidate_sha):
        raise CorrectionIntegrationError(
            f"invalid cumulative tip candidate SHA: {cumulative_tip_candidate_sha}"
        )
    if not _RUN_ID_PATTERN.fullmatch(cumulative_tip_run_id):
        raise CorrectionIntegrationError(
            f"invalid cumulative tip RUN id: {cumulative_tip_run_id}"
        )
    if isinstance(task_revision, bool) or not isinstance(task_revision, int) or task_revision < 1:
        raise CorrectionIntegrationError(f"invalid task revision: {task_revision}")

    root = Path(repo).resolve() if repo else Path.cwd().resolve()

    # Check task
    task_path = root / ".ai" / "tasks" / f"{task_id}.yaml"
    if not task_path.is_file():
        raise CorrectionIntegrationError(f"TASK document not found: {task_id}")
    from .task import parse_task

    try:
        loaded_task = parse_task(task_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CorrectionIntegrationError(f"cannot load TASK {task_id}: {exc}") from exc
    if loaded_task.task_id != task_id or loaded_task.revision != task_revision:
        raise CorrectionIntegrationError(
            f"TASK identity or revision mismatch: requested {task_id} r{task_revision}, "
            f"found {loaded_task.task_id} r{loaded_task.revision}"
        )

    # 1. Resolve canonical remote task lifecycle as mandatory evidence
    from .review_transport import resolve_remote_task_lifecycle as _default_resolve_lifecycle
    from .unified_state import _decode_remote_lifecycle, _operational_parent

    import aios_renew.operator as op_module
    import aios_renew.review_transport as rt_module

    resolve_lifecycle_fn = getattr(op_module, "resolve_remote_task_lifecycle", None)
    if (
        resolve_lifecycle_fn is None
        or resolve_lifecycle_fn is getattr(rt_module, "resolve_remote_task_lifecycle", None)
    ):
        resolve_lifecycle_fn = getattr(rt_module, "resolve_remote_task_lifecycle", _default_resolve_lifecycle)
    if resolve_lifecycle_fn is None:
        resolve_lifecycle_fn = _default_resolve_lifecycle

    try:
        lifecycle = resolve_lifecycle_fn(
            root, task_id=task_id, task_revision=task_revision
        )
    except CorrectionIntegrationError:
        raise
    except Exception as exc:
        raise CorrectionIntegrationError(
            f"failed to resolve canonical remote task lifecycle: {exc}"
        ) from exc

    # 2. Canonical remote main validation: require main_sha to equal authorized main SHA
    lifecycle_main = getattr(lifecycle, "main_sha", None)
    if not lifecycle_main or not isinstance(lifecycle_main, str):
        raise CorrectionIntegrationError("canonical remote task lifecycle has no main SHA")
    if lifecycle_main != main_sha:
        raise CorrectionIntegrationError(
            f"canonical remote main SHA ({lifecycle_main}) does not match authorized main SHA ({main_sha})"
        )

    # 3. Decode canonical remote lifecycle: propagate decoding failures as CorrectionIntegrationError
    try:
        decoded, _ = _decode_remote_lifecycle(root, loaded_task, lifecycle)
    except CorrectionIntegrationError:
        raise
    except Exception as exc:
        raise CorrectionIntegrationError(
            f"failed to decode canonical remote task lifecycle: {exc}"
        ) from exc

    # 4. Require exactly one valid RESULT operational tip whose RUN id and candidate SHA equal authorized cumulative tip
    children: dict[str, list[Any]] = {}
    for item in decoded:
        p = _operational_parent(item)
        if p is not None:
            children.setdefault(p, []).append(item)
    if any(len(value) != 1 for value in children.values()):
        raise CorrectionIntegrationError(
            "cumulative correction lineage has competing continuations"
        )
    tips = [item for item in decoded if item.run_id not in children]
    if not tips:
        raise CorrectionIntegrationError(
            "canonical remote task lifecycle has no operational tips"
        )
    if len(tips) != 1:
        raise CorrectionIntegrationError(
            f"canonical remote task lifecycle has competing operational tips: {[t.run_id for t in tips]}"
        )

    canonical_tip = tips[0]
    tip_kind = getattr(canonical_tip, "terminal_kind", getattr(canonical_tip, "kind", None))
    if tip_kind != "RESULT":
        raise CorrectionIntegrationError(
            f"canonical operational tip {canonical_tip.run_id} is not a valid RESULT terminal (found {tip_kind})"
        )
    if (
        canonical_tip.run_id != cumulative_tip_run_id
        or canonical_tip.candidate_sha != cumulative_tip_candidate_sha
    ):
        raise CorrectionIntegrationError(
            f"cumulative tip selector is stale: expected {canonical_tip.run_id} ({canonical_tip.candidate_sha}), "
            f"authorized {cumulative_tip_run_id} ({cumulative_tip_candidate_sha})"
        )

    # 5. Validate commits exist locally
    code, kind, _ = _git_cmd(
        root, "cat-file", "-t", cumulative_tip_candidate_sha, allow_fail=True
    )
    if code != 0 or kind != "commit":
        raise CorrectionIntegrationError(
            f"cumulative tip candidate commit {cumulative_tip_candidate_sha} is missing or not a commit"
        )
    code, kind, _ = _git_cmd(
        root, "cat-file", "-t", main_sha, allow_fail=True
    )
    if code != 0 or kind != "commit":
        raise CorrectionIntegrationError(
            f"authorized main commit {main_sha} is missing or not a commit"
        )

    integration_id = derive_integration_identity(
        task_id, task_revision, cumulative_tip_run_id, cumulative_tip_candidate_sha, main_sha
    )
    ref = integration_ref_name(integration_id)

    # 3. Idempotency / collision check:
    code, existing_sha, _ = _git_cmd(
        root, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", allow_fail=True
    )
    if code == 0 and existing_sha:
        existing_result = resolve_valid_integration(
            root,
            task_id=task_id,
            task_revision=task_revision,
            cumulative_tip_run_id=cumulative_tip_run_id,
            cumulative_tip_candidate_sha=cumulative_tip_candidate_sha,
            authorized_main_sha=main_sha,
        )
        if existing_result is not None and existing_result.integration_candidate_sha == existing_sha:
            return existing_result
        raise CorrectionIntegrationError(
            f"existing integration ref {ref} collides with a different or corrupted candidate commit"
        )

    # 4. Check clean merge
    code, mb_out, _ = _git_cmd(
        root, "merge-base", "--all", cumulative_tip_candidate_sha, main_sha, allow_fail=True
    )
    if code != 0:
        raise CorrectionIntegrationError(
            "cannot find merge base between cumulative tip and main"
        )
    mb_list = [line.strip() for line in mb_out.splitlines() if line.strip()]
    if len(mb_list) != 1:
        raise CorrectionIntegrationError(
            f"ambiguous or missing merge base between cumulative tip and main (found {len(mb_list)})"
        )

    code, tree_out, tree_err = _git_cmd(
        root, "merge-tree", "--write-tree", cumulative_tip_candidate_sha, main_sha, allow_fail=True
    )
    if code != 0:
        raise CorrectionIntegrationError(
            f"merge conflict between cumulative tip and main: {tree_err or tree_out}"
        )
    merge_tree_sha = tree_out.strip().splitlines()[0]
    if not _SHA_PATTERN.fullmatch(merge_tree_sha):
        raise CorrectionIntegrationError("git merge-tree produced invalid tree SHA")

    # 5. Materialize candidate commit via git commit-tree
    commit_msg = (
        f"AIOS correction integration for {task_id} r{task_revision}\n\n"
        f"task_id: {task_id}\n"
        f"task_revision: {task_revision}\n"
        f"cumulative_tip_run_id: {cumulative_tip_run_id}\n"
        f"cumulative_tip_candidate_sha: {cumulative_tip_candidate_sha}\n"
        f"authorized_main_sha: {main_sha}\n"
        f"integration_id: {integration_id}\n"
    )
    candidate_commit_sha = _commit_tree(
        root,
        tree_sha=merge_tree_sha,
        parent_shas=[cumulative_tip_candidate_sha, main_sha],
        message=commit_msg,
    )

    # 6. Update local ref
    _git_cmd(root, "update-ref", ref, candidate_commit_sha)

    # 7. If remote origin exists, push ref to remote
    code, remote, _ = _git_cmd(
        root, "config", "--get", "remote.origin.url", allow_fail=True
    )
    if code == 0 and remote:
        _git_cmd(
            root,
            "push",
            "--quiet",
            "--no-tags",
            "origin",
            f"{candidate_commit_sha}:{ref}",
            allow_fail=True,
        )

    return CorrectionIntegrationResult(
        task_id=task_id,
        task_revision=task_revision,
        cumulative_tip_run_id=cumulative_tip_run_id,
        cumulative_tip_candidate_sha=cumulative_tip_candidate_sha,
        authorized_main_sha=main_sha,
        integration_id=integration_id,
        integration_candidate_sha=candidate_commit_sha,
        integration_ref=ref,
        tree_sha=merge_tree_sha,
        parents=(cumulative_tip_candidate_sha, main_sha),
    )


__all__ = [
    "CorrectionIntegrationError",
    "CorrectionIntegrationResult",
    "derive_integration_identity",
    "integration_ref_name",
    "integrate_correction",
    "resolve_valid_integration",
]
