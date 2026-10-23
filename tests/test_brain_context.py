"""Offline BP-3 contracts over bounded Brain Sync observations."""

from dataclasses import replace
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from aios_renew.brain_context import (
    BrainContextError,
    compose_brain_work_context,
    load_flow_cards,
    resolve_flow,
)
from aios_renew.brain_sync import BrainSyncSnapshot


def test_post_task_work_context_consumes_fresh_git_projection_and_rejects_stale_context(tmp_path):
    from tests.test_brain_sync import PostTaskGit
    from aios_renew.brain_sync import observe_brain_sync
    fixture = PostTaskGit(tmp_path).result()
    selector = dict(wake_selector=fixture.wake)
    context = compose_brain_work_context(repo=fixture.repo, post_task_selector=selector)
    portable = json.loads(context.render())
    observed = portable["canonical_observation"]
    assert observed["post_task"]["selection"] == selector
    assert observed["post_task"]["task"] == fixture.run["task"]
    assert observed["next_action"] == observed["post_task"]["next_action"] == "SEMANTIC_REVIEW"
    assert observed["unified_state"]["run_id"] == observed["post_task"]["run_id"] == fixture.run_id
    assert observed["unified_state"]["outstanding_findings"] == []
    assert "execution_base" in observed["unified_state"]
    assert observed["repository"]["root"] == "protected-repository-root"
    assert "post_task_sha256" in context.invalidation_basis
    assert len(context.render().encode()) <= 8192
    assert all(s not in context.render() for s in (str(fixture.repo), "synthetic-secret", "remote_url"))
    snapshot = observe_brain_sync(repo=fixture.repo, post_task_selector=selector)
    with pytest.raises(BrainContextError, match="fresh selector"):
        compose_brain_work_context(snapshot)
    fixture.review()
    with pytest.raises(BrainContextError, match="POST_TASK_SOURCE_MOVED"):
        context.render()
    fresh = compose_brain_work_context(repo=fixture.repo, post_task_selector=selector)
    assert fresh.canonical_observation["post_task"]["review"]["status"] == "PASS"
    assert fresh.invalidation_fingerprint != context.invalidation_fingerprint
    fresh.canonical_observation["post_task"]["review"]["status"] = "forged"
    with pytest.raises(BrainContextError, match="altered Post-TASK"):
        fresh.render()


def test_post_task_work_context_codec_enforces_portable_bound_and_selector_exclusivity(tmp_path):
    from tests.test_brain_sync import PostTaskGit
    fixture = PostTaskGit(tmp_path)
    selector = dict(task_id=fixture.task_id)
    context = compose_brain_work_context(repo=fixture.repo, post_task_selector=selector,
                                         current_request=dict(human_input="x" * 9000))
    with pytest.raises(BrainContextError, match="POST_TASK_LIMIT_EXHAUSTED"):
        context.render()
    with pytest.raises(BrainContextError, match="fresh selector"):
        compose_brain_work_context(repo=fixture.repo, post_task_selector=selector,
                                   diagnostic_selector=dict(run_id=fixture.run_id))


def test_task394_work_context_carries_one_safe_page_and_invalidates_on_page_change(tmp_path, monkeypatch):
    from aios_renew import brain_sync, brain_context
    from tests.test_failure_detail_pagination import GitFailure, summary_failure
    canonical = GitFailure(tmp_path / "page-context", *summary_failure(33))
    monkeypatch.setattr(brain_sync, "_resolve_main_sha", lambda repo: "d" * 40)
    monkeypatch.setattr(brain_sync, "_resolve_repository_name", lambda repo: "trung-via/AIOS-renew")
    selector = canonical.discover()["paging"]["commands"][0]["first_selector"]
    real = brain_sync.observe_brain_sync
    monkeypatch.setattr(brain_context, "observe_brain_sync", lambda **kw: real(**kw, canonical_sources=canonical.sources))
    first = compose_brain_work_context(repo=canonical.reader,
        diagnostic_selector={"failure_page_selector": selector}, current_request={"flow_selector": "DIAGNOSTIC"})
    later = compose_brain_work_context(repo=canonical.reader,
        diagnostic_selector={"failure_page_selector": first.canonical_observation["failure_page"]["next_selector"]},
        current_request={"flow_selector": "DIAGNOSTIC"})
    assert first.invalidation_fingerprint != later.invalidation_fingerprint
    assert "failure_page_sha256" in later.invalidation_basis
    rendered = later.render()
    assert later.as_dict()["canonical_observation"]["repository"]["root"] == "protected-repository-root"
    assert json.dumps(str(canonical.reader))[1:-1] not in rendered
    assert all(s not in rendered for s in ("synthetic-secret", "C:/private", "raw_path", "traceback"))
    assert len(later.canonical_observation["failure_page"]["page"]["failed_nodes"]) == 1
    forged = deepcopy(later.canonical_observation)
    forged["failure_page"]["page"]["failed_nodes"][0]["identity"] = "C:/private/host"
    with pytest.raises(ValueError):
        replace(later, canonical_observation=forged).render()


def test_task372_provider_neutral_work_context_carries_only_bound_safe_detail(monkeypatch, tmp_path):
    from aios_renew import brain_sync, brain_context
    from tests.test_brain_attention import Task372CanonicalSources
    from tests.test_operational_receipt import Task372Actions
    actions, canonical = Task372Actions(), Task372CanonicalSources()
    monkeypatch.setattr(brain_sync, "resolve_repository", lambda repo: tmp_path)
    monkeypatch.setattr(brain_sync, "_resolve_main_sha", lambda repo: "d" * 40)
    monkeypatch.setattr(brain_sync, "_resolve_repository_name", lambda repo: "trung-via/AIOS-renew")
    real = brain_sync.observe_brain_sync
    monkeypatch.setattr(brain_context, "observe_brain_sync", lambda **kw: real(**kw, operational_sources=actions, canonical_sources=canonical))
    context = compose_brain_work_context(repo=tmp_path, diagnostic_selector={"issue_number": actions.issue_number},
        current_request={"flow_selector": "DIAGNOSTIC"})
    exported = context.render()
    assert context.canonical_observation["diagnostics"]["operational_context"]["attempt"]["workflow_run_id"] == actions.run_id
    assert "synthetic-secret" not in exported and "raw_path" not in exported and "Traceback" not in exported
    assert "diagnostics_sha256" in context.invalidation_basis
    with pytest.raises(BrainContextError, match="invalid diagnostic selector"):
        compose_brain_work_context(repo=tmp_path, diagnostic_selector={"url": "https://attacker.invalid/private"})
    forged = deepcopy(context.canonical_observation["diagnostics"])
    forged["failure_context"]["safe_detail"]["run_id"] = "RUN-foreign-001"
    source = real(repo=tmp_path, run_id=canonical.run_id, canonical_sources=canonical)
    with pytest.raises(ValueError):
        compose_brain_work_context(replace(source, diagnostics=forged))


