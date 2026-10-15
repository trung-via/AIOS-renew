import pytest
import copy
from dataclasses import replace

from aios_renew import (
    ArtifactValidationError,
    ResultPackage,
    Run,
    parse_evidence,
    parse_result,
    parse_task,
    validate_result_package,
)
from aios_renew.artifacts import (
    Claim,
    Result,
    validate_structural_result,
    validate_structural_result_package,
    Evidence, EvidenceSource, EvidenceOutcome, validate_evidence,
)


def test_v2_package_keeps_raw_nonzero_and_validates_separate_baseline_outcome():
    from tests.test_verification_contract import V2_BASE, V2_CANDIDATE, V2_COMMAND, v2_observation, v2_record
    from aios_renew.verification_contract import MINIMUM_SUFFICIENT_V2
    from aios_renew.runtime import result_package_data
    task, run, result, _ = make_contracts()
    task = replace(task, verification=replace(task.verification, required=(V2_COMMAND,), policy=MINIMUM_SUFFICIENT_V2))
    run = replace(run, base_sha=V2_BASE)
    result = replace(result, head_sha=V2_CANDIDATE)
    record = v2_record(base=v2_observation(V2_BASE), candidate=v2_observation())
    item = Evidence("E1", run.run_id, V2_CANDIDATE, "VERIFICATION", EvidenceSource(V2_COMMAND),
                    EvidenceOutcome(1, "51 pre-existing failures"), "immutable.raw", record)
    package = validate_result_package(task=task, run=run, result=result, evidence=(item,))
    decoded = result_package_data(package)["evidence"][0]
    assert decoded["result"]["exit_code"] == 1
    assert validate_evidence(decoded) == item
    tampered = copy.deepcopy(decoded)
    tampered["verification"]["candidate"]["reports"][0]["fingerprint"] = "0" * 64
    with pytest.raises(ArtifactValidationError, match="Runtime verification record"):
        validate_evidence(tampered)
    legacy = replace(task, verification=replace(task.verification, policy="minimum-sufficient-v1"))
    with pytest.raises(ArtifactValidationError, match="successful evidence"):
        validate_result_package(task=legacy, run=run, result=result, evidence=(item,))
    wrong_base = replace(run, base_sha="d" * 40)
    with pytest.raises(ArtifactValidationError, match="attribution base"):
        validate_result_package(task=task, run=wrong_base, result=result, evidence=(item,))
    unbound = replace(item, result=EvidenceOutcome(0, "pass"), verification=None)
    with pytest.raises(ArtifactValidationError, match="successful evidence"):
        validate_result_package(task=task, run=run, result=result, evidence=(unbound,))


TASK_SOURCE = """
task_id: TASK-005
revision: 1
goal: Define canonical RESULT and EVIDENCE contracts.
problem: Executor claims require immutable proof.
assumptions: []
scope:
  inspect: []
  modify:
    - src/aios_renew/artifacts.py
non_goals:
  - Semantic review.
constraints:
  hard:
    - Claims require evidence.
acceptance:
  - id: AC1
    condition: RESULT claims bind to acceptance and evidence.
verification:
  required:
    - pytest tests/test_artifacts.py
"""

RESULT_SOURCE = """
head_sha: def456
claims:
  - id: C1
    satisfies:
      - AC1
    claim: RESULT and EVIDENCE are implemented.
    evidence:
      - E1
changed_files:
  - src/aios_renew/artifacts.py
unresolved: []
"""

EVIDENCE_SOURCE = """
evidence_id: E1
run_id: RUN-005-001
subject_sha: def456
type: TEST
source:
  command: pytest tests/test_artifacts.py
result:
  exit_code: 0
  summary: 6 passed
raw:
  path: .ai/evidence/E1.log
"""


def make_contracts(policy=None):
    task = parse_task(TASK_SOURCE)
    if policy is not None:
        task = replace(task, verification=replace(task.verification, policy=policy))
    run = Run.from_task(
        run_id="RUN-005-001",
        task=task,
        executor="codex",
        base_sha="abc123",
        workspace="C:/workspace",
    )
    return task, run, parse_result(RESULT_SOURCE), parse_evidence(EVIDENCE_SOURCE)


def test_parses_result_contract() -> None:
    result = parse_result(RESULT_SOURCE)

    assert result.head_sha == "def456"
    assert result.claims[0].satisfies == ("AC1",)
    assert result.claims[0].evidence == ("E1",)


def test_parses_evidence_contract() -> None:
    evidence = parse_evidence(EVIDENCE_SOURCE)

    assert evidence.run_id == "RUN-005-001"
    assert evidence.result.exit_code == 0
    assert evidence.raw_path == ".ai/evidence/E1.log"


@pytest.mark.parametrize("policy", (None, "minimum-sufficient-v1"))
def test_builds_sha_bound_result_package(policy) -> None:
    task, run, result, evidence = make_contracts(policy)

    package = validate_result_package(
        task=task,
        run=run,
        result=result,
        evidence=[evidence],
    )

    assert package == ResultPackage(result=result, evidence=(evidence,))


def test_rejects_claim_without_evidence_reference() -> None:
    invalid = RESULT_SOURCE.replace("evidence:\n      - E1", "evidence: []")

    with pytest.raises(ArtifactValidationError, match="evidence must not be empty"):
        parse_result(invalid)


def test_rejects_missing_referenced_evidence() -> None:
    task, run, result, _ = make_contracts()

    with pytest.raises(ArtifactValidationError, match="missing evidence: E1"):
        validate_result_package(
            task=task,
            run=run,
            result=result,
            evidence=[],
        )


