import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import aios_renew.runtime as runtime_module
from aios_renew.artifacts import (
    Claim,
    Evidence,
    EvidenceOutcome,
    EvidenceSource,
    Result,
    ResultPackage,
)
from aios_renew.review import Finding, Remediation, RemediationExecution
from aios_renew.review_transport import ReviewTransportError
from aios_renew.run import Run
from aios_renew.runtime import (
    RuntimeCompletion,
    primary_completion_policy,
    remediation_completion_policy,
    repair_completion_policy,
)
from aios_renew.task import (
    AcceptanceCriterion,
    Task,
    TaskConstraints,
    TaskScope,
    TaskVerification,
)


class BoundaryError(RuntimeError):
    pass


class StubRuntimeCompletion(RuntimeCompletion):
    def __init__(self, *args, head_sha: str, changed_files: set[str], **kwargs):
        super().__init__(*args, **kwargs)
        self.head_sha = head_sha
        self.changed_files = changed_files

    def _git(self, *args: str, strip_stdout: bool = True) -> str:
        if args == ("rev-parse", "HEAD"):
            return self.head_sha
        if args == ("status", "--porcelain"):
            return ""
        raise AssertionError(f"unexpected Git observation: {args}")

    def _committed_changed_files(self, base_sha: str, head_sha: str) -> set[str]:
        return set(self.changed_files)


def completion_fixture(tmp_path: Path):
    task = Task(
        task_id="TASK-052",
        revision=1,
        goal="Test the Runtime boundary.",
        problem="Completion needs deterministic ownership.",
        assumptions=(),
        scope=TaskScope(inspect=(), modify=("OUTPUT.txt",)),
        non_goals=(),
        constraints=TaskConstraints(hard=()),
        acceptance=(AcceptanceCriterion(id="AC1", condition="Complete."),),
        verification=TaskVerification(required=("verify-one", "verify-two")),
    )
    run = Run.from_task(
        run_id="RUN-052-001",
        task=task,
        executor="codex",
        base_sha="base",
        workspace=str(tmp_path),
    )
    state = SimpleNamespace(
        staging=tmp_path / "staging",
        verification=tmp_path / "verification",
        results=tmp_path / "results",
        failures=tmp_path / "failures",
        observations=tmp_path / "observations",
        repairs=tmp_path / "repairs",
    )
    run_path = tmp_path / "runs" / "RUN-052-001.json"
    run_path.parent.mkdir()
    run_path.write_text("{}", encoding="utf-8")
    package = ResultPackage(
        result=Result(
            head_sha="head",
            claims=(
                Claim(
                    id="C1",
                    satisfies=("AC1",),
                    claim="The admitted candidate is complete.",
                    evidence=(),
                ),
            ),
            changed_files=("OUTPUT.txt",),
            unresolved=(),
        ),
        evidence=(),
    )
    return task, run, state, run_path, package


def evidence_for(run_id: str, subject_sha: str, commands: tuple[str, ...]):
    return tuple(
        Evidence(
            evidence_id=f"{run_id}-V{index:03d}",
            run_id=run_id,
            subject_sha=subject_sha,
            type="VERIFICATION",
            source=EvidenceSource(command=command),
            result=EvidenceOutcome(exit_code=0, summary="passed"),
            raw_path=f"raw/{index}",
        )
        for index, command in enumerate(commands, start=1)
    )


