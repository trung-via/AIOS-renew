"""Ordinary KA-01 activation integration definitions; Runtime owns execution.

Real Git source/ref/admission, Dispatcher/Codex adapter, lease, resource,
verification and terminal validators are used. Only Human HTTP and the native
provider process are fixtures. Neither is evidence of Codex live delivery.
"""

import copy
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from aios_renew import github_issue_ingress as carrier
from aios_renew import ka01_admission as ka01
from aios_renew import operator
from aios_renew import review_transport as transport
from aios_renew.execution_profile import bind_execution_profile
from aios_renew.run import Run
from aios_renew.runtime import primary_completion_policy
from aios_renew.verification_contract import VerificationContractError
from tests.git_fixture_support import (
    materialize_git_baseline, commit_fixture_state, _write_tree, _write_object,
)
from tests.operator_test_support import TASK_SOURCE, git, static_payload


SOURCE_ROOT = Path(__file__).resolve().parents[1]


class ProductionFixture:
    def __init__(self, root, monkeypatch, *, remote_url=False, policy="minimum-sufficient-v1", source_mismatch=False,
                 interactive=False, first_fail=False, proof_source=None):
        self.interactive = interactive
        task = TASK_SOURCE.replace("  required:\n", f"  policy: {policy}\n  required:\n")
        task = task.replace("git status --porcelain", "python check_output.py")
        files = {path: (SOURCE_ROOT / path).read_bytes() for path in (
            ka01.INTERACTIVE_IMPLEMENTATION_PATHS if interactive else ka01.IMPLEMENTATION_PATHS)}
        if interactive:
            # pytest prepends src even when the editable install already adds
            # it. Count each search entry once, as in the verifier subprocess,
            # so metadata discovery observes the same installed toolchain.
            monkeypatch.setattr(sys, "path", list(dict.fromkeys(sys.path)))
            task = task.replace("python check_output.py",
                "python -m pytest tests/test_output.py -n 0 --dist no --max-worker-restart 0")
            task = task.replace("Exercise the thin operator without a real executor.",
                "Exercise one native session with original Runtime feedback and committed output.")
            task = task.replace("  inspect: []", "  inspect: [tests/test_output.py]")
            task = task.replace("    - Commit the output.",
                "    - Commit OUTPUT.txt with complete followed by a newline.\n"
                "    - Do not alter tests, source support, or task files.\n"
                + ("    - For this bounded transport exercise, the first candidate alone must instead contain incomplete followed by a newline; after factual Runtime feedback independently correct it.\n"
                   if first_fail else ""))
            for path in ("src/aios_renew/verification_contract.py", "src/aios_renew/proof_coverage_contract.py",
                         "src/aios_renew/parallel_verification.py", "tests/bp_v4_probe_plugin.py",
                         "tests/conftest.py", "tests/git_fixture_support.py", "pyproject.toml"):
                files[path] = (SOURCE_ROOT / path).read_bytes()
            files.update({"src/aios_renew/__init__.py": b"", "tests/__init__.py": b"",
                "tests/test_output.py": proof_source or "from pathlib import Path\ndef test_output():\n    assert Path('OUTPUT.txt').read_text() == 'complete\\n'\n"})
        if source_mismatch:
            files["src/aios_renew/runtime.py"] += b"\n# Different selected implementation.\n"
        files.update({
            ka01.NORMATIVE_PATH: (SOURCE_ROOT / ka01.NORMATIVE_PATH).read_bytes(),
            ".ai/tasks/TASK-101.yaml": task,
            ".ai/executor-profiles.yaml": (SOURCE_ROOT / ".ai/executor-profiles.yaml").read_bytes(),
            ".ai/brain-ingress-carriers.yaml": yaml.safe_dump(dict(
                format="AIOS_BRAIN_INGRESS_CARRIERS_POLICY", version=1,
                github_issue=dict(enabled=True, repository="fixture-owner/fixture-repository",
                    authorized_actors=["fixture-human"], title_marker="[AIOS BRAIN INGRESS]",
                    max_body_bytes=131072))),
            "check_output.py": "from pathlib import Path\nassert Path('OUTPUT.txt').read_text() == 'complete\\n'\n",
        })
        self.repo, self.remote, self.base = materialize_git_baseline(root, files=files,
            user_name="KA01 activation fixture", user_email="ka01@example.invalid",
            commit_message="exact supported source", remote_head_main=True)
        if remote_url:
            git(self.repo, "remote", "set-url", "origin", self.remote.as_uri())
        self.url = git(self.repo, "remote", "get-url", "origin")
        self.task = operator.load_task(self.repo, "TASK-101")
        self.run = Run.from_task(run_id="RUN-101-001", task=self.task, executor="codex",
            base_sha=self.base, workspace=str(self.repo))
        self.profile = bind_execution_profile(run_id=self.run.run_id, executor="codex",
            model="gpt-6.1-sol" if interactive else "gpt-6-sol",
            reasoning_effort="max" if interactive else "high", repo=self.repo)
        self.policy = primary_completion_policy(self.task, base_sha=self.base)
        self.state = operator.runtime_paths(self.repo)
        self.issues, self.native_calls, self.verification_calls = {}, [], []
        self.owner = self.package = None
        self.native_hook = self.verification_hook = None
        self.output, self.native_ack_only = "complete\n", False
        monkeypatch.setattr(carrier, "_github_selection_json", self.http)
        # Notification transport is not an activation, proof or delivery permit.
        monkeypatch.setattr(transport, "publish_terminal_attention", lambda *a, **k: None)
        original = operator._activate_ka01_primary

        def observe_original(**kwargs):
            self.owner = kwargs["runtime"]
            return original(**kwargs)
        monkeypatch.setattr(operator, "_activate_ka01_primary", observe_original)

    def http(self, repository, suffix):
        assert repository == "fixture-owner/fixture-repository"
        if not suffix:
            return dict(full_name=repository, id=101)
        number = int(suffix.rsplit("/", 1)[1])
        issue = self.issues[number]
        if suffix.startswith("/issues/"):
            return copy.deepcopy(issue)
        assert suffix.startswith("/selection-edit-history/")
        return dict(data=dict(repository=dict(nameWithOwner=repository, databaseId=101,
            issue=dict(id=issue["node_id"], number=number, body=issue["body"], title=issue["title"],
                createdAt=issue["created_at"], lastEditedAt=issue.get("edited_at")))))

    def issue(self, number, body):
        raw = ka01._bytes(body)
        self.issues[number] = dict(number=number, id=10000 + number, node_id=f"I_{number}",
            title="[AIOS BRAIN INGRESS]", state="open", body=raw.decode("ascii"),
            repository_url="https://api.github.com/repos/fixture-owner/fixture-repository",
            user=dict(login="fixture-human", id=401, type="User"), created_at="2026-01-01T00:00:00Z")
        return dict(issue=number, sha256=ka01._digest(raw))

    def authorize(self, overrides=(), *, selection_change=None, consent_change=None):
        pins = [self.issue(1000 + i, dict(format="AIOS_KA01_V13_OVERRIDE", version=1,
            ka01_version="1.3", task=asdict(self.run.task), operations=["PRIMARY"], values=values))
            for i, values in enumerate(overrides)]
        selection = dict(format="AIOS_KA01_V13_SELECTION", version=1,
            implementation=ka01.INTERACTIVE_IMPLEMENTATION if self.interactive else ka01.PRODUCTION_IMPLEMENTATION,
            ka01_version="1.3", profile=ka01.PROFILE,
            policy=self.task.verification.policy, task=asdict(self.run.task), operations=["PRIMARY"],
            normative=dict(source_sha=self.base, path=ka01.NORMATIVE_PATH, sha256=ka01.NORMATIVE_SHA256),
            overrides=pins, remote_url=self.url)
        if selection_change:
            selection_change(selection)
        selected = self.issue(1100, selection)
        # This is a proposed Human consent body, constructed before admission;
        # the ordinary caller independently establishes every claimed fact.
        source = dict(operation="PRIMARY", task_sha256=ka01._digest(asdict(self.task)),
            policy_sha256=ka01._digest(asdict(self.policy)),
            admitted_run_sha256=ka01._digest(ka01._bytes(asdict(self.run))),
            base_sha=self.base, admission_main_sha=self.base)
        consent = dict(format="AIOS_KA01_V13_RUN_CONSENT", version=1,
            mode=ka01.INTERACTIVE_MODE if self.interactive else ka01.PRODUCTION_MODE,
            implementation=ka01.INTERACTIVE_IMPLEMENTATION if self.interactive else ka01.PRODUCTION_IMPLEMENTATION, ka01_version="1.3",
            selection_sha256=selected["sha256"], task=asdict(self.run.task), run_id=self.run.run_id,
            admitted_run_sha256=source["admitted_run_sha256"], operation="PRIMARY", executor="codex",
            execution_profile_sha256=ka01._digest(self.profile.as_dict()),
            operation_source_sha256=ka01._digest(source),
            effective_limits_sha256=ka01._digest(ka01.resolve_limits(list(overrides))))
        if consent_change:
            consent_change(consent)
        agreed = self.issue(1101, consent)
        self.request = ka01.AdmissionRequest(3 if self.interactive else 2, 1100, selected["sha256"], 1101, agreed["sha256"])
        self.ref = "refs/heads/aios/ka01-v13-consent/" + self.run.run_id
        # Materialize an actual orphan Git locator on the authoritative remote.
        # Do not seed it into local objects: the ordinary exact-source fetch is
        # part of the exercised production path, not an offline-only shortcut.
        objects = self.remote / "objects"
        blob = bytes.fromhex(_write_object(objects, "blob", ka01._bytes(asdict(self.request))))
        tree = _write_tree(objects, {".ai": {"transport": {"ka01-v13.json": blob}}})
        self.selector = _write_object(objects, "commit", (f"tree {tree}\n"
            "author Fixture <fixture@example.invalid> 1700000000 +0000\n"
            "committer Fixture <fixture@example.invalid> 1700000000 +0000\n\nselector\n").encode())
        git(self.remote, "update-ref", self.ref, self.selector)
        return self.request

    def native(self, command, **kwargs):
        self.native_calls.append((command, kwargs.get("timeout")))
        canonical = json.loads(kwargs["input"].decode().split("CANONICAL_INPUT:\n", 1)[1])
        assert canonical["run"]["run_id"] == self.run.run_id
        assert command[0] == "codex" and self.profile.model in command
        if self.native_hook:
            self.native_hook(self)
        if self.native_ack_only:
            return subprocess.CompletedProcess(command, 0, stdout=b'{"status":"ACK"}', stderr=b"")
        (self.repo / "OUTPUT.txt").write_text(self.output, encoding="utf-8")
        head = commit_fixture_state(self.repo, paths=("OUTPUT.txt",), message="native fixture candidate",
            user_name="KA01 activation fixture", user_email="ka01@example.invalid")
        payload = static_payload()
        payload["result"].update(head_sha=head, changed_files=["OUTPUT.txt"])
        self.package = payload
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload).encode(), stderr=b"")

    def verify(self, command, **kwargs):
        self.verification_calls.append((command, kwargs.get("timeout")))
        if self.interactive:
            self.package = self.owner._ka01_budget.session.last_turn.output["package"]
        assert git(Path(kwargs["cwd"]), "rev-parse", "HEAD") == self.package["result"]["head_sha"]
        if self.verification_hook:
            self.verification_hook(self)
        return subprocess.run(command, **kwargs)

    def execute(self, *, opted=True, **changes):
        arguments = dict(executor="codex", repo=self.repo, native_runner=self.native,
            verification_runner=self.verify, model=self.profile.model, reasoning_effort=self.profile.reasoning_effort)
        if opted:
            arguments["ka01_admission"] = self.request
        arguments.update(changes)
        return operator.run_task(self.task.task_id, **arguments)