@pytest.mark.parametrize("policy", (None, "minimum-sufficient-v1"))
def test_rejects_evidence_for_different_sha(policy) -> None:
    task, run, result, _ = make_contracts(policy)
    wrong_sha = parse_evidence(
        EVIDENCE_SOURCE.replace("subject_sha: def456", "subject_sha: other")
    )

    with pytest.raises(ArtifactValidationError, match="subject_sha"):
        validate_result_package(
            task=task,
            run=run,
            result=result,
            evidence=[wrong_sha],
        )


def test_rejects_unknown_acceptance_reference() -> None:
    task, run, _, evidence = make_contracts()
    result = parse_result(RESULT_SOURCE.replace("- AC1", "- AC9"))

    with pytest.raises(ArtifactValidationError, match="unknown acceptance.*AC9"):
        validate_result_package(
            task=task,
            run=run,
            result=result,
            evidence=[evidence],
        )


@pytest.mark.parametrize("policy", (None, "minimum-sufficient-v1"))
def test_rejects_missing_required_verification_evidence(policy) -> None:
    task, run, result, _ = make_contracts(policy)
    unrelated = parse_evidence(
        EVIDENCE_SOURCE.replace(
            "command: pytest tests/test_artifacts.py",
            "command: pytest tests/test_executor.py",
        )
    )

    with pytest.raises(
        ArtifactValidationError, match="missing verification evidence"
    ):
        validate_result_package(
            task=task,
            run=run,
            result=result,
            evidence=[unrelated],
        )


@pytest.mark.parametrize("policy", (None, "minimum-sufficient-v1"))
@pytest.mark.parametrize("exit_code", (1, 2, -2, 9))
def test_rejects_failed_required_verification_evidence(policy, exit_code) -> None:
    task, run, result, _ = make_contracts(policy)
    failed = parse_evidence(EVIDENCE_SOURCE.replace("exit_code: 0", f"exit_code: {exit_code}"))

    with pytest.raises(ArtifactValidationError, match="no successful evidence"):
        validate_result_package(
            task=task,
            run=run,
            result=result,
            evidence=[failed],
        )


@pytest.mark.parametrize("policy", (None, "minimum-sufficient-v1"))
def test_required_verification_uses_exact_command_equality(policy) -> None:
    task, run, result, _ = make_contracts(policy)
    different = parse_evidence(
        EVIDENCE_SOURCE.replace(
            "command: pytest tests/test_artifacts.py",
            "command: pytest  tests/test_artifacts.py",
        )
    )

    with pytest.raises(
        ArtifactValidationError, match="missing verification evidence"
    ):
        validate_result_package(
            task=task,
            run=run,
            result=result,
            evidence=[different],
        )


def test_allows_extra_evidence_with_successful_required_verification() -> None:
    task, run, result, required = make_contracts()
    extra = parse_evidence(
        EVIDENCE_SOURCE.replace("evidence_id: E1", "evidence_id: E2").replace(
            "command: pytest tests/test_artifacts.py",
            "command: git diff --check",
        )
    )

    package = validate_result_package(
        task=task,
        run=run,
        result=result,
        evidence=[required, extra],
    )

    assert package.evidence == (required, extra)


def test_verification_evidence_need_not_be_referenced_by_claim() -> None:
    task, run, result, required = make_contracts()
    claim_evidence = parse_evidence(
        EVIDENCE_SOURCE.replace("evidence_id: E1", "evidence_id: E2").replace(
            "command: pytest tests/test_artifacts.py",
            "command: manual acceptance inspection",
        )
    )
    result = parse_result(RESULT_SOURCE.replace("- E1", "- E2"))

    package = validate_result_package(
        task=task,
        run=run,
        result=result,
        evidence=[claim_evidence, required],
    )

    assert package.result == result


def test_structural_result_accepts_empty_claim_and_package_evidence() -> None:
    task, run, _, _ = make_contracts()
    result = validate_structural_result(
        {
            "head_sha": "def456",
            "claims": [
                {
                    "id": "C1",
                    "satisfies": ["AC1"],
                    "claim": "Structural semantic claim.",
                    "evidence": [],
                }
            ],
            "changed_files": ["src/aios_renew/artifacts.py"],
            "unresolved": [],
        }
    )

    package = validate_structural_result_package(
        task=task, run=run, result=result, evidence=[]
    )

    assert package.result.claims[0].evidence == ()
    assert package.evidence == ()


def test_canonical_package_still_rejects_structural_empty_claim_evidence() -> None:
    task, run, _, evidence = make_contracts()
    structural = Result(
        head_sha="def456",
        claims=(Claim("C1", ("AC1",), "Structural claim.", ()),),
        changed_files=("src/aios_renew/artifacts.py",),
        unresolved=(),
    )

    with pytest.raises(ArtifactValidationError, match="evidence must not be empty"):
        validate_result_package(
            task=task,
            run=run,
            result=structural,
            evidence=[evidence],
        )


@pytest.mark.parametrize("exit_code", (None, False, True, "0", 0.0))
def test_v1_evidence_decoder_rejects_invalid_raw_exit_instead_of_success(exit_code):
    import yaml
    decoded = yaml.safe_load(EVIDENCE_SOURCE)
    decoded["result"]["exit_code"] = exit_code
    with pytest.raises(ArtifactValidationError, match="exit_code must be an integer"):
        validate_evidence(decoded)


@pytest.mark.parametrize("section, field", (("result", "exit_code"), ("source", "command"), ("raw", "path")))
def test_v1_evidence_decoder_rejects_missing_proof_fields(section, field):
    import yaml
    decoded = yaml.safe_load(EVIDENCE_SOURCE)
    del decoded[section][field]
    with pytest.raises(ArtifactValidationError, match="required"):
        validate_evidence(decoded)