class CanonicalAuditRepository:
    """Canonical Git with the primary RUN/ResultPackage shape of R5 artifact
    356d320a919f0b0df5df254702e0a0c17780e042, including its admitted null head.
    """

    approval_key = "human_r21_four_specialized_activation_20261017"
    activation_states = (
        "HUMAN_APPROVED_CANONICALIZED_LIVE_PRODUCTION_CONFORMANCE_OUTSTANDING", "ACTIVE",
    )

    def __init__(self, temporary: Path):
        root = Path(__file__).resolve().parents[1]
        self.canonical = temporary / "canonical"
        self.canonical.mkdir()
        self.repo = temporary / "AIOS-renew"
        self.git(self.canonical, "init", "--initial-branch=main")
        self.git(self.canonical, "config", "user.name", "Canonical fixture")
        self.git(self.canonical, "config", "user.email", "fixture@example.invalid")
        self.git(self.canonical, "config", "core.autocrlf", "false")
        for name in (".ai/brain-audit-profiles.yaml", ".ai/brain-return-contracts.yaml", ".ai/tasks/TASK-368.yaml"):
            self.write(name, (root / name).read_bytes())
        self.base_sha = self.commit("admitted R5 base fixture")
        self.write("src/aios_renew/brain_provider.py", (root / "src/aios_renew/brain_provider.py").read_bytes())
        self.source_sha = self.commit("published R5 source fixture")
        self.git(self.canonical, "update-ref", "refs/heads/aios/review/RUN-368-001", self.source_sha)
        task = yaml.safe_load((root / ".ai/tasks/TASK-368.yaml").read_bytes())
        self.run = {"run_id": "RUN-368-001", "task": {"id": "TASK-368", "revision": 1},
                    "executor": "codex", "base_sha": self.base_sha, "head_sha": None,
                    "status": "ACTIVE", "workspace": "protected-runtime-workspace",
                    "return_affinity": {"kind": "LEGACY_REPOSITORY_DEFAULT_ROUTE"}}
        evidence_ids = [f"RUN-368-001-V{index:03}" for index in (1, 2)]
        self.result_package = {
            "result": {"head_sha": self.source_sha,
                       "claims": [{"id": f"C{index}", "satisfies": [entry["id"]],
                                   "claim": entry["condition"], "evidence": list(evidence_ids)}
                                  for index, entry in enumerate(task["acceptance"], 1)],
                       "changed_files": ["src/aios_renew/brain_provider.py"], "unresolved": []},
            "evidence": [{"evidence_id": evidence_id, "run_id": self.run["run_id"],
                          "subject_sha": self.source_sha, "type": "VERIFICATION",
                          "source": {"command": command},
                          "result": {"exit_code": 0, "summary": "Runtime verification exit code 0"},
                          "raw": {"path": f"protected-runtime-raw/{evidence_id}"}}
                         for evidence_id, command in zip(evidence_ids, task["verification"]["required"])],
        }
        self.save_artifacts()
        self.review = {"review_id": "REVIEW-368-001", "reviewed_sha": self.source_sha,
                       "mode": "PRIMARY", "verdict": "PASS", "findings": [],
                       "acceptance": {entry["id"]: "PASS" for entry in task["acceptance"]}}
        self.save_review()
        approval = deepcopy(yaml.safe_load((root / ".ai/roadmap-state.yaml").read_bytes())[self.approval_key])
        approval.update(review_decision_sha=self.review_sha, reviewed_published_source_sha=self.source_sha,
                        approved_against_main_sha=self.source_sha)
        self.roadmap = {self.approval_key: approval}
        self.save_roadmap()
        # Real ingress compares local TASK bytes with canonical Git bytes.
        self.git(temporary, "clone", "--config", "core.autocrlf=false", str(self.canonical), str(self.repo))

    @staticmethod
    def git(repo, *args, input=None):
        # Match Runtime's byte-exact Git input: text mode translates tree-record
        # LF delimiters to CRLF on Windows, adding carriage returns to paths.
        output = subprocess.run(("git", "-C", str(repo), *args), check=True, capture_output=True,
                                input=None if input is None else input.encode("utf-8")).stdout
        return output.decode("utf-8").strip()

    def write(self, name, raw):
        path = self.canonical / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def commit(self, message):
        self.git(self.canonical, "add", "-A")
        self.git(self.canonical, "commit", "--allow-empty", "-m", message)
        return self.git(self.canonical, "rev-parse", "HEAD")

    def save_roadmap(self, raw=None):
        self.write(".ai/roadmap-state.yaml", raw if raw is not None else yaml.safe_dump(self.roadmap).encode())
        self.main_sha = self.commit("canonical planning fixture")

    def save_review(self, *, run_id="RUN-368-001"):
        self.git(self.canonical, "switch", "--detach", self.source_sha)
        self.write(f".ai/reviews/{self.review['review_id']}.yaml", yaml.safe_dump(self.review).encode())
        self.review_sha = self.commit("canonical R5 PRIMARY review fixture")
        self.git(self.canonical, "update-ref", f"refs/heads/aios/review-decision/{run_id}", self.review_sha)
        self.git(self.canonical, "switch", "main")

    def save_artifacts(self, *, run_id="RUN-368-001", repair=None, failure=None):
        # Runtime artifacts are an independent immutable snapshot, not a commit
        # on the candidate or review branch. Keep that separation in the fixture.
        entries = []
        documents = [("run.json", self.run), ("failure.json", failure) if failure is not None
                     else ("result.json", self.result_package)]
        if repair is not None:
            documents.append(("repair.json", repair))
        for name, document in documents:
            sha = self.git(self.canonical, "hash-object", "-w", "--stdin", input=json.dumps(document))
            entries.append(f"100644 blob {sha}\t{name}\n")
        tree = self.git(self.canonical, "mktree", input="".join(entries))
        tree = self.git(self.canonical, "mktree", input=f"040000 tree {tree}\ttransport\n")
        tree = self.git(self.canonical, "mktree", input=f"040000 tree {tree}\t.ai\n")
        self.artifact_sha = self.git(self.canonical, "commit-tree", tree, "-m", "canonical R5 artifacts fixture")
        kind = "failure-artifacts" if failure is not None else "artifacts"
        self.git(self.canonical, "update-ref", f"refs/heads/aios/{kind}/{run_id}", self.artifact_sha)

    def publish_successor(self, *, omit_reviewed_body=False, activation_record=None):
        """Offline independently reviewed successor publication and later Human approval.

        This prepares temporary Git fixtures only; it never changes repository
        policy or stands in for the actual Runtime/Reviewer/Publisher outcome.
        """
        from aios_renew.brain_audit import _specialized_profiles
        from aios_renew.brain_context import _digest, _R21_SUCCESSOR_APPROVAL
        root = Path(__file__).resolve().parents[1]
        original = {key: deepcopy(getattr(self, key)) for key in (
            "base_sha", "source_sha", "review_sha", "artifact_sha", "run", "result_package", "review")}
        task_raw = (root / ".ai/tasks/TASK-392.yaml").read_bytes()
        task = yaml.safe_load(task_raw)
        self.base_sha = self.main_sha
        registry_raw = (root / ".ai/brain-audit-profiles.yaml").read_bytes()
        if omit_reviewed_body:
            registry = yaml.safe_load(registry_raw)
            registry["profiles"] = [p for p in registry["profiles"] if p["id"] != "tca-2"]
            self.write(".ai/brain-audit-profiles.yaml", yaml.safe_dump(registry).encode())
        self.write(".ai/tasks/TASK-392.yaml", task_raw)
        self.source_sha = self.commit("successor implementation fixture")
        self.successor_source_sha = self.source_sha
        self.git(self.canonical, "update-ref", "refs/heads/aios/review/RUN-392-001", self.source_sha)
        self.run = {**deepcopy(original["run"]), "run_id": "RUN-392-001", "task": {"id": "TASK-392", "revision": 1},
                    "base_sha": self.base_sha}
        evidence_id = "RUN-392-001-V001"
        self.result_package = {
            "result": {"head_sha": self.source_sha,
                "claims": [{"id": f"C{i}", "satisfies": [c["id"]], "claim": c["condition"], "evidence": [evidence_id]}
                           for i, c in enumerate(task["acceptance"], 1)],
                "changed_files": [".ai/brain-audit-profiles.yaml"], "unresolved": []},
            "evidence": [{"evidence_id": evidence_id, "run_id": self.run["run_id"], "subject_sha": self.source_sha,
                "type": "VERIFICATION", "source": {"command": task["verification"]["required"][0]},
                "result": {"exit_code": 0, "summary": "Synthetic Runtime fixture"},
                "raw": {"path": "protected-runtime-raw/successor"}}]}
        self.save_artifacts(run_id="RUN-392-001")
        self.successor_artifact_sha = self.artifact_sha
        self.review = {"review_id": "REVIEW-392-001", "reviewed_sha": self.source_sha, "mode": "PRIMARY",
                       "verdict": "PASS", "findings": [], "acceptance": {c["id"]: "PASS" for c in task["acceptance"]}}
        self.save_review(run_id="RUN-392-001")
        self.successor_review_sha = self.review_sha
        if omit_reviewed_body:
            self.write(".ai/brain-audit-profiles.yaml", registry_raw)
            self.commit("registry added after reviewed publication")
        approval = deepcopy(activation_record if activation_record is not None else self.roadmap[self.approval_key])
        if activation_record is None:
            approval.update(approved_on="2026-10-22", implementation_task="TASK-392", implementation_revision=1,
                implementation_run="RUN-392-001", review_id="REVIEW-392-001",
                human_decision="ACTIVATE_R21_TCA2_ROA1R3_WITH_UNCHANGED_AOA_MOA_AND_HUMAN_V1_OPTION",
                target_profiles=_specialized_profiles(successors=True), supersedes=self.approval_key)
        # TASK-395 tests consume the actual approved record. Only Git object
        # bindings change to address this independently authenticated offline repo.
        approval.update(review_decision_sha=self.review_sha,
            reviewed_published_source_sha=self.source_sha, approved_against_main_sha=self.source_sha,
            superseded_approval_digest=_digest(self.roadmap[self.approval_key]))
        self.roadmap[_R21_SUCCESSOR_APPROVAL] = approval
        self.save_roadmap()
        for key, value in original.items():
            setattr(self, key, value)
        return approval

    def publish_tca3_successor(self, *, omit_reviewed_body=False):
        """Isolated historical four-flow scenario, not the real TASK-405 outcome."""
        from aios_renew.brain_audit import _specialized_profiles
        from aios_renew.brain_context import (
            _digest, _R21_SUCCESSOR_APPROVAL, _R21_TCA3_APPROVAL, _TCA3_PRIORITY_RECORD,
        )
        root = Path(__file__).resolve().parents[1]
        if _R21_SUCCESSOR_APPROVAL not in self.roadmap:
            self.publish_successor()
        original = {key: deepcopy(getattr(self, key)) for key in (
            "base_sha", "source_sha", "review_sha", "artifact_sha", "run", "result_package", "review")}
        # This historical approval and RUN bind r1; the repository TASK is now
        # r2. Keep the synthetic reviewed candidate consistent with that identity.
        task = {**yaml.safe_load((root / ".ai/tasks/TASK-405.yaml").read_bytes()), "revision": 1}
        task_raw = yaml.safe_dump(task).encode()
        self.base_sha = self.main_sha
        registry_raw = (root / ".ai/brain-audit-profiles.yaml").read_bytes()
        if omit_reviewed_body:
            registry = yaml.safe_load(registry_raw)
            registry["profiles"] = [p for p in registry["profiles"] if p["id"] != "tca-3"]
            self.write(".ai/brain-audit-profiles.yaml", yaml.safe_dump(registry).encode())
        else:
            self.write(".ai/brain-audit-profiles.yaml", registry_raw)
        self.write(".ai/tasks/TASK-405.yaml", task_raw)
        priority = yaml.safe_load((root / ".ai/roadmap-state.yaml").read_bytes())[_TCA3_PRIORITY_RECORD]
        self.roadmap[_TCA3_PRIORITY_RECORD] = deepcopy(priority)
        self.write(".ai/roadmap-state.yaml", yaml.safe_dump(self.roadmap).encode())
        self.source_sha = self.commit("tca-3 successor implementation fixture")
        self.tca3_source_sha = self.source_sha
        self.git(self.canonical, "update-ref", "refs/heads/aios/review/RUN-405-001", self.source_sha)
        self.run = {**deepcopy(original["run"]), "run_id": "RUN-405-001", "task": {"id": "TASK-405", "revision": 1},
                    "base_sha": self.base_sha}
        evidence_ids = [f"RUN-405-001-V{index:03}" for index in range(1, len(task["verification"]["required"]) + 1)]
        self.result_package = {
            "result": {"head_sha": self.source_sha,
                "claims": [{"id": f"C{i}", "satisfies": [c["id"]], "claim": c["condition"], "evidence": list(evidence_ids)}
                           for i, c in enumerate(task["acceptance"], 1)],
                "changed_files": [".ai/brain-audit-profiles.yaml"], "unresolved": []},
            "evidence": [{"evidence_id": evidence_id, "run_id": self.run["run_id"], "subject_sha": self.source_sha,
                "type": "VERIFICATION", "source": {"command": command},
                "result": {"exit_code": 0, "summary": "Synthetic Runtime fixture"},
                "raw": {"path": f"protected-runtime-raw/{evidence_id}"}}
                for evidence_id, command in zip(evidence_ids, task["verification"]["required"])]}
        self.save_artifacts(run_id="RUN-405-001")
        self.tca3_artifact_sha = self.artifact_sha
        self.review = {"review_id": "REVIEW-405-001", "reviewed_sha": self.source_sha, "mode": "PRIMARY",
                       "verdict": "PASS", "findings": [], "acceptance": {c["id"]: "PASS" for c in task["acceptance"]}}
        self.save_review(run_id="RUN-405-001")
        self.tca3_review_sha = self.review_sha
        if omit_reviewed_body:
            self.write(".ai/brain-audit-profiles.yaml", registry_raw)
            self.commit("tca-3 registry added after reviewed publication")
        approval = deepcopy(self.roadmap[_R21_SUCCESSOR_APPROVAL])
        approval.update(
            approved_on="2026-10-23", status="ACTIVE",
            implementation_task="TASK-405", implementation_revision=1,
            implementation_run="RUN-405-001", review_id="REVIEW-405-001", review_verdict="PASS",
            human_decision="ACTIVATE_R21_TCA3_WITH_UNCHANGED_AOA_MOA_ROA1R3_AND_HUMAN_V1_OPTION",
            target_profiles=_specialized_profiles(tca3=True),
            supersedes=_R21_SUCCESSOR_APPROVAL,
            superseded_approval_digest=_digest(self.roadmap[_R21_SUCCESSOR_APPROVAL]),
            review_decision_sha=self.review_sha,
            reviewed_published_source_sha=self.source_sha,
            approved_against_main_sha=self.source_sha,
        )
        self.roadmap[_R21_TCA3_APPROVAL] = approval
        self.save_roadmap()
        for key, value in original.items():
            setattr(self, key, value)
        return approval


@pytest.fixture
def canonical_audit_repo(tmp_path):
    return CanonicalAuditRepository(tmp_path)


@pytest.fixture(scope="module")
def task395_activation_record():
    from aios_renew.brain_context import _R21_SUCCESSOR_APPROVAL, _UniqueKeyLoader
    root = Path(__file__).resolve().parents[1]
    roadmap = yaml.compose((root / ".ai/roadmap-state.yaml").read_bytes())
    keys = [key.value for key, _ in roadmap.value]
    assert len(keys) == len(set(keys))
    records = [node for key, node in roadmap.value if key.value == _R21_SUCCESSOR_APPROVAL]
    assert len(records) == 1
    return yaml.load(yaml.serialize(records[0]), Loader=_UniqueKeyLoader)


@pytest.fixture
def task395_canonical_audit_repo(canonical_audit_repo, task395_activation_record):
    canonical_audit_repo.publish_successor(activation_record=task395_activation_record)
    return canonical_audit_repo


def test_task395_candidate_binds_exact_published_source_and_preserves_original_approval(task395_activation_record):
    from aios_renew.brain_audit import parse_profile_registry, profile_ref
    from aios_renew.brain_context import _digest
    root = Path(__file__).resolve().parents[1]
    approval = task395_activation_record
    source = "86e9a3287b1a5b336275a28c173cf835e9c2366d"
    assert approval["authority"] == "EXPLICIT_HUMAN_BRAIN_PROSPECTIVE_POLICY_APPROVAL"
    assert approval["approved_on"] == "2026-10-21" and approval["status"] == "ACTIVE"
    assert approval["human_decision"] == "ACTIVATE_R21_TCA2_ROA1R3_WITH_UNCHANGED_AOA_MOA_AND_HUMAN_V1_OPTION"
    assert (approval["implementation_task"], approval["implementation_revision"], approval["implementation_run"],
            approval["review_id"], approval["review_verdict"]) == (
                "TASK-392", 1, "RUN-392-001", "REVIEW-392-001", "PASS")
    assert approval["review_decision_sha"] == "c2e28435c0f98268421ad2b23233acdd0b949d9f"
    assert approval["reviewed_published_source_sha"] == approval["approved_against_main_sha"] == source
    assert (approval["target_policy_format"], approval["target_policy_version"], approval["target_policy_state"]) == (
        "AIOS_BRAIN_AUDIT_POLICY", 1, "FOUR_SPECIALIZED")
    subprocess.run(("git", "-C", str(root), "merge-base", "--is-ancestor", source, "HEAD"), check=True)
    published = subprocess.check_output(("git", "-C", str(root), "show", f"{source}:.ai/roadmap-state.yaml"))
    candidate = (root / ".ai/roadmap-state.yaml").read_bytes().replace(b"\r\n", b"\n")
    # Keep the historical approval byte-exact without freezing future lawful
    # Human/Brain changes to unrelated roadmap entries.
    historical_records = []
    for raw in (published.replace(b"\r\n", b"\n"), candidate):
        text = raw.decode("utf-8")
        for key, node in yaml.compose(text).value:
            if key.value == CanonicalAuditRepository.approval_key:
                historical_records.append(text[key.start_mark.index:node.end_mark.index])
    assert len(historical_records) == 2 and historical_records[0] == historical_records[1]
    original = yaml.safe_load(published)[CanonicalAuditRepository.approval_key]
    assert approval["supersedes"] == CanonicalAuditRepository.approval_key
    assert approval["superseded_approval_digest"] == _digest(original) == (
        "8db137a966ba2f3047cbf133b4d9513ecb918e80fb9a86d897098a377cce40a9")
    registry = parse_profile_registry(subprocess.check_output((
        "git", "-C", str(root), "show", f"{source}:.ai/brain-audit-profiles.yaml")))
    refs = {p["id"]: profile_ref(p) for p in registry["profiles"]}
    assert approval["target_profiles"] == {flow: refs[identity] for flow, identity in (
        ("ARCHITECTURE", "aoa-1"), ("TASK_AUTHORING", "tca-2"),
        ("REMEDIATION_AUTHORING", "moa-1"), ("REPAIR_AUTHORING", "roa-1-r3"))}
    for key in ("architecture_and_task_v1_option", "correction_v1_option"):
        assert approval[key] == original[key]