@pytest.mark.parametrize("remote_url", [False, True])
def test_ordinary_primary_selects_real_budgets_proof_and_single_terminal(tmp_path, monkeypatch, remote_url):
    f = ProductionFixture(tmp_path / "ordinary", monkeypatch, remote_url=remote_url)
    f.authorize()
    # Exercise ordinary synchronization as well as the already-current caller;
    # the latter must fetch the exact selector instead of receiving local bytes.
    summary = f.execute(synchronize=not remote_url)
    budget = f.owner._ka01_budget
    assert budget.production and budget.stopped and budget.pending is None
    assert budget.native_state == "RETURNED" and budget.native_calls == 1
    assert f.owner._local_feedback_identity is None and not f.owner._local_feedback_receipts
    assert len(f.native_calls) == len(f.verification_calls) == 1
    assert 0 < f.native_calls[0][1] <= 240 * 60
    assert 0 < f.verification_calls[0][1] <= 180 * 60
    assert budget.checkpoints_reserved == budget.checkpoints_spent == 1
    assert budget.corrections_reserved == budget.corrections_spent == 0
    assert 0 < budget.verification_spent_ns < budget.verification_limit_ns
    assert budget.verification_reserved_ns == budget.verification_settled_ns
    admitted = json.loads((budget.directory / "admission.json").read_bytes())["authority"]
    assert admitted["production_activation"] == "SUPPORTED_BOUNDED_PRIMARY"
    assert admitted["effective_limits"] == ka01.DEFAULTS
    assert admitted["implementation_main_sha"] == f.base
    assert admitted["execution_profile"] == f.profile.as_dict()
    assert admitted["consent"]["provenance"]["actor"] == "fixture-human"
    assert set(admitted["implementation_source"]) == set(ka01.IMPLEMENTATION_PATHS)
    result = json.loads(summary.result_path.read_bytes())
    assert result["evidence"][0]["subject_sha"] == summary.head_sha
    assert result["evidence"][0]["source"]["command"] == "python check_output.py"
    assert result["result"]["claims"][0]["evidence"] == [f.run.run_id + "-V001"]
    assert not (f.state.failures / (f.run.run_id + ".json")).exists()
    assert git(f.remote, "rev-parse", "refs/heads/aios/review/" + f.run.run_id) == summary.head_sha
    assert git(f.remote, "rev-parse", "refs/heads/main") == f.base
    assert summary.ka01["native_feedback_delivery"] == "UNSUPPORTED_SAME_LIVE_RECIPIENT_CHANNEL_UNAVAILABLE"
    assert summary.ka01["continuation"] == "NOT_AUTHORIZED"
    assert summary.ka01["status"] == "COMPLETED_SUPPORTED_PRIMARY" and len(ka01._bytes(summary.ka01)) < 8192
    assert summary.ka01["review"] == summary.ka01["publication"] == "NOT_OBSERVED"
    assert summary.ka01["source_main_sha"] == f.base and '"configuration"' in summary.render()
    original = summary.result_path.read_bytes()
    from aios_renew.artifacts import ResultPackage, validate_structural_result
    # Only the structural native package may enter completion, never a receipt.
    # A stopped terminal owner cannot execute more proof or overwrite RESULT.
    with pytest.raises(VerificationContractError):
        f.owner.complete(ResultPackage(validate_structural_result(f.package["result"]), ()), f.policy)
    assert summary.result_path.read_bytes() == original and len(f.verification_calls) == 1