def test_runtime_owns_ordered_verification_evidence_persistence_and_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task, run, state, run_path, package = completion_fixture(tmp_path)
    verification_calls = []
    transported = []

    def verify(commands, **kwargs):
        exact_commands = tuple(commands)
        verification_calls.append((exact_commands, kwargs["subject_sha"]))
        return evidence_for(kwargs["run_id"], kwargs["subject_sha"], exact_commands)

    def transport(*args, **kwargs):
        result_path = kwargs["result_path"]
        transported.append((kwargs["head_sha"], result_path.is_file()))

    monkeypatch.setattr(runtime_module, "execute_verification", verify)
    monkeypatch.setattr(runtime_module, "transport_post_pass", transport)
    completion = StubRuntimeCompletion(
        repo=tmp_path,
        state=state,
        task=task,
        run=run,
        run_path=run_path,
        verification_runner=lambda *args, **kwargs: None,
        observation_tracker=None,
        error_type=BoundaryError,
        head_sha="head",
        changed_files={"OUTPUT.txt"},
    ).complete(package, primary_completion_policy(task, base_sha=run.base_sha))

    assert verification_calls == [(("verify-one", "verify-two"), "head")]
    assert transported == [("head", True)]
    stored = json.loads(completion.result_path.read_text(encoding="utf-8"))
    assert [item["subject_sha"] for item in stored["evidence"]] == ["head", "head"]
    assert stored["result"]["claims"][0]["evidence"] == [
        "RUN-052-001-V001",
        "RUN-052-001-V002",
    ]


def test_completion_gate_failure_runs_no_verification_or_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task, run, state, run_path, package = completion_fixture(tmp_path)
    calls = []
    monkeypatch.setattr(
        runtime_module,
        "execute_verification",
        lambda *args, **kwargs: calls.append("verification"),
    )
    monkeypatch.setattr(
        runtime_module,
        "transport_post_pass",
        lambda *args, **kwargs: calls.append("transport"),
    )
    boundary = StubRuntimeCompletion(
        repo=tmp_path,
        state=state,
        task=task,
        run=run,
        run_path=run_path,
        verification_runner=lambda *args, **kwargs: None,
        observation_tracker=None,
        error_type=BoundaryError,
        head_sha="head",
        changed_files=set(),
    )

    with pytest.raises(BoundaryError, match="RESULT.changed_files mismatch"):
        boundary.complete(
            package, primary_completion_policy(task, base_sha=run.base_sha)
        )

    assert calls == []
    assert not (state.results / f"{run.run_id}.json").exists()


@pytest.mark.parametrize(
    ("kind", "direct_candidate"),
    (("REMEDIATION", False), ("DIRECT_CANDIDATE", True), ("REPAIR", False)),
)
def test_operation_policies_share_one_runtime_completion_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    direct_candidate: bool,
) -> None:
    task, run, state, run_path, primary_package = completion_fixture(tmp_path)
    finding = Finding(
        id="R1",
        basis="AC1",
        action="CODE_FIX",
        location="OUTPUT.txt",
        issue="Needs correction.",
        expected="Corrected output.",
    )
    remediation = Remediation(
        finding_id="R1",
        action="CODE_FIX",
        reviewed_sha="base",
        modification_scope=("OUTPUT.txt",),
        affected_verification=("verify-one", "verify-two"),
    )
    execution = RemediationExecution(
        review_id="REVIEW-052-001",
        finding=finding,
        remediation=remediation,
        run=run,
    )
    if kind == "REPAIR":
        policy = repair_completion_policy(
            task,
            root_base_sha="base",
            failed_head_sha="failed",
            action="CODE_FIX",
            modification_scope=("OUTPUT.txt",),
            lineage_path=tmp_path / "repairs" / "RUN-052-001.json",
        )
        package = primary_package
    else:
        policy = remediation_completion_policy(
            execution, direct_candidate=direct_candidate
        )
        package = ResultPackage(
            result=Result(
                head_sha="head",
                claims=(),
                changed_files=("OUTPUT.txt",),
                unresolved=(),
            ),
            evidence=(),
        )
    calls = []
    monkeypatch.setattr(
        runtime_module,
        "execute_verification",
        lambda commands, **kwargs: (
            calls.append((kind, tuple(commands))),
            evidence_for(kwargs["run_id"], kwargs["subject_sha"], tuple(commands)),
        )[1],
    )
    monkeypatch.setattr(
        runtime_module, "transport_post_pass", lambda *args, **kwargs: None
    )

    completion = StubRuntimeCompletion(
        repo=tmp_path,
        state=state,
        task=task,
        run=run,
        run_path=run_path,
        verification_runner=lambda *args, **kwargs: None,
        observation_tracker=None,
        error_type=BoundaryError,
        head_sha="head",
        changed_files={"OUTPUT.txt"},
    ).complete(package, policy)

    assert calls == [(kind, ("verify-one", "verify-two"))]
    assert completion.result_path.is_file()