@pytest.mark.parametrize("status", CanonicalAuditRepository.activation_states)
def test_canonical_audit_inputs_authenticate_git_source_and_exact_r5_bindings(canonical_audit_repo, status):
    from aios_renew.brain_context import canonical_audit_inputs
    from aios_renew.brain_provider_protocol import construct_request
    from aios_renew.brain_return_contract import parse_return_contract_registry
    from test_brain_provider_protocol import production_inputs, digest

    root = Path(__file__).resolve().parents[1]
    registry = parse_return_contract_registry((root / ".ai/brain-return-contracts.yaml").read_bytes())
    approval = canonical_audit_repo.roadmap[canonical_audit_repo.approval_key]
    approval["status"] = status
    canonical_audit_repo.save_roadmap()
    run = json.loads(canonical_audit_repo.git(canonical_audit_repo.canonical, "show",
                     f"{canonical_audit_repo.artifact_sha}:.ai/transport/run.json"))
    package_data = json.loads(canonical_audit_repo.git(canonical_audit_repo.canonical, "show",
                              f"{canonical_audit_repo.artifact_sha}:.ai/transport/result.json"))
    assert run["status"] == "ACTIVE" and run["head_sha"] is None
    assert "head_sha" not in package_data
    assert package_data["result"]["head_sha"] == canonical_audit_repo.source_sha
    assert all(item["subject_sha"] == canonical_audit_repo.source_sha for item in package_data["evidence"])
    supplied, contract, bindings, sources = production_inputs(registry, "ARCHITECTURE", canonical_audit_repo)
    policy, package, authenticate = canonical_audit_inputs(supplied, contract, bindings,
                                                         repo=canonical_audit_repo.repo, **sources)
    assert policy["human_authority_digest"] == digest(approval)
    assert policy["r5_publication_sha"] == canonical_audit_repo.source_sha
    assert policy["main_sha"] == canonical_audit_repo.main_sha
    req = construct_request(supplied, contract, bindings, package, request_mode="PROSPECTIVE",
                            audit_policy=policy, selection_authenticator=authenticate, **sources)
    assert authenticate(req["audit_selection"], "CONTINUE") is True
    # A normalized/self-rehashed declaration is still not source authentication.
    for key in ("packet_fingerprint", "subject_fingerprint", "source_context_fingerprint",
                "work_context_fingerprint", "canonical_facts_fingerprint", "external_bindings",
                "audit_profile_ref", "return_contract_ref", "selected_flow"):
        changed = deepcopy(req["audit_selection"])
        changed[key] = "f" * 64
        assert authenticate(changed, "CONTINUE") is False
    changed = deepcopy(req["audit_selection"])
    changed["policy"]["human_authority_digest"] = "f" * 64
    assert authenticate(changed, "ADMIT") is False
    assert authenticate(req["audit_selection"], "UNKNOWN") is False
    # Editing only the checkout cannot alter canonical policy or profile content.
    (canonical_audit_repo.repo / ".ai/roadmap-state.yaml").write_text("forged: true\n", encoding="utf-8")
    (canonical_audit_repo.repo / ".ai/brain-audit-profiles.yaml").write_text("forged: true\n", encoding="utf-8")
    assert authenticate(req["audit_selection"], "CONTINUE") is True
    # Even the same Git content at a different transport binding cannot replace
    # the canonical source of an admitted invocation.
    canonical_audit_repo.git(canonical_audit_repo.repo, "config", "remote.origin.url",
                             str(canonical_audit_repo.canonical) + "/.")
    assert authenticate(req["audit_selection"], "CONTINUE") is False


def snapshot(action="EXECUTE_PRIMARY", *, blocker=None, status="SELECTED", roadmap=None):
    unified = None if status != "SELECTED" else {
        "format": "AIOS_UNIFIED_STATE", "version": 1,
        "task": {"id": "TASK-177", "revision": 2},
        "next_action": action, "run_id": "RUN-177-001",
        "finding_id": "FINDING-177-001", "blocker": blocker,
    }
    return BrainSyncSnapshot(
        repository={"root": str(Path(__file__).resolve().parents[1]),
                    "name": "AIOS-renew", "main_sha": "a" * 40,
                    "remote": "origin", "remote_url": "https://secret@example.test/repo"},
        main_sha="a" * 40,
        roadmap=roadmap or {"present": True, "next_items": ["bp-3"]},
        selection_status=status, lifecycle_state="READY" if blocker is None else "BLOCKED",
        next_action=action, authority="REVIEWER" if action == "SEMANTIC_REVIEW" else "BRAIN",
        selected_task={"id": "TASK-177", "revision": 2} if unified else None,
        unified_state=unified, blocker=blocker,
    )


@pytest.mark.parametrize("action,expected", [
    ("EXECUTE_PRIMARY", "NONE"), ("EXECUTE_REMEDIATION", "NONE"),
    ("EXECUTE_REPAIR", "NONE"), ("RETRY_TRANSPORT", "NONE"),
    ("RECOVER_PRIMARY", "NONE"), ("PUBLICATION", "NONE"),
    ("WAIT", "NONE"), ("DONE", "NONE"), ("NONE", "NONE"),
    ("SEMANTIC_REVIEW", "SEMANTIC_REVIEW"),
    ("AUTHOR_REMEDIATION", "REMEDIATION_AUTHORING"),
    ("AUTHOR_REPAIR", "REPAIR_AUTHORING"),
])
def test_exact_unified_state_selection(action, expected):
    source = snapshot(action)
    before = source.as_dict()
    context = compose_brain_work_context(source)
    resolution = resolve_flow(context)
    assert resolution.selected_flow == expected
    assert resolution.pending_canonical_obligation == (None if expected == "NONE" else expected)
    assert resolution.canonical_next_action == action
    assert source.as_dict() == before
    assert context.canonical_observation["next_action"] == action
    assert "remote_url" not in context.canonical_observation["repository"]
    assert "secret" not in context.render()
    for output in (context.as_dict(), resolution.as_dict()):
        assert all(output[key] is False for key in
                   ("run_created", "executor_invoked", "verification_invoked", "state_mutated"))
        json.dumps(output)


def test_unique_unauthored_next_and_other_blockers():
    blocker = {"code": "UNAUTHORED_TASK", "task_id": "TASK-999", "message": "missing"}
    context = compose_brain_work_context(snapshot("NONE", blocker=blocker, status="UNAUTHORED_TASK"))
    result = resolve_flow(context)
    assert result.selected_flow == "TASK_AUTHORING"
    assert result.selection_basis == "UNIQUE_UNAUTHORED_NEXT"
    assert result.canonical_blocker == blocker
    ambiguous = {"code": "AMBIGUOUS_NEXT", "candidates": ["a", "b"]}
    context = compose_brain_work_context(snapshot("NONE", blocker=ambiguous, status="AMBIGUOUS_NEXT"))
    assert resolve_flow(context).selected_flow == "NONE"
    assert resolve_flow(context, cards_path=None).canonical_blocker == ambiguous


def test_human_text_does_not_infer_a_flow():
    context = compose_brain_work_context(snapshot(), {"human_input": "please research, review and repair"})
    assert resolve_flow(context).selected_flow == "NONE"
    assert resolve_flow(context).requires_fresh_context_for_continuation is False


@pytest.mark.parametrize("selector", ["ARCHITECTURE", "TASK_AUTHORING", "DIAGNOSTIC", "RESEARCH"])
def test_explicit_side_flow_preserves_pending_obligation(selector):
    blocker = {"code": "CANONICAL_BLOCKER", "message": "preserve exactly"}
    source = snapshot("SEMANTIC_REVIEW", blocker=blocker)
    context = compose_brain_work_context(source, {"flow_selector": selector, "human_input": "current priority"})
    result = resolve_flow(context)
    assert result.selected_flow == selector
    assert result.authority_owner == "BRAIN"
    assert result.card["authority_owner"] == "BRAIN"
    assert result.pending_canonical_obligation == "SEMANTIC_REVIEW"
    assert result.pending_canonical_authority_owner == "REVIEWER"
    assert result.canonical_next_action == "SEMANTIC_REVIEW"
    assert result.unified_state_next_action == "SEMANTIC_REVIEW"
    assert result.canonical_blocker == blocker
    assert result.requires_fresh_context_for_continuation is True
    assert context.current_request == {"flow_selector": selector, "human_input": "current priority"}
    assert context.canonical_observation["blocker"] == blocker
    assert context.canonical_observation["unified_state"] == source.as_dict()["unified_state"]


def test_diagnostic_on_ambiguous_state():
    blocker = {"code": "AMBIGUOUS_NEXT", "candidates": ["a", "b"]}
    context = compose_brain_work_context(snapshot("NONE", blocker=blocker, status="AMBIGUOUS_NEXT"),
                                         {"flow_selector": "DIAGNOSTIC"})
    result = resolve_flow(context)
    assert result.selected_flow == "DIAGNOSTIC"
    assert result.canonical_blocker == blocker
    assert result.pending_canonical_obligation is None


def test_diagnostic_preserves_brain_sync_conflict_and_unified_projection():
    blocker = {"code": "ROADMAP_LIFECYCLE_CONFLICT", "task_id": "TASK-177"}
    source = replace(snapshot("DONE"), selection_status="ROADMAP_LIFECYCLE_CONFLICT",
                     next_action="NONE", blocker=blocker)
    context = compose_brain_work_context(source, {"flow_selector": "DIAGNOSTIC"})
    result = resolve_flow(context)
    assert result.selected_flow == "DIAGNOSTIC"
    assert result.canonical_blocker == blocker
    assert result.canonical_next_action == "NONE"
    assert result.unified_state_next_action == "DONE"


@pytest.mark.parametrize("invalid_request", [
    {}, {"flow_selector": "SEMANTIC_REVIEW"}, {"flow_selector": ""},
    {"flow_selector": None}, {"human_input": 1}, {"human_input": "x", "extra": 1},
    {"human_input": "\ud800"}, {"human_input": "🙂" * 4097},
])
def test_current_request_fails_closed(invalid_request):
    with pytest.raises(BrainContextError):
        compose_brain_work_context(snapshot(), invalid_request)


def test_utf8_bound_and_invalidation():
    source = snapshot("AUTHOR_REPAIR")
    valid = {"human_input": "🙂" * 4096}
    first = compose_brain_work_context(source, valid)
    assert len(first.current_request["human_input"].encode("utf-8")) == 16384
    assert first.invalidation_fingerprint == compose_brain_work_context(source, valid).invalidation_fingerprint
    variants = [
        compose_brain_work_context(source, {"human_input": "changed"}),
        compose_brain_work_context(replace(source, main_sha="b" * 40), valid),
        compose_brain_work_context(replace(source, roadmap={"next_items": ["other"]}), valid),
        compose_brain_work_context(replace(source, selected_task={"id": "TASK-177", "revision": 3}), valid),
        compose_brain_work_context(snapshot("SEMANTIC_REVIEW"), valid),
        compose_brain_work_context(replace(source, blocker={"code": "BLOCKED"}), valid),
    ]
    assert len({first.invalidation_fingerprint, *(item.invalidation_fingerprint for item in variants)}) == 7
    assert resolve_flow(first).invalidation_fingerprint == first.invalidation_fingerprint