def test_ordinary_activation_freezes_independent_field_overrides(tmp_path, monkeypatch):
    f = ProductionFixture(tmp_path / "overrides", monkeypatch)
    f.authorize([{"max_corrections": 0}, {"max_checkpoints": 1}, {"max_runtime_verification_minutes": 90}])
    summary = f.execute()
    assert summary.ka01["effective_limits"] == {**ka01.DEFAULTS,
        "max_corrections": 0, "max_checkpoints": 1, "max_runtime_verification_minutes": 90}
    assert summary.ka01["checkpoints_spent"] == 1 and summary.ka01["corrections_spent"] == 0


def test_ordinary_activation_rejects_redirected_runtime_storage(tmp_path, monkeypatch):
    f = ProductionFixture(tmp_path / "storage-redirect", monkeypatch)
    f.authorize()
    state = replace(f.state, preverification=tmp_path / "unowned-pre-verification")
    monkeypatch.setattr(operator, "runtime_paths", lambda repo: state)
    with pytest.raises(VerificationContractError, match="CHECKPOINT_EXISTING_RUNTIME_OWNERSHIP_REQUIRED"):
        f.execute()
    assert f.native_calls == f.verification_calls == []
    assert f.owner._ka01_budget is None and f.owner._local_feedback_stopped
    assert not list(f.state.results.glob("*.json"))