@pytest.mark.parametrize(
    "declared_changed_files",
    (("SECOND.txt",), (), ("FIRST.txt",)),
)
def test_repair_persists_complete_root_delta_from_noncanonical_structural_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    declared_changed_files: tuple[str, ...],
) -> None:
    task, run, state, run_path, package = completion_fixture(tmp_path)
    task = replace(
        task,
        scope=TaskScope(inspect=(), modify=("FIRST.txt", "SECOND.txt")),
    )
    package = replace(
        package,
        result=replace(
            package.result,
            changed_files=declared_changed_files,
        ),
    )
    policy = repair_completion_policy(
        task,
        root_base_sha="base",
        failed_head_sha="failed",
        action="CODE_FIX",
        modification_scope=("SECOND.txt",),
        lineage_path=tmp_path / "repairs" / "RUN-052-000.json",
    )
    verification_calls = []

    class RepairTopologyCompletion(StubRuntimeCompletion):
        def _committed_changed_files(
            self, base_sha: str, head_sha: str
        ) -> set[str]:
            return {
                ("base", "head"): {"SECOND.txt", "FIRST.txt"},
                ("failed", "head"): {"SECOND.txt"},
            }[(base_sha, head_sha)]

    monkeypatch.setattr(
        runtime_module,
        "execute_verification",
        lambda commands, **kwargs: (
            verification_calls.append((tuple(commands), kwargs["subject_sha"])),
            evidence_for(kwargs["run_id"], kwargs["subject_sha"], tuple(commands)),
        )[1],
    )
    monkeypatch.setattr(
        runtime_module, "transport_post_pass", lambda *args, **kwargs: None
    )

    completion = RepairTopologyCompletion(
        repo=tmp_path,
        state=state,
        task=task,
        run=run,
        run_path=run_path,
        verification_runner=lambda *args, **kwargs: None,
        observation_tracker=None,
        error_type=BoundaryError,
        head_sha="head",
        changed_files=set(),
    ).complete(package, policy)

    staged = json.loads(
        (state.staging / f"{run.run_id}.json").read_text(encoding="utf-8")
    )
    stored = json.loads(completion.result_path.read_text(encoding="utf-8"))
    assert staged["result"]["changed_files"] == list(declared_changed_files)
    assert stored["result"]["changed_files"] == ["FIRST.txt", "SECOND.txt"]
    assert verification_calls == [(("verify-one", "verify-two"), "head")]
    assert {item["subject_sha"] for item in stored["evidence"]} == {"head"}


def test_transport_failure_after_result_does_not_erase_canonical_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task, run, state, run_path, package = completion_fixture(tmp_path)
    monkeypatch.setattr(
        runtime_module,
        "execute_verification",
        lambda commands, **kwargs: evidence_for(
            kwargs["run_id"], kwargs["subject_sha"], tuple(commands)
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "transport_post_pass",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ReviewTransportError("transport unavailable")
        ),
    )
    boundary = StubRuntimeCompletion(
        repo=tmp_path,
        state=state,
        task=task,
        run=run,
        run_path=run_path,
        verification_runner=lambda *args, **kwargs: None,
        observation_tracker=None,
        error_type=BoundaryError,
        head_sha="head",
        changed_files={"OUTPUT.txt"},
    )

    with pytest.raises(BoundaryError, match="review transport failed"):
        boundary.complete(
            package, primary_completion_policy(task, base_sha=run.base_sha)
        )

    assert (state.results / f"{run.run_id}.json").is_file()
    assert not (state.failures / f"{run.run_id}.json").exists()