def test_altered_context_fails_closed():
    context = compose_brain_work_context(snapshot())
    context.canonical_observation["roadmap"]["next_items"].append("another")
    with pytest.raises(BrainContextError):
        resolve_flow(context)


@pytest.mark.parametrize("operation,artifact_key", [
    ("AUTHOR_REPAIR", "artifacts_sha"), ("AUTHOR_REMEDIATION", "review_decision_sha"),
])
def test_exact_correction_artifact_identity_invalidates_work_context(operation, artifact_key):
    source = replace(snapshot(operation), roadmap={}, correction_subject={
        "policy": "EXACT_CORRECTION_SUBJECT_LINEAGE_V1", "operation": operation,
        "task": {"id": "TASK-177", "revision": 2}, artifact_key: "b" * 40,
    })
    first = compose_brain_work_context(source)
    moved = compose_brain_work_context(replace(source, correction_subject={
        **source.correction_subject, artifact_key: "d" * 40}))
    assert first.canonical_observation["roadmap"] == {}
    assert first.canonical_observation["selected_task"] == {"id": "TASK-177", "revision": 2}
    assert first.invalidation_fingerprint != moved.invalidation_fingerprint
    assert resolve_flow(first).selected_flow == operation.replace("AUTHOR_", "") + "_AUTHORING"
    first.canonical_observation["correction_subject"][artifact_key] = "d" * 40
    with pytest.raises(BrainContextError, match="stale or altered"):
        resolve_flow(first)


def test_checkout_root_and_remote_alias_are_not_semantic_identity():
    source = snapshot("AUTHOR_REPAIR")
    human_request = {"flow_selector": "DIAGNOSTIC", "human_input": "inspect"}
    first = compose_brain_work_context(source, human_request)
    other = replace(source, repository={**source.repository, "root": "/other/checkout",
                                        "remote": "upstream", "remote_url": "https://other.example/repo"})
    second = compose_brain_work_context(other, human_request)
    assert first.invalidation_basis == second.invalidation_basis
    assert first.invalidation_fingerprint == second.invalidation_fingerprint
    assert first.canonical_observation["repository"] != second.canonical_observation["repository"]