def test_selected_source_must_be_the_running_implementation(tmp_path, monkeypatch):
    f = ProductionFixture(tmp_path / "source-mismatch", monkeypatch, source_mismatch=True)
    f.authorize()
    with pytest.raises(VerificationContractError, match="RUNNING_IMPLEMENTATION_SOURCE_MISMATCH"):
        f.execute()
    assert f.native_calls == f.verification_calls == []


def test_native_transport_ack_is_not_a_structural_result_or_feedback_receipt(tmp_path, monkeypatch):
    f = ProductionFixture(tmp_path / "ack", monkeypatch)
    f.authorize()
    f.native_ack_only = True
    with pytest.raises(operator.OperatorError, match="invalid structural ResultPackage"):
        f.execute()
    assert len(f.native_calls) == 1 and not f.verification_calls
    assert f.owner._local_feedback_identity is None and not f.owner._local_feedback_receipts
    assert f.owner._ka01_budget.stopped and not list(f.state.results.glob("*.json"))


@pytest.mark.parametrize("defect", [
    "version", "implementation", "normative", "policy", "operation", "run", "executor", "profile", "source", "limits",
    "forged-human", "edited-consent", "closed-selection", "missing-selector", "competing-selector", "moved-main",
])
def test_ordinary_selection_rejects_untrusted_stale_or_mismatched_context_before_native(tmp_path, monkeypatch, defect):
    f = ProductionFixture(tmp_path / "rejection", monkeypatch)
    selection = {
        "version": lambda s: s.update(ka01_version="1.2"),
        "implementation": lambda s: s.update(implementation=ka01.IMPLEMENTATION),
        "normative": lambda s: s["normative"].update(sha256="0" * 64),
        "policy": lambda s: s.update(policy="minimum-sufficient-v2"),
        "operation": lambda s: s.update(operations=["REPAIR"]),
    }.get(defect)
    consent = {
        "run": lambda c: c.update(run_id="RUN-101-002"),
        "executor": lambda c: c.update(executor="antigravity"),
        "profile": lambda c: c.update(execution_profile_sha256="0" * 64),
        "source": lambda c: c.update(operation_source_sha256="0" * 64),
        "limits": lambda c: c.update(effective_limits_sha256="0" * 64),
    }.get(defect)
    f.authorize(selection_change=selection, consent_change=consent)
    if defect == "forged-human":
        f.issues[1100]["user"]["login"] = "unapproved-actor"
    elif defect == "edited-consent":
        f.issues[1101]["edited_at"] = "2026-01-02T00:00:00Z"
    elif defect == "closed-selection":
        f.issues[1100]["state"] = "closed"
    elif defect == "missing-selector":
        git(f.remote, "update-ref", "-d", f.ref)
    elif defect == "competing-selector":
        (f.remote / "packed-refs").write_text(
            "# pack-refs with: peeled fully-peeled sorted\n" + f.selector + " " + f.ref + "/competing\n")
    elif defect == "moved-main":
        (f.repo / "later.txt").write_text("later source")
        commit_fixture_state(f.repo, paths=("later.txt",), message="main moved",
            user_name="KA01 activation fixture", user_email="ka01@example.invalid",
            remote=f.remote, remote_ref="refs/heads/main")
    with pytest.raises((VerificationContractError, carrier.GitHubIssueIngressError)):
        f.execute(synchronize=False)
    assert f.native_calls == f.verification_calls == []
    assert not list(f.state.results.glob("*.json"))
    assert f.owner is not None and f.owner._ka01_opted and f.owner._local_feedback_stopped


@pytest.mark.parametrize("defect", ["withdraw-consent", "profile-sidecar", "lease", "feedback-api"])
def test_original_native_authority_loss_stops_without_feedback_retry_or_proof(tmp_path, monkeypatch, defect):
    f = ProductionFixture(tmp_path / "native-stop", monkeypatch)
    f.authorize()

    def lose_authority(f):
        budget = f.owner._ka01_budget
        if defect == "withdraw-consent":
            f.issues[1101]["state"] = "closed"
        elif defect == "profile-sidecar":
            path = f.state.execution_profiles / (f.run.run_id + ".json")
            profile = json.loads(path.read_bytes())
            profile["model"] = "substitution"
            path.write_bytes(ka01._bytes(profile))
        elif defect == "lease":
            budget.leases.release(budget.lease)
            budget.leases.acquire(f.run)
        else:
            f.owner.seal_checkpoint(None)  # A local/queued feedback seam is unsupported here.
    f.native_hook = lose_authority
    with pytest.raises((VerificationContractError, carrier.GitHubIssueIngressError)):
        f.execute()
    assert len(f.native_calls) == 1 and not f.verification_calls
    assert f.owner._ka01_budget.stopped
    assert not list(f.state.results.glob("*.json"))


@pytest.mark.parametrize("limits", [
    {"max_tokens": 1}, {"max_run_wall_minutes": 0.000001, "max_runtime_verification_minutes": 0.000001},
    {"max_runtime_verification_minutes": 0.000001},
])
def test_unknown_usage_or_exhausted_finite_time_cannot_create_pass(tmp_path, monkeypatch, limits):
    f = ProductionFixture(tmp_path / "resource-stop", monkeypatch)
    f.authorize([limits])
    with pytest.raises(VerificationContractError):
        f.execute()
    assert not f.verification_calls and not list(f.state.results.glob("*.json"))
    assert len(f.native_calls) <= 1