def test_resolver_uses_observed_repository_registry(tmp_path: Path):
    source = snapshot("SEMANTIC_REVIEW")
    repo = tmp_path / "checkout"
    registry = repo / ".ai" / "flow-cards.yaml"
    registry.parent.mkdir(parents=True)
    source_cards = Path(__file__).resolve().parents[1] / ".ai" / "flow-cards.yaml"
    registry.write_text(source_cards.read_text(encoding="utf-8"), encoding="utf-8")
    context = compose_brain_work_context(replace(source, repository={**source.repository, "root": str(repo)}))
    assert resolve_flow(context).selected_flow == "SEMANTIC_REVIEW"
    assert resolve_flow(context, cards_path=registry).selected_flow == "SEMANTIC_REVIEW"
    unrelated = tmp_path / "unrelated.yaml"
    unrelated.write_text(registry.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(BrainContextError, match="unrelated"):
        resolve_flow(context, cards_path=unrelated)
    registry.unlink()
    with pytest.raises(BrainContextError, match="cannot load"):
        resolve_flow(context)


def test_altered_operational_root_cannot_redirect_registry(tmp_path: Path):
    context = compose_brain_work_context(snapshot())
    context.canonical_observation["repository"]["root"] = str(tmp_path)
    with pytest.raises(BrainContextError, match="altered"):
        resolve_flow(context)


def test_composition_and_resolution_have_no_mutating_call_path(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected mutation or dispatch")

    monkeypatch.setattr("aios_renew.brain_context.observe_brain_sync", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    context = compose_brain_work_context(snapshot("SEMANTIC_REVIEW"))
    assert resolve_flow(context).selected_flow == "SEMANTIC_REVIEW"


def test_registry_exact_seven_and_contracts():
    cards = load_flow_cards()
    assert set(cards) == {"ARCHITECTURE", "TASK_AUTHORING", "SEMANTIC_REVIEW",
                          "REMEDIATION_AUTHORING", "REPAIR_AUTHORING", "DIAGNOSTIC", "RESEARCH"}
    assert cards["RESEARCH"]["entry_conditions"] == ["EXPLICIT_SELECTOR"]
    assert cards["RESEARCH"]["decision_family_ref"] == "HUMAN_BRAIN_RESEARCH_ASSURANCE"
    assert cards["RESEARCH"]["handoff_target"] == "RESEARCH_PROTOCOL"
    assert cards["RESEARCH"]["expected_return_shape"] == "RESEARCH_PACKET"
    assert cards["SEMANTIC_REVIEW"]["authority_owner"] == "REVIEWER"
    assert cards["SEMANTIC_REVIEW"]["decision_family_ref"] == "review.validate_review"
    assert cards["REPAIR_AUTHORING"]["decision_family_ref"] == "publication._validate_repair_authorization"
    assert cards["REPAIR_AUTHORING"]["entry_conditions"] == [
        "UNIFIED_STATE_AUTHOR_REPAIR", "EXPLICIT_UNEXECUTED_REPAIR_SUPERSESSION"]


@pytest.mark.parametrize("action", ["AUTHOR_REPAIR", "AUTHOR_REMEDIATION", "SEMANTIC_REVIEW",
                                    "EXECUTE_PRIMARY", "EXECUTE_REMEDIATION", "WAIT", "DONE", "NONE"])
def test_explicit_repair_is_not_a_generic_override(action):
    with pytest.raises(BrainContextError, match="supersession"):
        compose_brain_work_context(snapshot(action), {"flow_selector": "REPAIR_AUTHORING"})


def test_explicit_repair_requires_canonical_material_even_in_execute_state():
    with pytest.raises(BrainContextError, match="supersession"):
        compose_brain_work_context(snapshot("EXECUTE_REPAIR"), {"flow_selector": "REPAIR_AUTHORING"})


@pytest.mark.parametrize("fault", ["main", "lifecycle", "authorization", "failure", "competing", "executed"])
def test_explicit_repair_rejects_changed_canonical_authority_before_material_compilation(monkeypatch, fault):
    from types import SimpleNamespace
    from aios_renew import authoring_ingress as ingress
    from aios_renew.review_transport import ReviewTransportError

    source = snapshot("EXECUTE_REPAIR")
    source = replace(source, unified_state={**source.unified_state,
                     "failed_run_id": "RUN-177-001", "failed_head_sha": "b" * 40,
                     "correction_sha": "c" * 40})
    repair_ref = "refs/heads/aios/repair/RUN-177-001"
    failure_ref = "refs/heads/aios/failure-artifacts/RUN-177-001"
    refs = {"refs/heads/main": source.main_sha, repair_ref: "c" * 40, failure_ref: "d" * 40}
    if fault == "main":
        refs["refs/heads/main"] = "e" * 40
    if fault == "failure":
        refs.pop(failure_ref)
    monkeypatch.setattr(ingress, "_authoring_refs", lambda repo: ("origin", refs))
    monkeypatch.setattr(ingress, "_prove_authoring_inputs", lambda *args: None)
    fresh = replace(source, next_action="WAIT") if fault == "lifecycle" else source
    monkeypatch.setattr("aios_renew.brain_sync.observe_brain_sync", lambda **kwargs: fresh)

    def authorization(*args, **kwargs):
        if fault == "competing":
            raise ReviewTransportError("competing current authorization")
        return SimpleNamespace(commit_sha="e" * 40 if fault == "authorization" else "c" * 40,
                               ref=repair_ref)

    monkeypatch.setattr(ingress, "resolve_remote_repair_authorization", authorization)

    def continuation(*args):
        if fault == "executed":
            raise ingress.AuthoringIngressError("canonical continuation already exists")

    monkeypatch.setattr(ingress, "_check_continuation_does_not_exist", continuation)
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid authority reached semantic material or mutation")
    monkeypatch.setattr(ingress, "_authoring_blob", forbidden)
    monkeypatch.setattr(ingress, "_commit_tree", forbidden)
    monkeypatch.setattr(ingress, "_publish_ingress_ref", forbidden)
    with pytest.raises(BrainContextError, match="supersession"):
        compose_brain_work_context(source, {"flow_selector": "REPAIR_AUTHORING"})


@pytest.mark.parametrize("mutation", [
    lambda data: data["cards"].pop(),
    lambda data: data["cards"].__setitem__(5, dict(data["cards"][0])),
    lambda data: data["cards"][0].__setitem__("id", "EXECUTE_PRIMARY"),
    lambda data: data["cards"][0].pop("required_context"),
    lambda data: data["cards"][0].__setitem__("authority_owner", "REVIEWER"),
    lambda data: data["cards"][2].__setitem__("decision_family_ref", "PASS_OR_FAIL"),
    lambda data: data["cards"][2].__setitem__("verdicts", ["PASS", "BLOCKED"]),
    lambda data: data["cards"][0].__setitem__("handoff_target", "EXECUTE_ANYTHING"),
    lambda data: data["cards"][0]["entry_conditions"].append("EXECUTE_PRIMARY"),
    lambda data: data["cards"][0]["required_context"].pop(),
])
def test_registry_rejects_malformed_cards(tmp_path: Path, mutation):
    registry = {"format": "AIOS_FLOW_CARDS", "version": 1,
                "cards": list(load_flow_cards().values())}
    mutation(registry)
    path = tmp_path / "cards.yaml"
    path.write_text(yaml.safe_dump(registry), encoding="utf-8")
    with pytest.raises(BrainContextError):
        load_flow_cards(path)


def test_registry_rejects_duplicate_yaml_keys(tmp_path: Path):
    path = tmp_path / "cards.yaml"
    path.write_text("format: AIOS_FLOW_CARDS\nformat: AIOS_FLOW_CARDS\n", encoding="utf-8")
    with pytest.raises(BrainContextError):
        load_flow_cards(path)


def test_fresh_failure_work_context_carries_safe_cause_and_invalidates_on_source_movement():
    from copy import deepcopy
    from aios_renew.review_transport import failure_context
    from tests.test_review_transport import _native_failure_fixture
    source = snapshot("AUTHOR_REPAIR")
    _, private = _native_failure_fixture()
    private.update(run_id="RUN-177-001", task=dict(id="TASK-177", revision=2))
    diagnostic = failure_context(private, artifact_sha="c" * 40)
    unified = dict(source.unified_state, failed_run_id=private["run_id"], failed_head_sha=private["failed_head_sha"],
                   failure_context=diagnostic)
    first = compose_brain_work_context(replace(source, unified_state=unified))
    assert first.canonical_observation["unified_state"]["failure_context"]["diagnostic"]["category"] == "NATIVE_TIMEOUT"
    assert "synthetic-secret" not in first.render()
    changed = deepcopy(unified); changed["failure_context"]["source"]["artifact_sha"] = "d" * 40
    second = compose_brain_work_context(replace(source, unified_state=changed))
    assert first.invalidation_fingerprint != second.invalidation_fingerprint
    assert resolve_flow(first).selected_flow == resolve_flow(second).selected_flow == "REPAIR_AUTHORING"


def test_task392_canonical_successor_policy_preserves_old_admission_or_stops(canonical_audit_repo):
    from aios_renew.brain_context import canonical_audit_inputs
    from aios_renew.brain_provider_protocol import construct_request
    from aios_renew.brain_return_contract import parse_return_contract_registry
    from test_brain_provider_protocol import production_inputs
    root = Path(__file__).resolve().parents[1]
    registry = parse_return_contract_registry((root / ".ai/brain-return-contracts.yaml").read_bytes())
    supplied, contract, bindings, sources = production_inputs(registry, "TASK_AUTHORING", canonical_audit_repo)
    policy, package, authenticate = canonical_audit_inputs(supplied, contract, bindings, repo=canonical_audit_repo.repo, **sources)
    assert package["audit_profile_ref"]["id"] == "tca-1"  # Nine registry entries do not activate anything.
    req = construct_request(supplied, contract, bindings, package, request_mode="PROSPECTIVE", audit_policy=policy,
                            selection_authenticator=authenticate, **sources)
    original_approval = deepcopy(canonical_audit_repo.roadmap[canonical_audit_repo.approval_key])
    canonical_audit_repo.publish_successor()
    assert canonical_audit_repo.roadmap[canonical_audit_repo.approval_key] == original_approval
    assert authenticate(req["audit_selection"], "CONTINUE") is False
    assert req["audit_profile_package"] == package and req["audit_selection"]["policy"] == policy
    with pytest.raises(BrainContextError):
        canonical_audit_inputs(supplied, contract, bindings, repo=canonical_audit_repo.repo, **sources)


@pytest.mark.parametrize("fault", ("partial", "mixed", "digest", "bool_version", "supersedes", "old_approval",
    "duplicate", "missing_review", "review_sha", "unpublished", "missing_artifact", "wrong_run", "unreviewed_body"))
def test_task392_canonical_successor_cannot_replace_authority_with_a_digest(canonical_audit_repo, fault):
    from aios_renew.brain_context import canonical_audit_inputs, _R21_SUCCESSOR_APPROVAL
    from aios_renew.brain_return_contract import parse_return_contract_registry
    from test_brain_provider_protocol import production_inputs
    root = Path(__file__).resolve().parents[1]
    canonical = canonical_audit_repo
    approval = canonical.publish_successor(omit_reviewed_body=fault == "unreviewed_body")
    if fault == "partial":
        del approval["target_profiles"]["REPAIR_AUTHORING"]
    elif fault == "mixed":
        approval["target_profiles"]["TASK_AUTHORING"] = canonical.roadmap[canonical.approval_key]["target_profiles"]["TASK_AUTHORING"]
    elif fault == "digest":
        approval["target_profiles"]["TASK_AUTHORING"]["digest"] = "f" * 64
    elif fault == "bool_version":
        approval["target_profiles"]["ARCHITECTURE"]["version"] = True
    elif fault == "supersedes":
        approval["superseded_approval_digest"] = "f" * 64
    elif fault == "old_approval":
        canonical.roadmap[canonical.approval_key]["approved_on"] = "2026-10-22"
    elif fault == "duplicate":
        canonical.roadmap[_R21_SUCCESSOR_APPROVAL + "_other"] = deepcopy(approval)
    elif fault == "missing_review":
        canonical.git(canonical.canonical, "update-ref", "-d", "refs/heads/aios/review-decision/RUN-392-001")
    elif fault == "review_sha":
        approval["review_decision_sha"] = canonical.review_sha
    elif fault == "unpublished":
        canonical.git(canonical.canonical, "switch", "--detach", canonical.source_sha)
        canonical.write(".ai/roadmap-state.yaml", yaml.safe_dump(canonical.roadmap).encode())
        unrelated = canonical.commit("approval without successor publication ancestry")
        canonical.git(canonical.canonical, "branch", "-f", "main", unrelated)
        canonical.git(canonical.canonical, "switch", "main")
    elif fault == "missing_artifact":
        canonical.git(canonical.canonical, "update-ref", "-d", "refs/heads/aios/artifacts/RUN-392-001")
    elif fault == "wrong_run":
        canonical.git(canonical.canonical, "update-ref", "refs/heads/aios/artifacts/RUN-392-001", canonical.artifact_sha)
    if fault != "unpublished":
        canonical.save_roadmap()
    else:
        canonical.main_sha = unrelated
    registry = parse_return_contract_registry((root / ".ai/brain-return-contracts.yaml").read_bytes())
    supplied, contract, bindings, sources = production_inputs(registry, "TASK_AUTHORING", canonical)
    with pytest.raises(BrainContextError):
        canonical_audit_inputs(supplied, contract, bindings, repo=canonical.repo, **sources)


def test_task405_priority_record_preserves_tca2_live_default_and_prepares_two_step_tca3_cutover(
        task395_canonical_audit_repo):
    from aios_renew.brain_audit import _TCA_V3_DIGEST, _TCA_SUCCESSOR_DIGEST
    from aios_renew.brain_context import (
        canonical_audit_inputs, _TCA3_PRIORITY_RECORD, _R21_SUCCESSOR_APPROVAL, _UniqueKeyLoader,
    )
    from aios_renew.brain_provider_protocol import construct_request
    from aios_renew.brain_return_contract import parse_return_contract_registry
    from test_brain_provider_protocol import production_inputs
    root = Path(__file__).resolve().parents[1]
    roadmap = yaml.compose((root / ".ai/roadmap-state.yaml").read_bytes())
    keys = [key.value for key, _ in roadmap.value]
    assert len(keys) == len(set(keys))
    nodes = {key.value: node for key, node in roadmap.value}
    priority = yaml.load(yaml.serialize(nodes[_TCA3_PRIORITY_RECORD]), Loader=_UniqueKeyLoader)
    r21_successor = yaml.load(yaml.serialize(nodes[_R21_SUCCESSOR_APPROVAL]), Loader=_UniqueKeyLoader)
    assert priority["authority"] == "EXPLICIT_HUMAN_PRIORITY_AND_BRAIN_PROSPECTIVE_PLANNING_TRANSCRIPTION"
    assert priority["approved_on"] == "2026-10-23"
    assert priority["planning_only"] is True
    assert priority["current_live_default_profile"] == {
        "id": "tca-2", "version": 2, "digest": _TCA_SUCCESSOR_DIGEST,
        "status": "ACTIVE_DEFAULT_UNTIL_SEPARATE_AUTHENTICATED_CUTOVER",
    }
    assert priority["prospective_successor_profile"] == {
        "id": "tca-3", "version": 3, "registry_path": ".ai/brain-audit-profiles.yaml",
        "status": "CANDIDATE_PREPARED_NOT_ACTIVE",
        "digest_pinning": "SEPARATE_POST_PUBLICATION_ACTIVATION_TASK_PINS_EXACT_REVIEWED_DIGEST",
    }
    assert priority["paused_pte"]["id"] == "post-task-dirty-recovery-pte-20261022"
    assert priority["paused_pte"]["state"] == "PAUSED_RESUMABLE_NOT_COMPLETED_OR_RETIRED"
    assert r21_successor["target_profiles"]["TASK_AUTHORING"]["id"] == "tca-2"

    # Installing the priority record on canonical main keeps tca-2 as the live default.
    canonical = task395_canonical_audit_repo
    canonical.roadmap[_TCA3_PRIORITY_RECORD] = deepcopy(priority)
    canonical.save_roadmap()
    registry = parse_return_contract_registry((root / ".ai/brain-return-contracts.yaml").read_bytes())
    supplied, contract, bindings, sources = production_inputs(registry, "TASK_AUTHORING", canonical)
    policy, package, authenticate = canonical_audit_inputs(
        supplied, contract, bindings, repo=canonical.repo, **sources)
    assert package["audit_profile_ref"]["id"] == "tca-2"
    req_tca2 = construct_request(supplied, contract, bindings, package, request_mode="PROSPECTIVE",
                                 audit_policy=policy, selection_authenticator=authenticate, **sources)
    assert req_tca2["audit_profile_package"]["audit_profile_ref"]["id"] == "tca-2"

    # Attempting to self-activate tca-3 via the planning priority record fails closed.
    for bad_field, bad_val in (
        ("planning_only", False),
        ("prospective_successor_profile", {**priority["prospective_successor_profile"], "status": "ACTIVE"}),
        ("current_live_default_profile", {"id": "tca-3", "version": 3, "digest": _TCA_V3_DIGEST}),
    ):
        tampered = deepcopy(priority)
        tampered[bad_field] = bad_val
        canonical.roadmap[_TCA3_PRIORITY_RECORD] = tampered
        canonical.save_roadmap()
        supplied_bad, contract_bad, bindings_bad, sources_bad = production_inputs(registry, "TASK_AUTHORING", canonical)
        with pytest.raises(BrainContextError):
            canonical_audit_inputs(supplied_bad, contract_bad, bindings_bad, repo=canonical.repo, **sources_bad)

    # Restore valid priority record and publish separate tca-3 activation cutover.
    canonical.roadmap[_TCA3_PRIORITY_RECORD] = deepcopy(priority)
    canonical.save_roadmap()
    tca3_approval = canonical.publish_tca3_successor()
    reviewed_task = yaml.safe_load(canonical.git(canonical.canonical, "show",
        f"{canonical.tca3_source_sha}:.ai/tasks/TASK-405.yaml"))
    assert reviewed_task["revision"] == tca3_approval["implementation_revision"] == 1
    canonical.git(canonical.canonical, "merge-base", "--is-ancestor", canonical.tca3_source_sha, "main")
    # In-flight tca-2 request stops rather than silently switching to tca-3.
    assert authenticate(req_tca2["audit_selection"], "CONTINUE") is False
    supplied3, contract3, bindings3, sources3 = production_inputs(registry, "TASK_AUTHORING", canonical)
    policy3, package3, authenticate3 = canonical_audit_inputs(
        supplied3, contract3, bindings3, repo=canonical.repo, **sources3)
    assert package3["audit_profile_ref"] == {"id": "tca-3", "version": 3, "digest": _TCA_V3_DIGEST}
    assert policy3["profiles"] == tca3_approval["target_profiles"]
    assert policy3["profiles"]["ARCHITECTURE"]["id"] == "aoa-1"
    assert policy3["profiles"]["REMEDIATION_AUTHORING"]["id"] == "moa-1"
    assert policy3["profiles"]["REPAIR_AUTHORING"]["id"] == "roa-1-r3"


@pytest.mark.parametrize("fault", (
    "partial", "mixed_tca2", "digest", "supersedes_digest", "missing_review",
    "missing_artifact", "wrong_task", "unreviewed_body",
))
def test_task405_tca3_prospective_activation_rejects_unauthenticated_or_forged_cutover(
        canonical_audit_repo, fault):
    from aios_renew.brain_context import canonical_audit_inputs
    from aios_renew.brain_return_contract import parse_return_contract_registry
    from test_brain_provider_protocol import production_inputs
    root = Path(__file__).resolve().parents[1]
    canonical = canonical_audit_repo
    approval = canonical.publish_tca3_successor(omit_reviewed_body=fault == "unreviewed_body")
    if fault == "partial":
        del approval["target_profiles"]["TASK_AUTHORING"]
    elif fault == "mixed_tca2":
        approval["target_profiles"]["TASK_AUTHORING"] = canonical.roadmap[
            "human_r21_tca_roa_successor_activation"]["target_profiles"]["TASK_AUTHORING"]
    elif fault == "digest":
        approval["target_profiles"]["TASK_AUTHORING"]["digest"] = "0" * 64
    elif fault == "supersedes_digest":
        approval["superseded_approval_digest"] = "0" * 64
    elif fault == "missing_review":
        canonical.git(canonical.canonical, "update-ref", "-d", "refs/heads/aios/review-decision/RUN-405-001")
    elif fault == "missing_artifact":
        canonical.git(canonical.canonical, "update-ref", "-d", "refs/heads/aios/artifacts/RUN-405-001")
    elif fault == "wrong_task":
        approval["implementation_task"] = "TASK-392"
    canonical.save_roadmap()
    registry = parse_return_contract_registry((root / ".ai/brain-return-contracts.yaml").read_bytes())
    supplied, contract, bindings, sources = production_inputs(registry, "TASK_AUTHORING", canonical)
    with pytest.raises(BrainContextError):
        canonical_audit_inputs(supplied, contract, bindings, repo=canonical.repo, **sources)


@pytest.fixture
def task406_canonical_audit_repo(canonical_audit_repo, monkeypatch):
    """Real offline Git proof chain. Only fixed production object IDs are rebased
    to this isolated repository; all parsing, metadata, ancestry and ref checks run.
    These synthetic Runtime/Reviewer fixtures are never verification EVIDENCE.
    """
    import aios_renew.brain_context as context
    c = canonical_audit_repo
    c.publish_successor()
    root = Path(__file__).resolve().parents[1]
    saved = {key: deepcopy(getattr(c, key)) for key in (
        "base_sha", "source_sha", "review_sha", "artifact_sha", "run", "result_package", "review")}
    task2 = yaml.safe_load((root / ".ai/tasks/TASK-405.yaml").read_bytes())
    c.write(".ai/tasks/TASK-405.yaml", yaml.safe_dump(task2).encode())
    first = c.commit("TASK-405 r2 first parent fixture")
    task1 = {**task2, "revision": 1}
    c.git(c.canonical, "switch", "--detach", first)
    for path in context._TASK405_EXCEPTION["implementation_blobs"]:
        c.write(path, yaml.safe_dump(c.roadmap).encode() if path == ".ai/roadmap-state.yaml" else (root / path).read_bytes())
    c.write(".ai/tasks/TASK-405.yaml", yaml.safe_dump(task1).encode())
    source = c.commit("TASK-405 original r1 source fixture")
    c.git(c.canonical, "update-ref", "refs/heads/aios/review/RUN-405-004", source)
    c.source_sha = source
    c.base_sha = first
    c.run = {**saved["run"], "run_id": "RUN-405-004", "base_sha": first, "task": {"id": "TASK-405", "revision": 1}}

    def package(task, run_id, sha, paths):
        ids = [f"{run_id}-V{i:03}" for i in range(1, len(task["verification"]["required"]) + 1)]
        return {"result": {"head_sha": sha, "changed_files": sorted(paths), "unresolved": [],
                    "claims": [{"id": f"C{i}", "satisfies": [ac["id"]], "claim": ac["condition"], "evidence": ids}
                               for i, ac in enumerate(task["acceptance"], 1)]},
                "evidence": [{"evidence_id": eid, "run_id": run_id, "subject_sha": sha, "type": "VERIFICATION",
                    "source": {"command": command}, "result": {"exit_code": 0, "summary": "Synthetic Runtime fixture"},
                    "raw": {"path": f"fixture-only/{eid}"}}
                    for eid, command in zip(ids, task["verification"]["required"])]}

    c.result_package = package(task1, "RUN-405-004", source, context._TASK405_EXCEPTION["implementation_blobs"])
    c.result_package["result"]["claims"][1]["claim"] = "Original false 98304-byte registry bound."
    c.save_artifacts(run_id="RUN-405-004")
    artifact = c.artifact_sha
    c.review = {"review_id": "REVIEW-405-001", "reviewed_sha": source, "mode": "PRIMARY", "verdict": "CHANGES_REQUIRED",
                "acceptance": {f"AC{i}": "FAIL" if i == 10 else "PASS" for i in range(1, 11)},
                "findings": [{"id": "F1", "basis": "AC10", "action": "EVIDENCE_ONLY", "location": "C2",
                              "issue": "False immutable claim", "expected": "Truthful claim without rewriting history"}]}
    c.save_review(run_id="RUN-405-004")
    original_review = c.review_sha
    c.review = {"review_id": "REVIEW-405-002", "reviewed_sha": source, "mode": "DELTA", "verdict": "BLOCKED",
                "prior_finding_id": "F1", "acceptance": {"AC10": "FAIL"}, "findings": []}
    c.save_review(run_id="RUN-405-005")
    delta = c.review_sha
    # Integrate all twelve exact implementation blobs, retaining main's TASK r2.
    for path in context._TASK405_EXCEPTION["implementation_blobs"]:
        c.write(path, subprocess.check_output(("git", "-C", str(c.canonical), "show", f"{source}:{path}")))
    c.git(c.canonical, "add", "-A")
    tree = c.git(c.canonical, "write-tree")
    merge = c.git(c.canonical, "commit-tree", tree, "-p", first, "-p", source, "-m", "Human Git exception fixture")
    c.git(c.canonical, "update-ref", "refs/heads/main", merge)
    c.git(c.canonical, "reset", "--hard", merge)
    exception = deepcopy(context._TASK405_EXCEPTION)
    exception.update(merge_sha=merge, parents=[first, source], tree=tree, source_sha=source,
                     original_artifacts_sha=artifact, original_review_sha=original_review, delta_review_sha=delta,
                     implementation_blobs={p: c.git(c.canonical, "rev-parse", f"{source}:{p}")
                                           for p in exception["implementation_blobs"]})
    monkeypatch.setattr(context, "_TASK405_EXCEPTION", exception)
    predecessor = context._digest(c.roadmap[context._R21_SUCCESSOR_APPROVAL])
    monkeypatch.setattr(context, "_THREE_FLOW_PREDECESSOR_DIGEST", predecessor)
    task = yaml.safe_load((root / ".ai/tasks/TASK-406.yaml").read_bytes())
    c.write(".ai/tasks/TASK-406.yaml", yaml.safe_dump(task).encode())
    root_base = c.commit("TASK-406 authored base fixture")
    context_raw = (root / "src/aios_renew/brain_context.py").read_bytes()
    # This fixture is the archived TASK-406 publication, before TASK-408 code.
    context_raw = context_raw.replace(context._ON_DEMAND_APPROVAL.encode(), b"archived-on-demand-marker")
    c.write("src/aios_renew/brain_context.py", context_raw + b"\n# failed candidate fixture\n")
    failed = c.commit("TASK-406 failed PRIMARY candidate fixture")
    c.git(c.canonical, "update-ref", "refs/heads/aios/failure/RUN-406-002", failed)
    c.run = {**saved["run"], "run_id": "RUN-406-002", "base_sha": root_base,
             "task": {"id": "TASK-406", "revision": 2}}
    failure = {"kind": "FAILURE", "run_id": c.run["run_id"], "task": c.run["task"],
               "executor": "codex", "base_sha": root_base, "failed_head_sha": failed,
               "phase": "VERIFICATION", "error": {"type": "RuntimeVerificationError", "message": "Synthetic failure"},
               "candidate": {"changed_files": ["src/aios_renew/brain_context.py"], "dirty": False,
                             "descends_from_base": True, "transportable": True, "repairable": True,
                             "outside_task_scope": []}}
    from aios_renew.review_transport import _public_metadata_value
    failure = _public_metadata_value(failure)
    c.save_artifacts(run_id="RUN-406-002", failure=failure)
    failure_artifacts = c.artifact_sha
    authorization = {"repair_id": "REPAIR-406-002-001", "failed_run_id": "RUN-406-002", "failed_head_sha": failed,
                     "task": c.run["task"], "action": "CODE_FIX", "modification_scope": task["scope"]["modify"],
                     "instructions": ["Correct the bounded failed candidate fixture"], "constraints": []}
    c.git(c.canonical, "switch", "--detach", failed)
    c.write(".ai/transport/repair.json", json.dumps(authorization).encode())
    authorization_sha = c.commit("canonical repair authorization fixture")
    c.git(c.canonical, "update-ref", "refs/heads/aios/repair/RUN-406-002", authorization_sha)
    c.git(c.canonical, "switch", "main")
    monkeypatch.setattr(context, "_THREE_FLOW_REPAIR", {
        "failed_run_id": "RUN-406-002", "failed_head_sha": failed, "failure_artifacts_sha": failure_artifacts,
        "repair_id": authorization["repair_id"], "action": "CODE_FIX",
        "repair_authorization_sha": authorization_sha, "root_base_sha": root_base})
    approval = context._three_flow_approval(predecessor)
    c.roadmap[context._THREE_FLOW_APPROVAL] = approval
    c.write(".ai/roadmap-state.yaml", yaml.safe_dump(c.roadmap).encode())
    c.write("src/aios_renew/brain_context.py", context_raw)
    c.source_sha = c.commit("TASK-406 reviewed implementation fixture")
    c.three_flow_source_sha = c.source_sha
    c.main_sha = c.source_sha
    c.git(c.canonical, "update-ref", context._THREE_FLOW_REFS[0], c.source_sha)
    c.run = {**saved["run"], "run_id": context._THREE_FLOW_RUN, "base_sha": failed, "task": {"id": "TASK-406", "revision": 2}}
    c.three_flow_lineage = {"failed_run_id": "RUN-406-002", "failed_head_sha": failed,
        "root_base_sha": root_base, "result_base_sha": root_base, "repair_authorization_sha": authorization_sha,
        "task": task, "failure": failure, "repair": authorization, "run": deepcopy(c.run)}
    c.result_package = package(task, context._THREE_FLOW_RUN, c.source_sha, ["src/aios_renew/brain_context.py"])
    c.save_artifacts(run_id=context._THREE_FLOW_RUN, repair=c.three_flow_lineage)
    c.three_flow_artifact_sha = c.artifact_sha
    c.review = {"review_id": "REVIEW-406-001", "reviewed_sha": c.source_sha, "mode": "PRIMARY", "verdict": "PASS",
                "acceptance": {ac["id"]: "PASS" for ac in task["acceptance"]}, "findings": []}
    c.save_review(run_id=context._THREE_FLOW_RUN)
    c.three_flow_review_sha = c.review_sha
    c.three_flow_review = deepcopy(c.review)
    c.three_flow_package = deepcopy(c.result_package)
    c.three_flow_run = deepcopy(c.run)
    c.exception = exception
    c.synthetic_package = package
    for key, value in saved.items():
        setattr(c, key, value)
    return c


@pytest.fixture
def task408_canonical_audit_repo(task406_canonical_audit_repo, monkeypatch):
    """Synthetic lifecycle objects in isolated Git, never verification proof."""
    import aios_renew.brain_context as context
    c = task406_canonical_audit_repo
    root = Path(__file__).resolve().parents[1]
    task = yaml.safe_load((root / ".ai/tasks/TASK-408.yaml").read_bytes())
    c.write(".ai/tasks/TASK-408.yaml", yaml.safe_dump(task).encode())
    from tests.test_brain_audit import task405_tca3_context
    audited_task = task405_tca3_context()[1]["task"]
    c.write(f".ai/tasks/{audited_task['task_id']}.yaml", yaml.safe_dump(audited_task).encode())
    c.on_demand_base_sha = c.commit("TASK-408 exact authored fixture")
    c.write("src/aios_renew/brain_context.py", b"# TASK-408 failed candidate fixture\n")
    failed = c.commit("TASK-408 failed PRIMARY fixture")
    c.git(c.canonical, "update-ref", "refs/heads/aios/failure/RUN-408-001", failed)
    c.run = {**c.three_flow_run, "run_id": "RUN-408-001", "base_sha": c.on_demand_base_sha,
             "task": {"id": "TASK-408", "revision": 1}}
    failure = {"kind": "FAILURE", "run_id": c.run["run_id"], "task": c.run["task"],
               "executor": "codex", "base_sha": c.on_demand_base_sha, "failed_head_sha": failed,
               "phase": "VERIFICATION", "error": {"type": "RuntimeVerificationError", "message": "Synthetic failure"},
               "candidate": {"changed_files": ["src/aios_renew/brain_context.py"], "dirty": False,
                             "descends_from_base": True, "transportable": True, "repairable": True,
                             "outside_task_scope": []}}
    from aios_renew.review_transport import _public_metadata_value
    failure = _public_metadata_value(failure)
    c.save_artifacts(run_id="RUN-408-001", failure=failure)
    failure_artifacts = c.artifact_sha
    authorization = {"repair_id": "REPAIR-408-001-001", "failed_run_id": "RUN-408-001", "failed_head_sha": failed,
                     "task": c.run["task"], "action": "CODE_FIX", "modification_scope": task["scope"]["modify"],
                     "instructions": ["Correct the exact failed TASK-408 fixture"], "constraints": []}
    c.git(c.canonical, "switch", "--detach", failed)
    c.write(".ai/transport/repair.json", json.dumps(authorization).encode())
    authorization_sha = c.commit("TASK-408 admitted repair authorization fixture")
    c.git(c.canonical, "update-ref", "refs/heads/aios/repair/RUN-408-001", authorization_sha)
    c.git(c.canonical, "switch", "main")
    monkeypatch.setattr(context, "_ON_DEMAND_REPAIR", {
        "failed_run_id": "RUN-408-001", "failed_head_sha": failed, "failure_artifacts_sha": failure_artifacts,
        "repair_id": authorization["repair_id"], "action": "CODE_FIX", "repair_authorization_sha": authorization_sha,
        "root_base_sha": c.on_demand_base_sha})
    c.roadmap[context._ON_DEMAND_APPROVAL] = context._on_demand_approval(
        context._digest(c.roadmap[context._THREE_FLOW_APPROVAL]))
    c.write(".ai/roadmap-state.yaml", yaml.safe_dump(c.roadmap).encode())
    c.write("src/aios_renew/brain_context.py", (root / "src/aios_renew/brain_context.py").read_bytes())
    c.source_sha = c.commit("TASK-408 prospective implementation fixture")
    c.main_sha = c.on_demand_source_sha = c.source_sha
    c.git(c.canonical, "update-ref", context._ON_DEMAND_REFS[0], c.source_sha)
    c.run = {**c.three_flow_run, "run_id": context._ON_DEMAND_RUN, "base_sha": failed,
             "task": {"id": "TASK-408", "revision": 1}}
    c.on_demand_lineage = {"failed_run_id": "RUN-408-001", "failed_head_sha": failed,
        "root_base_sha": c.on_demand_base_sha, "result_base_sha": c.on_demand_base_sha,
        "repair_authorization_sha": authorization_sha, "task": task, "failure": failure,
        "repair": authorization, "run": deepcopy(c.run)}
    c.result_package = c.synthetic_package(task, context._ON_DEMAND_RUN, c.source_sha,
                                          ["src/aios_renew/brain_context.py", ".ai/roadmap-state.yaml"])
    c.save_artifacts(run_id=context._ON_DEMAND_RUN, repair=c.on_demand_lineage)
    c.on_demand_artifact_sha = c.artifact_sha
    c.review = {"review_id": "REVIEW-408-001", "reviewed_sha": c.source_sha,
                "mode": "PRIMARY", "verdict": "PASS", "findings": [],
                "acceptance": {ac["id"]: "PASS" for ac in task["acceptance"]}}
    c.save_review(run_id=context._ON_DEMAND_RUN)
    c.on_demand_review_sha = c.review_sha
    return c


def test_task408_exact_publication_authenticates_a_distinct_versioned_policy(task408_canonical_audit_repo):
    from aios_renew.brain_context import _canonical_audit_source, _ON_DEMAND_APPROVAL, _THREE_FLOW_APPROVAL
    c = task408_canonical_audit_repo
    record, registry, authority = _canonical_audit_source(c.repo)
    assert record == c.roadmap[_ON_DEMAND_APPROVAL]
    assert record["target_policy_version"] == 2 and record["target_policy_state"] == "HUMAN_ON_DEMAND"
    assert record["implementation_run"] == "RUN-408-002"
    assert record["implementation_repair"]["failed_run_id"] == "RUN-408-001"
    assert c.run["base_sha"] == record["implementation_repair"]["failed_head_sha"]
    assert all(record["target_profiles"][flow] is None for flow in (
        "TASK_AUTHORING", "REPAIR_AUTHORING", "REMEDIATION_AUTHORING"))
    assert c.roadmap[_THREE_FLOW_APPROVAL]["target_policy_state"] == "THREE_SPECIALIZED"
    assert authority["reviewed_source_sha"] == c.on_demand_source_sha
    assert authority["effective_status"] == "TASK408_REVIEWED_PUBLISHED_HUMAN_ON_DEMAND_ACTIVE"
    assert len(registry["profiles"]) == 10


def test_task408_roadmap_binds_only_the_admitted_repair_without_claiming_activation():
    from aios_renew.brain_context import (
        _ON_DEMAND_APPROVAL, _ON_DEMAND_REPAIR, _THREE_FLOW_APPROVAL, _on_demand_approval, _digest,
    )
    root = Path(__file__).resolve().parents[1]
    roadmap = yaml.safe_load((root / ".ai/roadmap-state.yaml").read_bytes())
    record = roadmap[_ON_DEMAND_APPROVAL]
    assert record == _on_demand_approval(_digest(roadmap[_THREE_FLOW_APPROVAL]))
    assert record["implementation_run"] == "RUN-408-002"
    assert record["implementation_repair"] == _ON_DEMAND_REPAIR
    assert _ON_DEMAND_REPAIR["failed_run_id"] == "RUN-408-001"
    assert _ON_DEMAND_REPAIR["failed_head_sha"] == "7e95961949ddc115dc443d54b0bde6ed34bb5c07"
    assert _ON_DEMAND_REPAIR["repair_authorization_sha"] == "b4131dde44802ca005573e2a3acf3903e8c18cc0"
    assert record["status"] == "READY_CANDIDATE_REQUIRES_TASK408_PASS_AND_PUBLICATION"


@pytest.mark.parametrize("fault", ("missing_lineage", "failed_run", "authorization", "root", "failure_artifacts", "candidate"))
def test_task408_publication_requires_the_exact_repaired_run(task408_canonical_audit_repo, fault):
    from aios_renew.brain_context import _canonical_audit_source, _ON_DEMAND_REFS, _ON_DEMAND_RUN
    c = task408_canonical_audit_repo
    if fault == "failure_artifacts":
        c.git(c.canonical, "update-ref", "refs/heads/aios/failure-artifacts/RUN-408-001", c.three_flow_artifact_sha)
    elif fault == "candidate":
        c.git(c.canonical, "update-ref", _ON_DEMAND_REFS[0], c.on_demand_lineage["failed_head_sha"])
    else:
        if fault == "failed_run":
            c.run["run_id"] = "RUN-408-001"
        elif fault == "authorization":
            c.on_demand_lineage["repair_authorization_sha"] = "f" * 40
        elif fault == "root":
            c.on_demand_lineage["root_base_sha"] = "f" * 40
        c.save_artifacts(run_id=_ON_DEMAND_RUN, repair=None if fault == "missing_lineage" else c.on_demand_lineage)
    with pytest.raises(BrainContextError):
        _canonical_audit_source(c.repo)


@pytest.mark.parametrize("fault", ["missing", "duplicate", "partial", "mixed", "predecessor", "self_active",
                                  "unpublished", "review_source", "review_verdict", "evidence", "run", "claims"])
def test_task408_policy_cannot_self_activate_or_fall_back(task408_canonical_audit_repo, fault):
    from aios_renew.brain_context import _canonical_audit_source, _ON_DEMAND_APPROVAL, _ON_DEMAND_REFS, _ON_DEMAND_RUN
    c = task408_canonical_audit_repo
    record = c.roadmap[_ON_DEMAND_APPROVAL]
    if fault == "missing":
        del c.roadmap[_ON_DEMAND_APPROVAL]
    elif fault == "duplicate":
        c.roadmap[_ON_DEMAND_APPROVAL + "_duplicate"] = deepcopy(record)
    elif fault == "partial":
        del record["target_profiles"]["REPAIR_AUTHORING"]
    elif fault == "mixed":
        record["target_profiles"]["TASK_AUTHORING"] = record["target_on_demand_profiles"]["TASK_AUTHORING"]
    elif fault == "predecessor":
        record["superseded_approval_digest"] = "f" * 64
    elif fault == "self_active":
        record["status"] = "ACTIVE"
    elif fault == "unpublished":
        c.git(c.canonical, "update-ref", "refs/heads/main", c.on_demand_base_sha)
    elif fault == "review_source":
        c.git(c.canonical, "update-ref", _ON_DEMAND_REFS[1], c.three_flow_review_sha)
    elif fault == "review_verdict":
        c.review.update(verdict="BLOCKED")
        c.save_review(run_id=_ON_DEMAND_RUN)
    elif fault in {"evidence", "run", "claims"}:
        if fault == "evidence":
            c.result_package["evidence"][0]["result"]["exit_code"] = 1
        elif fault == "run":
            c.run["task"]["revision"] = 2
        else:
            c.result_package["result"]["claims"].pop()
        c.save_artifacts(run_id=_ON_DEMAND_RUN, repair=c.on_demand_lineage)
    if fault in {"missing", "duplicate", "partial", "mixed", "predecessor", "self_active"}:
        c.save_roadmap()
    with pytest.raises(BrainContextError):
        _canonical_audit_source(c.repo)


def test_task406_roadmap_is_one_prospective_decision_and_exact_exception():
    from aios_renew.brain_context import (_THREE_FLOW_APPROVAL, _three_flow_approval, _R21_SUCCESSOR_APPROVAL,
        _THREE_FLOW_PREDECESSOR_DIGEST, _THREE_FLOW_REPAIR, _TASK405_EXCEPTION, _digest, _UniqueKeyLoader)
    root = Path(__file__).resolve().parents[1]
    raw = (root / ".ai/roadmap-state.yaml").read_text(encoding="utf-8")
    nodes = {k.value: v for k, v in yaml.compose(raw).value}
    record = yaml.load(yaml.serialize(nodes[_THREE_FLOW_APPROVAL]), Loader=_UniqueKeyLoader)
    predecessor = yaml.load(yaml.serialize(nodes[_R21_SUCCESSOR_APPROVAL]), Loader=_UniqueKeyLoader)
    assert _digest(predecessor) == _THREE_FLOW_PREDECESSOR_DIGEST
    assert record == _three_flow_approval(_digest(predecessor))
    assert record["status"] == "READY_CANDIDATE_REQUIRES_TASK406_PASS_AND_PUBLICATION"
    assert record["implementation_run"] == "RUN-406-003"
    assert record["implementation_repair"] == _THREE_FLOW_REPAIR
    assert record["implementation_repair"]["failed_run_id"] == "RUN-406-002"
    assert record["required_review_mode"] == "PRIMARY"
    assert "REMEDIATION_AUTHORING" not in record["target_profiles"]
    assert _TASK405_EXCEPTION["merge_sha"] == "f445626011d0664149011c8f57eb16a7014e6cd7"
    assert _TASK405_EXCEPTION["original_review_verdict"] == "CHANGES_REQUIRED"
    assert _TASK405_EXCEPTION["delta_review_verdict"] == "BLOCKED"
    assert len(_TASK405_EXCEPTION["implementation_blobs"]) == 12
    assert (_TASK405_EXCEPTION["false_c2_byte_bound"], _TASK405_EXCEPTION["actual_byte_bound"]) == (98304, 65536)


def test_task406_git_authenticates_new_pass_separately_from_nonpass_exception(task406_canonical_audit_repo):
    from aios_renew.brain_context import _canonical_audit_source, _THREE_FLOW_APPROVAL, _THREE_FLOW_RUN
    c = task406_canonical_audit_repo
    record, registry, authority = _canonical_audit_source(c.repo)
    assert record == c.roadmap[_THREE_FLOW_APPROVAL]
    assert record["implementation_run"] == c.three_flow_run["run_id"] == _THREE_FLOW_RUN
    assert c.three_flow_run["base_sha"] == record["implementation_repair"]["failed_head_sha"]
    assert authority["effective_status"] == "TASK406_REVIEWED_PUBLISHED_THREE_FLOW_ACTIVE"
    assert authority["reviewed_source_sha"] == c.three_flow_source_sha
    assert authority["task405_source_exception"]["review_verdicts"] == ["CHANGES_REQUIRED", "BLOCKED"]
    assert authority["task405_source_exception"]["kind"] == "HUMAN_GIT_SOURCE_EXCEPTION_NOT_PUBLISHER_PASS"
    assert len(registry["profiles"]) == 10


@pytest.mark.parametrize("fault", ("absent", "duplicate", "alias", "duplicate_key", "active_label", "old_digest", "moa", "conflict",
    "exception_source", "exception_sha", "parent", "tree", "blob", "evidence", "f1", "missing_review", "false_pass", "not_main",
    "old_review_ref", "old_artifact_ref", "old_candidate_ref", "delta_ref", "failed_implementation_run",
    "missing_failure", "failure_artifacts", "authorization_ref"))
def test_task406_activation_never_falls_back_on_approval_or_source_conflicts(task406_canonical_audit_repo, fault):
    from aios_renew.brain_context import (_canonical_audit_source, _THREE_FLOW_APPROVAL,
        _R21_TCA3_APPROVAL, _THREE_FLOW_REFS, _THREE_FLOW_RUN)
    c = task406_canonical_audit_repo
    record = c.roadmap[_THREE_FLOW_APPROVAL]
    if fault == "absent":
        del c.roadmap[_THREE_FLOW_APPROVAL]
    elif fault == "duplicate":
        c.roadmap[_THREE_FLOW_APPROVAL + "_other"] = deepcopy(record)
    elif fault == "alias":
        c.roadmap["unrelated_name_cannot_hide_another_approval"] = deepcopy(record)
    elif fault == "active_label":
        record["status"] = "ACTIVE"
    elif fault == "old_digest":
        record["superseded_approval_digest"] = "f" * 64
    elif fault == "moa":
        record["target_profiles"]["REMEDIATION_AUTHORING"] = None
    elif fault == "conflict":
        c.roadmap[_R21_TCA3_APPROVAL] = deepcopy(record)
    elif fault in {"exception_source", "exception_sha", "parent", "tree", "blob", "evidence", "f1"}:
        e = record["task405_source_exception"] = deepcopy(record["task405_source_exception"])
        if fault == "parent":
            e["parents"][0] = "f" * 40
        elif fault == "blob":
            e["implementation_blobs"]["src/aios_renew/brain_audit.py"] = "f" * 40
        elif fault == "evidence":
            e["evidence_exit_codes"] = [0, 1]
        elif fault == "f1":
            e["disposition"] = "RESOLVED_PASS"
        else:
            e[{"exception_source": "merge_sha", "exception_sha": "source_sha", "tree": "tree"}[fault]] = "f" * 40
    elif fault == "missing_review":
        c.git(c.canonical, "update-ref", "-d", _THREE_FLOW_REFS[1])
    elif fault == "failed_implementation_run":
        record["implementation_run"] = "RUN-406-002"
    elif fault == "missing_failure":
        c.git(c.canonical, "update-ref", "-d", "refs/heads/aios/failure/RUN-406-002")
    elif fault in {"failure_artifacts", "authorization_ref"}:
        kind = "failure-artifacts" if fault == "failure_artifacts" else "repair"
        c.git(c.canonical, "update-ref", f"refs/heads/aios/{kind}/RUN-406-002", c.three_flow_source_sha)
    elif fault in {"old_review_ref", "old_artifact_ref", "old_candidate_ref", "delta_ref"}:
        ref = {"old_review_ref": "review-decision/RUN-405-004", "old_artifact_ref": "artifacts/RUN-405-004",
               "old_candidate_ref": "review/RUN-405-004", "delta_ref": "review-decision/RUN-405-005"}[fault]
        c.git(c.canonical, "update-ref", f"refs/heads/aios/{ref}", c.three_flow_source_sha)
    elif fault == "false_pass":
        c.source_sha = c.three_flow_source_sha
        c.review = deepcopy(c.three_flow_review)
        c.review["acceptance"]["AC10"] = "FAIL"
        c.save_review(run_id=_THREE_FLOW_RUN)
    if fault == "not_main":
        # Preserve the approval/code but remove its reviewed source from ancestry.
        c.git(c.canonical, "switch", "--detach", c.exception["merge_sha"])
        c.write(".ai/roadmap-state.yaml", yaml.safe_dump(c.roadmap).encode())
        foreign = c.commit("unpublished TASK-406 sibling fixture")
        c.git(c.canonical, "update-ref", "refs/heads/main", foreign)
    else:
        c.save_roadmap()
    if fault == "duplicate_key":
        c.save_roadmap(raw=yaml.safe_dump(c.roadmap).encode() + f"\n{_THREE_FLOW_APPROVAL}: {{}}\n".encode())
    with pytest.raises((BrainContextError, ValueError)):
        _canonical_audit_source(c.repo)


@pytest.mark.parametrize("fault", ("absent", "failed_run", "failed_head", "authorization_sha", "repair_id", "action",
    "root_base", "result_base", "embedded_run", "embedded_task", "embedded_failure", "authorization_scope", "delta_review"))
def test_task406_activation_requires_the_actual_repaired_result_lineage(task406_canonical_audit_repo, fault):
    from aios_renew.brain_context import _canonical_audit_source, _THREE_FLOW_RUN
    c = task406_canonical_audit_repo
    # Establish a usable chain before applying the one fault under test.
    _canonical_audit_source(c.repo)
    lineage = deepcopy(c.three_flow_lineage)
    if fault == "failed_run":
        lineage["failed_run_id"] = "RUN-406-001"
    elif fault in {"failed_head", "authorization_sha", "root_base", "result_base"}:
        key = {"failed_head": "failed_head_sha", "authorization_sha": "repair_authorization_sha",
               "root_base": "root_base_sha", "result_base": "result_base_sha"}[fault]
        lineage[key] = "f" * 40
    elif fault == "repair_id":
        lineage["repair"]["repair_id"] = "REPAIR-406-001-001"
    elif fault == "action":
        lineage["repair"]["action"] = "NO_CHANGE"
    elif fault == "embedded_run":
        lineage["run"]["run_id"] = "RUN-406-002"
    elif fault == "embedded_task":
        lineage["task"]["revision"] = 1
    elif fault == "embedded_failure":
        lineage["failure"]["phase"] = "COMPLETION_GATE"
    elif fault == "authorization_scope":
        lineage["repair"]["modification_scope"] = []
    elif fault == "delta_review":
        c.source_sha = c.three_flow_source_sha
        c.review = {**deepcopy(c.three_flow_review), "mode": "DELTA", "prior_finding_id": "F1"}
        c.save_review(run_id=_THREE_FLOW_RUN)
    c.run = deepcopy(c.three_flow_run)
    c.result_package = deepcopy(c.three_flow_package)
    c.save_artifacts(run_id=_THREE_FLOW_RUN, repair=None if fault == "absent" else lineage)
    with pytest.raises((BrainContextError, ValueError)):
        _canonical_audit_source(c.repo)


@pytest.mark.parametrize("fault", ("first_evidence", "second_evidence", "claim", "decision", "blob", "source_revision"))
def test_task406_exact_exception_payload_cannot_forge_success(task406_canonical_audit_repo, monkeypatch, fault):
    from aios_renew import authoring_ingress
    from aios_renew.brain_context import _canonical_audit_source
    c = task406_canonical_audit_repo
    read = authoring_ingress._read_commit_blob
    review_path = ".ai/reviews/REVIEW-405-002.yaml"
    historical = yaml.safe_load(c.git(c.canonical, "show", f"{c.exception['delta_review_sha']}:{review_path}"))
    assert (historical["review_id"], historical["reviewed_sha"], historical["verdict"]) == (
        "REVIEW-405-002", c.exception["source_sha"], "BLOCKED")
    tampered = []
    def changed(repo, sha, path):
        raw = read(repo, sha, path)
        # The canonical blob reader fetches objects missing from the clone and
        # retries. Tamper only actual bytes, never the pre-fetch missing result.
        if raw is None:
            return None
        original = raw
        if sha == c.exception["original_artifacts_sha"] and path == ".ai/transport/result.json":
            data = json.loads(raw)
            if fault in {"first_evidence", "second_evidence"}:
                data["evidence"][fault == "second_evidence"]["result"]["exit_code"] = 1
            elif fault == "claim":
                data["result"]["claims"][1]["claim"] = "Corrected 65536-byte claim"
            raw = json.dumps(data).encode()
        if sha == c.exception["delta_review_sha"] and path == review_path and fault == "decision":
            raw = raw.replace(b"BLOCKED", b"PASS")
        if sha == c.exception["merge_sha"] and path == "src/aios_renew/brain_audit.py" and fault == "blob":
            raw += b"\n# changed integration\n"
        if sha == c.exception["source_sha"] and path == ".ai/tasks/TASK-405.yaml" and fault == "source_revision":
            data = yaml.safe_load(raw)
            data["revision"] = 2
            raw = yaml.safe_dump(data).encode()
        if raw != original:
            tampered.append((sha, path))
        return raw
    monkeypatch.setattr(authoring_ingress, "_read_commit_blob", changed)
    with pytest.raises((BrainContextError, ValueError)):
        _canonical_audit_source(c.repo)
    assert tampered, "negative proof must reach the actual immutable payload"


@pytest.mark.parametrize("phase", ("ADMIT", "CONTINUE"))
def test_task406_policy_currentness_is_reauthenticated_at_both_boundaries(task406_canonical_audit_repo, phase):
    from aios_renew.brain_context import canonical_audit_inputs
    from aios_renew.brain_provider_protocol import construct_request
    from aios_renew.brain_return_contract import parse_return_contract_registry
    from test_brain_provider_protocol import production_inputs
    c = task406_canonical_audit_repo
    root = Path(__file__).resolve().parents[1]
    returns = parse_return_contract_registry((root / ".ai/brain-return-contracts.yaml").read_bytes())
    supplied, contract, bindings, sources = production_inputs(returns, "ARCHITECTURE", c)
    policy, profile, authenticate = canonical_audit_inputs(supplied, contract, bindings, repo=c.repo, **sources)
    if phase == "CONTINUE":
        construct_request(supplied, contract, bindings, profile, request_mode="PROSPECTIVE",
                          audit_policy=policy, selection_authenticator=authenticate, **sources)
    c.commit("canonical source moved before boundary authentication")
    with pytest.raises(ValueError):
        construct_request(supplied, contract, bindings, profile, request_mode="PROSPECTIVE",
                          audit_policy=policy, selection_authenticator=authenticate, selection_phase=phase, **sources)


def test_task406_old_admission_stays_decodable_but_cannot_switch_during_cutover(task406_canonical_audit_repo):
    from aios_renew.brain_context import canonical_audit_inputs, _THREE_FLOW_REFS
    from aios_renew.brain_provider_protocol import construct_request, revalidate_request
    from aios_renew.brain_return_contract import parse_return_contract_registry
    from test_brain_provider_protocol import production_inputs
    c = task406_canonical_audit_repo
    root = Path(__file__).resolve().parents[1]
    returns = parse_return_contract_registry((root / ".ai/brain-return-contracts.yaml").read_bytes())
    refs = dict(zip(_THREE_FLOW_REFS, (c.three_flow_source_sha, c.three_flow_review_sha, c.three_flow_artifact_sha)))
    for ref in refs:
        c.git(c.canonical, "update-ref", "-d", ref)
    c.main_sha = c.exception["parents"][0]
    c.git(c.canonical, "update-ref", "refs/heads/main", c.main_sha)
    supplied, contract, bindings, sources = production_inputs(returns, "TASK_AUTHORING", c)
    context = sources["task_source_context"]
    context["source_material"] = [{"path": context["authorization"]["scope"]["modify"][0], "content": "def bounded(): pass"}]
    old_policy, old_profile, authenticate = canonical_audit_inputs(supplied, contract, bindings, repo=c.repo, **sources)
    request = construct_request(supplied, contract, bindings, old_profile, request_mode="PROSPECTIVE",
                                audit_policy=old_policy, selection_authenticator=authenticate, **sources)
    assert request["audit_profile_package"]["audit_profile_ref"]["id"] == "tca-2"
    for ref, sha in refs.items():
        c.git(c.canonical, "update-ref", ref, sha)
    c.git(c.canonical, "update-ref", "refs/heads/main", c.three_flow_source_sha)
    assert authenticate(request["audit_selection"], "CONTINUE") is False
    assert revalidate_request(request) == request
    assert old_policy["profiles"]["TASK_AUTHORING"]["id"] == "tca-2"




def test_task408_cutover_never_reselects_an_admitted_legacy_audit(task408_canonical_audit_repo):
    from aios_renew.brain_context import canonical_audit_inputs, _ON_DEMAND_REFS
    from aios_renew.brain_provider_protocol import (
        construct_request, revalidate_request, validate_response, revalidate_decision, BrainProviderProtocolError,
    )
    from tests.test_brain_provider_protocol import task408_inputs, r5_response
    from aios_renew.brain_return_contract import parse_return_contract_registry
    c = task408_canonical_audit_repo
    refs = {ref: c.git(c.canonical, "rev-parse", ref) for ref in _ON_DEMAND_REFS}
    for ref in refs:
        c.git(c.canonical, "update-ref", "-d", ref)
    c.git(c.canonical, "update-ref", "refs/heads/main", c.three_flow_source_sha)
    c.main_sha = c.three_flow_source_sha
    registry = parse_return_contract_registry((Path(__file__).resolve().parents[1] / ".ai/brain-return-contracts.yaml").read_bytes())
    admitted = []
    for flow, mode in (("REPAIR_AUTHORING", "ROA_CONFORMANCE"), ("TASK_AUTHORING", "TCA_CONSTRUCT")):
        supplied, contract, bindings, sources, _ = task408_inputs(registry, flow, c)
        policy, package, authenticate = canonical_audit_inputs(supplied, contract, bindings, repo=c.repo, **sources)
        old = construct_request(supplied, contract, bindings, package, request_mode="PROSPECTIVE",
                                audit_policy=policy, selection_authenticator=authenticate, **sources)
        assert old["request_mode"] == mode and policy["state"] == "THREE_SPECIALIZED"
        decision = validate_response(old, r5_response(old))
        admitted.append((supplied, contract, bindings, sources, policy, package, authenticate, old, decision))
    c.git(c.canonical, "update-ref", "refs/heads/main", c.on_demand_source_sha)
    for ref, sha in refs.items():
        c.git(c.canonical, "update-ref", ref, sha)
    for supplied, contract, bindings, sources, policy, package, authenticate, old, decision in admitted:
        with pytest.raises(BrainProviderProtocolError):
            construct_request(supplied, contract, bindings, package, request_mode=old["request_mode"],
                              audit_policy=policy, selection_authenticator=authenticate, selection_phase="CONTINUE", **sources)
        assert revalidate_request(old) == old
        assert revalidate_decision(decision, old) == decision
        assert old["audit_selection"]["policy"]["state"] == "THREE_SPECIALIZED"