def test_failed_runtime_proof_uses_existing_failure_terminal_without_reexecution(tmp_path, monkeypatch):
    f = ProductionFixture(tmp_path / "proof-failure", monkeypatch)
    f.authorize()
    f.output = "incomplete\n"
    with pytest.raises(operator.OperatorError):
        f.execute()
    assert len(f.native_calls) == len(f.verification_calls) == 1
    assert not list(f.state.results.glob("*.json"))
    failure = f.state.failures / (f.run.run_id + ".json")
    assert failure.is_file() and json.loads(failure.read_bytes())["kind"] == "FAILURE"
    assert f.owner._ka01_budget.pending is not None and f.owner._ka01_budget.stopped


@pytest.mark.parametrize("entrypoint,identity", [(operator.run_repair, "RUN-101-001"),
    (operator.run_remediation, "TASK-101")])
@pytest.mark.parametrize("interface", [2, 3])
def test_unsupported_correction_opt_in_stops_before_admission(tmp_path, entrypoint, identity, interface):
    request = ka01.AdmissionRequest(interface, 1, "1" * 64, 2, "2" * 64)
    with pytest.raises(VerificationContractError, match="PRODUCTION_OPERATION_UNSUPPORTED"):
        entrypoint(identity, executor="codex", repo=tmp_path / "absent", ka01_admission=request)
    assert not (tmp_path / "absent").exists()


def test_unsupported_verification_policy_is_not_silently_downgraded(tmp_path, monkeypatch):
    f = ProductionFixture(tmp_path / "v2-stop", monkeypatch, policy="minimum-sufficient-v2")
    f.authorize()
    with pytest.raises(VerificationContractError, match="PRODUCTION_CONFIGURATION_UNSUPPORTED"):
        f.execute()
    assert f.native_calls == f.verification_calls == []


def test_nonopted_ordinary_primary_preserves_legacy_behavior_and_historical_run(tmp_path, monkeypatch):
    f = ProductionFixture(tmp_path / "legacy", monkeypatch)
    historical = f.state.runs / "RUN-101-000.json"
    historical.write_bytes(ka01._bytes(asdict(replace(f.run, run_id="RUN-101-000"))))
    before = historical.read_bytes()
    summary = f.execute(opted=False)
    assert summary.ka01 is None and "ka01:" not in summary.render()
    assert f.owner is None and historical.read_bytes() == before
    assert len(f.native_calls) == len(f.verification_calls) == 1
    assert not (f.state.verification / f.run.run_id / "ka01-v13").exists()


def test_current_planning_has_one_activation_task_and_preserves_original_failure_lineage():
    roadmap = yaml.safe_load((SOURCE_ROOT / ".ai/roadmap-state.yaml").read_text(encoding="utf-8"))
    next_entries = [e for e in roadmap["sequence"] if e["status"] == "NEXT"]
    assert len(next_entries) == 1 and next_entries[0]["task_id"] == "TASK-424"
    assert roadmap["next_items"] == [next_entries[0]["id"]] == [roadmap["active_track"]]
    decision = roadmap["human_lean_vp_single_activation_closeout_20261025"]
    assert decision["remaining_task_count"] == 1 and decision["original_lean_vp_slot_count"] == 16
    assert decision["task418"]["failed_runs"] == [f"RUN-418-{i:03d}" for i in range(1, 8)]
    assert decision["task418"]["further_primary_or_repair"] == "NOT_SELECTED_RETIRED_PROSPECTIVELY"
    current = roadmap["human_lean_vp_native_primary_20261025"]
    assert current["remaining_task_count"] == 1 and current["implementation_task"] == "TASK-424"
    assert current["retained_implementation"] == ka01.PRODUCTION_IMPLEMENTATION
    assert current["new_implementation"] == ka01.INTERACTIVE_IMPLEMENTATION
    normative = (SOURCE_ROOT / ka01.NORMATIVE_PATH).read_bytes()
    assert hashlib.sha256(normative).hexdigest() == ka01.NORMATIVE_SHA256

