"""Deterministic synthetic browser/state fixtures; no live ChatGPT access."""

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent
from types import SimpleNamespace
from uuid import UUID

import pytest

from aios_renew import local_chat_wake as wake
from aios_renew import brain_attention as attention

EVENT = "terminal:RESULT:RUN-fixture-001:" + "a" * 40


def generic_event():
    return attention.project_observation(
        dict(boundary="PUBLICATION_FAILED", run_id="RUN-fixture-001", artifact_sha="a" * 40,
             decision_sha="b" * 40, reviewed_sha="c" * 40, stage="EXECUTION"),
        dict(workflow_run_id=1, run_attempt=1, artifact_id=2, source_digest="d" * 64)).event_id


def test_generic_attention_uses_durable_deferral_freshness_and_duplicate_preserving_compaction(binding):
    event = generic_event()
    adapter = RecoveryAdapter(binding, "DRAFT_PRESENT")
    assert deliver(event, wake.REPOSITORY, binding, lambda _: adapter)["status"] == "DEFERRED"
    assert stored(binding)["events"][event]["status"] == "DEFERRED"
    assert not adapter.inserts and not adapter.submits
    result = deliver(event, wake.REPOSITORY, binding, lambda _: adapter, projection=Projection("UNKNOWN"))
    assert result["reason"] == "CANONICAL_UNKNOWN" and event in stored(binding)["events"]
    wake.operate(wake.REPOSITORY, binding, adapter_factory=lambda _: pytest.fail("resolved source must not attach"),
                 projection=Projection("RESOLVED"), compact=True)
    assert event not in stored(binding)["events"]
    assert wake.event_digest(event) in stored(binding)["tombstones"]
    assert deliver(event, wake.REPOSITORY, binding, lambda _: adapter, projection=Projection("UNKNOWN"))["status"] == "NOOP"
    assert not adapter.submits


def test_generic_two_lane_isolation_and_generation_change_preserve_order(binding):
    event = generic_event()
    other = wake.Binding(synthetic_url(48), binding.cdp_endpoint, binding.state_path.with_name("generic-other.json"), 1, "fixture/other")
    blocked = RecoveryAdapter(binding, "GENERATION_ACTIVE")
    assert deliver(event, wake.REPOSITORY, binding, lambda _: blocked)["status"] == "DEFERRED"
    frozen = binding.state_path.read_bytes()
    active = RecoveryAdapter(other)
    assert deliver(event, other.repository, other, lambda _: active)["status"] == "SUBMITTED"
    assert binding.state_path.read_bytes() == frozen and not blocked.submits
    replacement = wake.Binding(synthetic_url(49), binding.cdp_endpoint, binding.state_path, binding.generation + 1, wake.REPOSITORY)
    current = [binding]
    racing = RecoveryAdapter(binding, race=lambda stage: current.__setitem__(0, replacement))
    assert deliver(event, wake.REPOSITORY, binding, lambda _: racing, binding_provider=lambda: current[0])["reason"] == "BINDING_GENERATION_CHANGED"
    assert not racing.inserts and not racing.submits
    assert stored(binding)["events"][event]["generation"] is None


def test_recovery_reuses_existing_subject_and_never_enqueues_its_own_identity(binding):
    event = generic_event()
    adapter = RecoveryAdapter(binding, "DRAFT_PRESENT")
    deliver(event, wake.REPOSITORY, binding, lambda _: adapter)
    recovery = attention.attention(attention.RECOVERY, dict(original_event_id=event)).event_id
    before = binding.state_path.read_bytes()
    assert deliver(recovery, wake.REPOSITORY, binding, lambda _: adapter, projection=Projection("UNKNOWN"))["reason"] == "CANONICAL_UNKNOWN"
    assert binding.state_path.read_bytes() == before
    adapter.block = None
    assert deliver(recovery, wake.REPOSITORY, binding, lambda _: adapter)["status"] == "SUBMITTED"
    assert list(stored(binding)["events"]) == [event]
    assert recovery not in stored(binding)["events"] and len(adapter.submits) == 1
    assert deliver(recovery, wake.REPOSITORY, binding, lambda _: adapter)["status"] == "BLOCKED"
    assert len(adapter.submits) == 1


def test_recovery_of_ambiguous_generic_attention_is_proof_only_on_original_generation(binding):
    event = generic_event()
    adapter = RecoveryAdapter(binding, proof=False)
    assert deliver(event, wake.REPOSITORY, binding, lambda _: adapter)["status"] == "BLOCKED"
    recovery = attention.attention(attention.RECOVERY, dict(original_event_id=event)).event_id
    assert deliver(recovery, wake.REPOSITORY, binding, lambda _: adapter)["status"] == "BLOCKED"
    assert len(adapter.submits) == 1 and stored(binding)["events"][event]["status"] == "AMBIGUOUS"
    assert stored(binding)["events"][event]["generation"] == binding.generation
    adapter.proof = True
    assert deliver(recovery, wake.REPOSITORY, binding, lambda _: adapter)["status"] == "SUBMITTED"
    assert len(adapter.submits) == 1


def test_recovery_without_an_existing_original_or_with_canonical_resolution_never_sends(binding):
    event = generic_event()
    recovery = attention.attention(attention.RECOVERY, dict(original_event_id=event)).event_id
    adapter = RecoveryAdapter(binding, "DRAFT_PRESENT")
    assert deliver(recovery, wake.REPOSITORY, binding, lambda _: adapter)["status"] == "BLOCKED"
    assert not binding.state_path.exists()
    deliver(event, wake.REPOSITORY, binding, lambda _: adapter)
    assert deliver(recovery, wake.REPOSITORY, binding, lambda _: adapter, projection=Projection("RESOLVED"))["status"] == "NOOP"
    assert stored(binding)["events"][event]["status"] == "RESOLVED_NOOP"
    assert not adapter.inserts and not adapter.submits


def test_every_non_recovery_attention_family_enters_same_durable_lane_path(binding):
    from test_brain_attention import examples
    for index, item in enumerate(examples()[:-1]):
        lane = wake.Binding(binding.chat_url, binding.cdp_endpoint,
                            binding.state_path.with_name(f"family-{index}.json"), binding.generation, binding.repository)
        adapter = RecoveryAdapter(lane, "DRAFT_PRESENT")
        assert deliver(item.event_id, lane.repository, lane, lambda _: adapter)["status"] == "DEFERRED"
        assert stored(lane)["events"][item.event_id]["status"] == "DEFERRED"
        assert not adapter.inserts and not adapter.submits


class Projection:
    def __init__(self, value="UNRESOLVED"):
        self.value, self.calls = value, []

    def observe(self, event_id):
        self.calls.append(event_id)
        return self.value(event_id) if callable(self.value) else self.value


def deliver(event_id, repository, binding, adapter_factory=wake.BrowserAdapter, **kwargs):
    kwargs.setdefault("projection", Projection())
    return wake.deliver(event_id, repository, binding, adapter_factory, **kwargs)


def stored(binding):
    return json.loads(binding.state_path.read_text())


class RecoveryAdapter:
    """Model only the transport boundaries, with no assistant content."""

    def __init__(self, binding, block=None, proof=True, generation="BUSY", race=None):
        self.binding, self.block, self.proof = binding, block, proof
        self.generation, self.race = generation, race
        self.submits, self.proofs, self.inserts = [], [], []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def check(self, text):
        if self.block:
            raise wake.WakeBlocked(self.block)

    def submit(self, text, before_insert=lambda: None, before_click=lambda: None):
        if self.race:
            self.race("insert")
        before_insert()
        self.inserts.append(text)
        if self.race:
            self.race("click")
        before_click()
        event_id = text.splitlines()[1].split(": ", 1)[1]
        assert stored(self.binding)["events"][event_id]["status"] == "AMBIGUOUS"
        self.submits.append(text)

    def prove(self, text):
        self.proofs.append(text)
        if not self.proof:
            raise wake.WakeBlocked("SUBMISSION_UNPROVEN")

    def generation_state(self):
        return self.generation

    def completed_wake(self, text):
        return False


def canonical_fixture(monkeypatch, kind="RESULT"):
    """Exact synthetic canonical ref/blob observations; no network or reducer."""
    run_id, artifact, head, successor = "RUN-fixture-001", "a" * 40, "b" * 40, "c" * 40
    task = {"id": "TASK-fixture", "revision": 1}
    root = "refs/heads/aios/"
    terminal = root + ("artifacts/" if kind == "RESULT" else "failure-artifacts/") + run_id
    candidate = root + ("review/" if kind == "RESULT" else "failure/") + run_id
    refs = {terminal: artifact, candidate: head}
    blobs = {(artifact, ".ai/transport/run.json"): dict(run_id=run_id, task=task, head_sha=None),
             (artifact, ".ai/transport/result.json"): {"result": {"head_sha": head}, "evidence": []},
             (artifact, ".ai/transport/failure.json"): dict(kind="FAILURE", run_id=run_id, task=task, failed_head_sha=head)}
    calls, drift = [], []
    def git(self, path, *args):
        calls.append(args)
        if args[0] == "ls-remote":
            current = dict(refs)
            if drift and sum(call[0] == "ls-remote" for call in calls) > 1:
                current[candidate] = "d" * 40
            return "\n".join(f"{sha}\t{ref}" for ref, sha in current.items())
        if args[0] == "show":
            sha, name = args[1].split(":", 1)
            return json.dumps(blobs[(sha, name)])
        if args[0] == "ls-tree":
            return ".ai/reviews/REVIEW-fixture-001.yaml"
        if args[0] == "rev-parse":
            return successor
        assert args[0] in {"init", "fetch"}
        return ""
    monkeypatch.setattr(wake.CanonicalFreshness, "git", git)
    projection = wake.CanonicalFreshness(wake.REPOSITORY)
    return SimpleNamespace(projection=projection, refs=refs, blobs=blobs, calls=calls, drift=drift,
                           event=f"terminal:{kind}:{run_id}:{artifact}", run_id=run_id, artifact=artifact,
                           head=head, successor=successor, task=task, root=root, terminal=terminal, candidate=candidate)


@pytest.mark.parametrize("kind", ["RESULT", "FAILURE"])
def test_canonical_observer_exact_terminal_and_no_checkout_or_semantic_mutation(monkeypatch, kind):
    fixture = canonical_fixture(monkeypatch, kind)
    assert fixture.projection.observe(fixture.event) == "UNRESOLVED"
    assert all(call[0] in {"init", "fetch", "ls-remote", "show"} for call in fixture.calls)
    fetches = [call for call in fixture.calls if call[0] == "fetch"]
    assert fetches and all("--no-write-fetch-head" in call and "--refmap=" in call for call in fetches)


@pytest.mark.parametrize("kind", ["RESULT", "FAILURE"])
@pytest.mark.parametrize("conflict", ["missing", "opposite", "artifact", "head", "task", "run", "drift"])
def test_canonical_observer_conflicts_and_unknown_identities_fail_closed(monkeypatch, kind, conflict):
    fixture = canonical_fixture(monkeypatch, kind)
    if conflict == "missing":
        del fixture.refs[fixture.terminal]
    if conflict == "opposite":
        family = "failure-artifacts/" if kind == "RESULT" else "artifacts/"
        fixture.refs[fixture.root + family + fixture.run_id] = "d" * 40
    if conflict == "artifact":
        fixture.refs[fixture.terminal] = "d" * 40
    if conflict == "head":
        fixture.refs[fixture.candidate] = "d" * 40
    if conflict == "task":
        fixture.blobs[(fixture.artifact, ".ai/transport/run.json")]["task"] = {"id": "TASK-fixture", "revision": True}
    if conflict == "run":
        fixture.blobs[(fixture.artifact, ".ai/transport/run.json")]["run_id"] = "RUN-other-001"
    if conflict == "drift":
        fixture.drift.append(True)
    assert fixture.projection.observe(fixture.event) == "UNKNOWN"


@pytest.mark.parametrize("mismatch", [None, "id", "head", "missing-fields"])
def test_only_exact_canonical_review_successor_resolves_result(monkeypatch, mismatch):
    fixture = canonical_fixture(monkeypatch)
    fixture.refs[fixture.root + "review-decision/" + fixture.run_id] = fixture.successor
    review = dict(review_id="REVIEW-fixture-001", reviewed_sha=fixture.head,
                  mode="PRIMARY", verdict="PASS", acceptance={}, findings=[])
    if mismatch == "id":
        review["review_id"] = "REVIEW-other-001"
    if mismatch == "head":
        review["reviewed_sha"] = "d" * 40
    if mismatch == "missing-fields":
        del review["findings"]
    fixture.blobs[(fixture.successor, ".ai/reviews/REVIEW-fixture-001.yaml")] = review
    assert fixture.projection.observe(fixture.event) == ("RESOLVED" if mismatch is None else "UNKNOWN")


@pytest.mark.parametrize("mismatch", [None, "run", "head", "task"])
def test_only_exact_human_repair_authorization_resolves_failure(monkeypatch, mismatch):
    fixture = canonical_fixture(monkeypatch, "FAILURE")
    fixture.refs[fixture.root + "repair/" + fixture.run_id] = fixture.successor
    authorization = dict(failed_run_id=fixture.run_id, failed_head_sha=fixture.head, task=fixture.task)
    if mismatch == "run":
        authorization["failed_run_id"] = "RUN-other-001"
    if mismatch == "head":
        authorization["failed_head_sha"] = "d" * 40
    if mismatch == "task":
        authorization["task"] = {"id": "TASK-other", "revision": 1}
    fixture.blobs[(fixture.successor, ".ai/transport/repair.json")] = authorization
    assert fixture.projection.observe(fixture.event) == ("RESOLVED" if mismatch is None else "UNKNOWN")


@pytest.mark.parametrize("mismatch", [None, "gap", "artifact", "predecessor", "identity"])
def test_repair_supersession_is_exact_contiguous_identity_observation(monkeypatch, mismatch):
    fixture = canonical_fixture(monkeypatch, "FAILURE")
    fixture.refs[fixture.root + "repair/" + fixture.run_id] = fixture.successor
    child = "d" * 40
    suffix = "3" if mismatch == "gap" else "2"
    fixture.refs[fixture.root + "repair-supersession/" + fixture.run_id + "/" + suffix] = child
    authorization = dict(failed_run_id=fixture.run_id, failed_head_sha=fixture.head, task=fixture.task)
    fixture.blobs[(fixture.successor, ".ai/transport/repair.json")] = authorization
    fixture.blobs[(child, ".ai/transport/repair.json")] = dict(authorization)
    metadata = dict(format="AIOS_REPAIR_SUPERSESSION", version=1, failed_run_id=fixture.run_id,
                    authorization_revision=2, predecessor_repair_sha=fixture.successor, failure_artifacts_sha=fixture.artifact)
    if mismatch == "artifact":
        metadata["failure_artifacts_sha"] = "e" * 40
    if mismatch == "predecessor":
        metadata["predecessor_repair_sha"] = "e" * 40
    if mismatch == "identity":
        fixture.blobs[(child, ".ai/transport/repair.json")]["failed_head_sha"] = "e" * 40
    fixture.blobs[(child, ".ai/transport/repair-supersession.json")] = metadata
    assert fixture.projection.observe(fixture.event) == ("RESOLVED" if mismatch is None else "UNKNOWN")


def test_canonical_remote_failure_is_sanitized_unknown(monkeypatch):
    def unavailable(*_):
        raise RuntimeError("private network details")
    monkeypatch.setattr(wake.CanonicalFreshness, "git", unavailable)
    assert wake.CanonicalFreshness(wake.REPOSITORY).observe(EVENT) == "UNKNOWN"


@pytest.mark.parametrize("reason", ["DRAFT_PRESENT", "GENERATION_ACTIVE", "TARGET_PAGE_NOT_UNIQUE", "SURFACE_UNPROVEN"])
def test_deferred_attention_survives_and_repeats_freshness_before_retry(binding, reason):
    adapter, projection = RecoveryAdapter(binding, reason), Projection()
    assert deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter, projection=projection)["status"] == "DEFERRED"
    assert stored(binding)["events"][EVENT]["status"] == "DEFERRED"
    assert not adapter.inserts
    adapter.block = None
    projection.calls.clear()
    receipts = wake.operate(wake.REPOSITORY, binding, adapter_factory=lambda _: adapter, projection=projection)
    assert receipts[-1]["status"] == "SUBMITTED"
    assert len(projection.calls) >= 3  # Preflight, insertion, click.
    assert len(adapter.submits) == 1


@pytest.mark.parametrize("boundary", ["insert", "click"])
@pytest.mark.parametrize("classification", ["RESOLVED", "UNKNOWN"])
def test_immediate_barrier_catches_canonical_race_without_sending(binding, boundary, classification):
    projection = Projection()
    def race(at):
        if at == boundary:
            projection.value = classification
    adapter = RecoveryAdapter(binding, race=race)
    result = deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter, projection=projection)
    assert result["status"] == ("NOOP" if classification == "RESOLVED" else "DEFERRED")
    assert not adapter.submits
    assert stored(binding)["events"][EVENT]["status"] == ("RESOLVED_NOOP" if classification == "RESOLVED" else "DEFERRED")


def test_human_chat_activity_is_not_canonical_resolution(binding):
    adapter = RecoveryAdapter(binding, "DRAFT_PRESENT")
    deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)
    adapter.block = "GENERATION_ACTIVE"  # Unrelated Human activity remains only a block.
    wake.operate(wake.REPOSITORY, binding, adapter_factory=lambda _: adapter, projection=Projection())
    assert stored(binding)["events"][EVENT]["status"] == "DEFERRED"
    wake.operate(wake.REPOSITORY, binding, adapter_factory=lambda _: pytest.fail("resolved subject must not attach"), projection=Projection("RESOLVED"))
    assert stored(binding)["events"][EVENT]["status"] == "RESOLVED_NOOP"


def test_ambiguity_is_proof_only_on_original_generation_and_never_redirects(binding):
    old = wake.Binding(binding.chat_url, binding.cdp_endpoint, binding.state_path, 1)
    current = wake.Binding(synthetic_url(99), binding.cdp_endpoint, binding.state_path, 2)
    adapter = RecoveryAdapter(old, proof=False)
    assert deliver(EVENT, wake.REPOSITORY, old, lambda _: adapter)["status"] == "BLOCKED"
    contacted = []
    def factory(bound):
        contacted.append(bound)
        assert bound == old
        return adapter
    wake.operate(wake.REPOSITORY, current, adapter_factory=factory, projection=Projection())
    assert contacted == [old]
    assert stored(old)["events"][EVENT]["generation"] == 1
    assert stored(old)["events"][EVENT]["status"] == "AMBIGUOUS"
    assert len(adapter.submits) == 1
    adapter.proof = True
    wake.operate(wake.REPOSITORY, current, adapter_factory=factory, projection=Projection())
    assert stored(old)["events"][EVENT]["status"] == "SUBMITTED"
    assert len(adapter.submits) == 1


@pytest.mark.parametrize("boundary", ["insert", "click"])
def test_pending_binding_change_aborts_before_click_and_retry_follows_current_generation(binding, boundary):
    old = wake.Binding(binding.chat_url, binding.cdp_endpoint, binding.state_path, 1)
    new = wake.Binding(synthetic_url(88), binding.cdp_endpoint, binding.state_path, 2)
    selected = [old]
    def race(at):
        if at == boundary:
            selected[0] = new
    adapter = RecoveryAdapter(old, race=race)
    receipt = deliver(EVENT, wake.REPOSITORY, old, lambda _: adapter, binding_provider=lambda: selected[0])
    assert receipt["reason"] == "BINDING_GENERATION_CHANGED"
    assert not adapter.submits
    replacement = RecoveryAdapter(new)
    wake.operate(wake.REPOSITORY, new, adapter_factory=lambda b: replacement if b == new else pytest.fail("wrong generation"), projection=Projection())
    assert len(replacement.submits) == 1
    assert stored(new)["events"][EVENT]["generation"] == 2


def test_generation_reuse_and_rollback_fail_closed(binding):
    first = wake.Binding(binding.chat_url, binding.cdp_endpoint, binding.state_path, 3)
    adapter = RecoveryAdapter(first, "DRAFT_PRESENT")
    deliver(EVENT, wake.REPOSITORY, first, lambda _: adapter)
    before = binding.state_path.read_bytes()
    for bad in (wake.Binding(synthetic_url(55), binding.cdp_endpoint, binding.state_path, 3),
                wake.Binding(binding.chat_url, binding.cdp_endpoint, binding.state_path, 2)):
        assert deliver(EVENT, wake.REPOSITORY, bad, lambda _: pytest.fail("must not attach"))["reason"] == "BINDING_GENERATION_CHANGED"
        assert binding.state_path.read_bytes() == before


def test_fifo_flight_preserves_distinct_subjects_and_revalidates_after_completion(binding):
    second = EVENT.replace("RUN-fixture-001", "RUN-fixture-002")
    third = EVENT.replace("RESULT:RUN-fixture-001", "FAILURE:RUN-fixture-003")
    adapter = RecoveryAdapter(binding)
    deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)
    assert deliver(second, wake.REPOSITORY, binding, lambda _: adapter)["status"] == "DEFERRED"
    assert deliver(third, wake.REPOSITORY, binding, lambda _: adapter)["status"] == "DEFERRED"
    assert len(adapter.submits) == 1
    assert list(stored(binding)["events"]) == [EVENT, second, third]
    adapter.generation = "IDLE"
    projection = Projection(lambda key: "RESOLVED" if key == second else "UNRESOLVED")
    wake.operate(wake.REPOSITORY, binding, adapter_factory=lambda _: adapter, projection=projection)
    assert stored(binding)["events"][second]["status"] == "RESOLVED_NOOP"
    assert stored(binding)["events"][third]["status"] == "SUBMITTED"
    assert len(adapter.submits) == 2


def test_unobserved_generation_completion_does_not_release_lane(binding):
    adapter = RecoveryAdapter(binding, generation="IDLE")
    deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)
    second = EVENT.replace("001", "002")
    deliver(second, wake.REPOSITORY, binding, lambda _: adapter)
    assert len(adapter.submits) == 1
    assert stored(binding)["flight"]["seen_busy"] is False
    assert stored(binding)["events"][second]["status"] == "DEFERRED"


def test_busy_lock_preserves_intake_in_lane_inbox_and_other_lane_progresses(binding):
    other = wake.Binding(synthetic_url(42), binding.cdp_endpoint, binding.state_path.with_name("other.json"), 1, "fixture/other")
    lock = binding.state_path.with_name(binding.state_path.name + ".lock")
    lock.touch()
    assert deliver(EVENT, wake.REPOSITORY, binding)["reason"] == "STATE_LOCKED_OR_UNAVAILABLE"
    inbox = binding.state_path.with_name(binding.state_path.name + ".queue")
    assert len(list(inbox.iterdir())) == 1
    frozen = next(inbox.iterdir()).read_bytes()
    adapter = RecoveryAdapter(other)
    assert deliver(EVENT, other.repository, other, lambda _: adapter)["status"] == "SUBMITTED"
    assert next(inbox.iterdir()).read_bytes() == frozen
    assert lock.exists() and not binding.state_path.exists()
    lock.unlink()
    recovered = RecoveryAdapter(binding)
    wake.operate(wake.REPOSITORY, binding, adapter_factory=lambda _: recovered, projection=Projection())
    assert len(recovered.submits) == 1
    assert not list(inbox.iterdir())


@pytest.mark.parametrize("block", ["DRAFT_PRESENT", "GENERATION_ACTIVE", "TARGET_PAGE_NOT_UNIQUE"])
def test_deferred_lane_cannot_mutate_consume_or_redirect_independent_lane(binding, block):
    other = wake.Binding(synthetic_url(44), binding.cdp_endpoint, binding.state_path.with_name("other.json"), 1, "fixture/other")
    blocked = RecoveryAdapter(binding, block)
    deliver(EVENT, wake.REPOSITORY, binding, lambda _: blocked)
    frozen = binding.state_path.read_bytes()
    adapter = RecoveryAdapter(other)
    result = deliver(EVENT, other.repository, other, lambda b: adapter if b == other else pytest.fail("cross-lane binding"))
    assert result["status"] == "SUBMITTED"
    assert binding.state_path.read_bytes() == frozen
    assert not blocked.submits


def test_compaction_requires_fresh_resolution_and_keeps_permanent_duplicate_digest(binding):
    adapter = RecoveryAdapter(binding, "DRAFT_PRESENT")
    deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)
    second = EVENT.replace("001", "002")
    adapter.block, adapter.proof = None, False
    deliver(second, wake.REPOSITORY, binding, lambda _: adapter)
    before = stored(binding)
    wake.operate(wake.REPOSITORY, binding, adapter_factory=lambda _: adapter, projection=Projection("UNKNOWN"), compact=True)
    assert set(stored(binding)["events"]) == set(before["events"])
    wake.operate(wake.REPOSITORY, binding, adapter_factory=lambda _: pytest.fail("resolution does not inspect browser"), projection=Projection("RESOLVED"), compact=True)
    data = stored(binding)
    assert not data["events"] and data["flight"] is None
    assert set(data["tombstones"]) == {wake.event_digest(EVENT), wake.event_digest(second)}
    result = deliver(EVENT, wake.REPOSITORY, binding, lambda _: pytest.fail("compacted duplicate must not resend"), projection=Projection("UNRESOLVED"))
    assert result["reason"] == "COMPACTED_DUPLICATE"


def test_submitted_unresolved_record_cannot_be_compacted(binding):
    adapter = RecoveryAdapter(binding)
    deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)
    wake.operate(wake.REPOSITORY, binding, adapter_factory=lambda _: adapter, projection=Projection(), compact=True)
    assert stored(binding)["events"][EVENT]["status"] == "SUBMITTED"
    assert not stored(binding)["tombstones"]


@pytest.mark.parametrize("duplicate", ["chat", "state", "lock", "queue-child", "config"])
def test_registry_rejects_duplicate_chat_and_all_state_ownership(monkeypatch, binding, duplicate):
    path = binding.state_path.parent / "registry.json"
    first = dict(chat_url=binding.chat_url, cdp_endpoint=binding.cdp_endpoint, state_path=str(binding.state_path), generation=1)
    second = dict(first, chat_url=synthetic_url(9), state_path=str(binding.state_path.with_name("other.json")))
    if duplicate == "chat":
        second["chat_url"] = synthetic_project_url(number=int(UUID(binding.chat_url.rsplit("/", 1)[-1])))
    if duplicate == "state":
        second["state_path"] = first["state_path"]
    if duplicate == "lock":
        second["state_path"] = first["state_path"] + ".lock"
    if duplicate == "queue-child":
        second["state_path"] = str(binding.state_path.with_name(binding.state_path.name + ".queue") / "child")
    if duplicate == "config":
        second["state_path"] = str(path)
    path.write_text(json.dumps(dict(version=2, lanes={wake.REPOSITORY: first, "fixture/other": second})))
    monkeypatch.setenv("AIOS_LOCAL_CHAT_WAKE_CONFIG", str(path))
    with pytest.raises(wake.WakeBlocked):
        wake.load_binding()


def test_registry_selects_only_exact_repository_and_generation(monkeypatch, binding):
    path = binding.state_path.parent / "registry.json"
    lanes = {wake.REPOSITORY: dict(chat_url=binding.chat_url, cdp_endpoint=binding.cdp_endpoint, state_path=str(binding.state_path), generation=7),
             "fixture/other": dict(chat_url=synthetic_url(8), cdp_endpoint=binding.cdp_endpoint, state_path=str(binding.state_path.with_name("other.json")), generation=11)}
    path.write_text(json.dumps(dict(version=2, lanes=lanes)))
    monkeypatch.setenv("AIOS_LOCAL_CHAT_WAKE_CONFIG", str(path))
    assert wake.load_binding().generation == 7
    assert wake.load_binding("fixture/other").generation == 11
    with pytest.raises(wake.WakeBlocked, match="BINDING_MISSING"):
        wake.load_binding("fixture/unknown")


def test_drain_is_gate_bound_and_does_not_admit_any_subject(monkeypatch, binding, capsys):
    monkeypatch.setenv("AIOS_LOCAL_CHAT_WAKE_ENABLED", "false")
    monkeypatch.setattr(wake, "load_binding", lambda *_: pytest.fail("closed gate must not read registry"))
    assert wake.main(["--drain", "--repository", wake.REPOSITORY]) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "ENABLE_GATE_CLOSED"
    monkeypatch.setenv("AIOS_LOCAL_CHAT_WAKE_ENABLED", "true")
    monkeypatch.setattr(wake, "load_binding", lambda *_: binding)
    monkeypatch.setattr(wake, "CanonicalFreshness", lambda *_: Projection())
    assert wake.main(["--drain", "--compact", "--rechecks", "2", "--interval", "0", "--repository", wake.REPOSITORY]) == 0
    assert stored(binding)["events"] == {}
    assert not binding.state_path.with_name(binding.state_path.name + ".queue").exists()


@pytest.mark.parametrize("arguments", [["--drain", "--event-id", EVENT], ["--drain", "--rechecks", "9"], ["--drain", "--interval", "31"], ["--compact", "--event-id", EVENT]])
def test_drain_bounds_and_admission_modes_fail_closed(arguments, capsys):
    assert wake.main(arguments + ["--repository", wake.REPOSITORY]) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "INVALID_INPUT"


def test_queued_generic_subjects_and_later_barriers_have_independent_bounded_observations(monkeypatch, binding):
    first = generic_event()
    original = attention.parse_event_id(first)
    second = attention.attention(original.family, dict(original.selectors, run_id="RUN-fixture-002")).event_id
    adapter = RecoveryAdapter(binding, "DRAFT_PRESENT")
    deliver(first, wake.REPOSITORY, binding, lambda _: adapter)
    deliver(second, wake.REPOSITORY, binding, lambda _: adapter)
    now, windows = [100.0], []
    monkeypatch.setattr(wake.time, "monotonic", lambda: now[0])
    projection = wake.CanonicalFreshness(wake.REPOSITORY)
    now[0] += 100  # Idle time after construction must not consume an observation.
    monkeypatch.setattr(projection, "git", lambda *_: "")
    def freshness(item, sources, artifacts):
        windows.append((item.event_id, artifacts.deadline - now[0]))
        assert artifacts.deadline == projection.deadline
        now[0] += 31 if item.event_id == first else 5
        return "UNRESOLVED" if item.event_id == first else "RESOLVED"
    monkeypatch.setattr(attention, "freshness", freshness)
    receipts = wake.operate(wake.REPOSITORY, binding, adapter_factory=lambda _: adapter, projection=projection)
    assert [(r["event_id"], r["reason"]) for r in receipts] == [
        (first, "CANONICAL_UNKNOWN"), (second, "CANONICALLY_RESOLVED")]
    assert windows == [(first, 30), (second, 30)]
    assert stored(binding)["events"][first]["status"] == "DEFERRED"
    assert stored(binding)["events"][second]["status"] == "RESOLVED_NOOP"
    assert projection.observe(second) == "RESOLVED"
    assert windows[-1] == (second, 30)
    assert not adapter.submits


def test_git_operation_ceiling_and_success_after_subject_deadline_fail_closed(monkeypatch, tmp_path):
    now, timeouts = [100.0], []
    monkeypatch.setattr(wake.time, "monotonic", lambda: now[0])
    projection = wake.CanonicalFreshness(wake.REPOSITORY)
    projection.deadline = 130
    def run(command, **kwargs):
        timeouts.append(kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout=b"")
    monkeypatch.setattr(wake.subprocess, "run", run)
    projection.git(tmp_path, "init")
    now[0] = 125
    projection.git(tmp_path, "ls-remote")
    assert timeouts == [15, 5]
    def exhausted(command, **kwargs):
        now[0] = 130
        return run(command, **kwargs)
    monkeypatch.setattr(wake.subprocess, "run", exhausted)
    with pytest.raises(wake.WakeBlocked, match="CANONICAL_UNKNOWN"):
        projection.git(tmp_path, "ls-remote")
    count = len(timeouts)
    with pytest.raises(wake.WakeBlocked, match="CANONICAL_UNKNOWN"):
        projection.git(tmp_path, "ls-remote")
    assert len(timeouts) == count


def test_recovery_unwrapping_cannot_extend_the_original_subject_observation_window(monkeypatch):
    original = generic_event()
    recovery = attention.attention(attention.RECOVERY, dict(original_event_id=original)).event_id
    now = [100.0]
    monkeypatch.setattr(wake.time, "monotonic", lambda: now[0])
    parse = attention.parse_event_id
    def parse_with_cost(event_id):
        now[0] += 5
        return parse(event_id)
    monkeypatch.setattr(attention, "parse_event_id", parse_with_cost)
    projection = wake.CanonicalFreshness(wake.REPOSITORY)
    monkeypatch.setattr(projection, "git", lambda *_: "")
    def freshness(item, sources, artifacts):
        assert item.event_id == original
        assert artifacts.deadline == 130
        now[0] = 131
        return "UNRESOLVED"
    monkeypatch.setattr(attention, "freshness", freshness)
    assert projection.observe(recovery) == "UNKNOWN"


def test_api_operation_ceiling_uses_the_subject_deadline_and_rejects_late_success(monkeypatch):
    import urllib.request
    now, timeouts = [100.0], []
    monkeypatch.setattr(attention.time, "monotonic", lambda: now[0])
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, limit):
            return b"{}"
    def open_request(request, timeout):
        timeouts.append(timeout)
        return Response()
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_: SimpleNamespace(open=open_request))
    artifacts = attention.ArtifactSources(wake.REPOSITORY, deadline=130)
    assert artifacts.request("/fixture") == {}
    now[0] = 125
    assert artifacts.request("/fixture") == {}
    assert timeouts == [10, 5]
    def late_read(self, limit):
        now[0] = 131
        return b"{}"
    monkeypatch.setattr(Response, "read", late_read)
    with pytest.raises(attention.AttentionError, match="SOURCE_TIMEOUT"):
        artifacts.request("/fixture")
    count = len(timeouts)
    with pytest.raises(attention.AttentionError, match="SOURCE_TIMEOUT"):
        artifacts.request("/fixture")
    assert len(timeouts) == count


def test_follow_up_drain_retries_unknown_without_schedule_and_stops_after_one_submission(monkeypatch, binding):
    first, second = generic_event(), EVENT.replace("001", "002")
    adapter = RecoveryAdapter(binding)
    for event in (first, second):
        deliver(event, wake.REPOSITORY, binding, lambda _: adapter, projection=Projection("UNKNOWN"))
    monkeypatch.setenv("AIOS_LOCAL_CHAT_WAKE_ENABLED", "true")
    monkeypatch.setattr(wake, "load_binding", lambda *_: binding)
    original_operate, passes, sleeps = wake.operate, [], []
    def operate(*args, **kwargs):
        passes.append(len(passes))
        # Freshness becomes available at the finite second opportunity.
        value = "UNKNOWN" if len(passes) == 1 else "UNRESOLVED"
        return original_operate(*args, adapter_factory=lambda _: adapter, projection=Projection(value), **kwargs)
    monkeypatch.setattr(wake, "operate", operate)
    monkeypatch.setattr(wake.time, "sleep", lambda interval: sleeps.append(interval))
    assert wake.main(["--drain", "--rechecks", "8", "--interval", "15", "--repository", wake.REPOSITORY]) == 0
    assert len(passes) == 2 and sleeps == [15]
    assert len(adapter.submits) == 1 and first in adapter.submits[0]
    assert stored(binding)["events"][second]["status"] == "DEFERRED"
    assert stored(binding)["flight"]["event_id"] == first


@pytest.mark.parametrize("proof", [True, False])
def test_duplicate_direct_fallback_concurrent_follow_ups_and_optional_schedule_never_resend(monkeypatch, binding, proof):
    from test_brain_attention import IDENTITY
    source = attention.source_document([dict(boundary="PUBLICATION_PROVEN", **IDENTITY,
                                              published_sha=IDENTITY["reviewed_sha"])])
    pointer = dict(workflow_run_id=100, run_attempt=1, artifact_id=200, source_digest=attention.digest(source))
    direct = attention.project_source(source, pointer)[0].event_id
    fallback = attention.project_source(attention.load_json(attention.canonical(source)), pointer)[0].event_id
    assert direct == fallback
    second = EVENT.replace("001", "002")
    adapter = RecoveryAdapter(binding, proof=proof)
    for event in (direct, fallback, second):
        deliver(event, wake.REPOSITORY, binding, lambda _: adapter, projection=Projection("UNKNOWN"))
    assert list(stored(binding)["events"]) == [direct, second]
    entered, release = ThreadEvent(), ThreadEvent()
    original_check = adapter.check
    def check(text):
        entered.set()
        assert release.wait(5)
        original_check(text)
    adapter.check = check
    original_operate = wake.operate
    def operate(repository, selected_binding, event_id=None, adapter_factory=None,
                projection=None, binding_provider=None, compact=False):
        return original_operate(repository, selected_binding, event_id,
                                lambda _: adapter, Projection(), binding_provider, compact)
    monkeypatch.setattr(wake, "operate", operate)
    monkeypatch.setenv("AIOS_LOCAL_CHAT_WAKE_ENABLED", "true")
    monkeypatch.setattr(wake, "load_binding", lambda *_: binding)
    command = ["--drain", "--rechecks", "2", "--interval", "0", "--repository", wake.REPOSITORY]
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_drain = executor.submit(wake.main, command)
        try:
            assert entered.wait(5)
            assert executor.submit(wake.main, command).result(timeout=5) == 1
            # Intake remains durable during the competing lane lock.
            assert wake.deliver(direct, wake.REPOSITORY, binding)["reason"] == "STATE_LOCKED_OR_UNAVAILABLE"
        finally:
            release.set()
        assert first_drain.result(timeout=5) == 0
    # An optional later scheduled invocation uses exactly the same drain.
    assert wake.main(command) == 0
    assert len(adapter.submits) == 1 and direct in adapter.submits[0]
    assert stored(binding)["events"][direct]["status"] == ("SUBMITTED" if proof else "AMBIGUOUS")
    assert stored(binding)["events"][second]["status"] == "DEFERRED"
    assert stored(binding)["flight"]["event_id"] == direct


def synthetic_url(number=1):
    # Generated fixture identity, never an operational conversation binding.
    return "https://chatgpt.com" + "/c/" + str(UUID(int=number))


def synthetic_project_url(number=1, project="g-fixture-project-1"):
    return "https://chatgpt.com/g/" + project + "/c/" + str(UUID(int=number))


@pytest.fixture(params=[synthetic_url(), synthetic_project_url()], ids=["standalone", "project"])
def binding(tmp_path, request):
    return wake.Binding(request.param, "http://" + "127.0.0.1:9222", tmp_path / "state.json")


@pytest.mark.parametrize("url", [
    synthetic_url(), synthetic_project_url(),
    synthetic_project_url(project="g-Fixture-ABC-123"),
    synthetic_project_url(project="g-"),
])
@pytest.mark.parametrize("suffix", ["", "/"])
def test_normalize_chat_preserves_full_identity_except_optional_trailing_slash(url, suffix):
    assert wake.normalize_chat(url + suffix) == url
    assert wake.normalize_chat(wake.normalize_chat(url + suffix)) == url


@pytest.mark.parametrize("path", [
    "/g//c/", "/g/fixture-project/c/", "/g/G-fixture-project/c/",
    "/g/g-fixture_project/c/", "/g/g-fixture.project/c/", "/g/g-fixture\u00e9/c/",
    "/g/g-fixture project/c/", "/g/g-fixture/project/c/",
    "/g/g-fixture-project/extra/c/", "/g/g-fixture-project/",
    "/g/g-fixture-project/c/c/", "/g/g-fixture-project//c/",
    "/g/%67-fixture-project/c/", "/g/g-fixture%2Dproject/c/",
    "/g/g-fixture%2Fproject/c/", "/g/g-fixture-project/%63/",
])
def test_malformed_project_paths_fail_closed(path):
    url = "https://chatgpt.com" + path + str(UUID(int=1))
    with pytest.raises(wake.WakeBlocked, match="INVALID_CHAT_BINDING"):
        wake.normalize_chat(url)


@pytest.mark.parametrize("base", [synthetic_url(), synthetic_project_url()])
@pytest.mark.parametrize("suffix", ["//", "/extra", "?fixture=value", "?", "#fixture", "#"])
def test_extra_components_queries_and_fragments_fail_closed(base, suffix):
    with pytest.raises(wake.WakeBlocked, match="INVALID_CHAT_BINDING"):
        wake.normalize_chat(base + suffix)


@pytest.mark.parametrize("origin", [
    "http://chatgpt.com", "HTTPS://chatgpt.com", "https://CHATGPT.COM",
    "https://www.chatgpt.com", "https://chatgpt.com.example.invalid",
    "https://chatgpt.com:443", "https://fixture@chatgpt.com", "https://example.invalid",
])
def test_project_wrong_origin_fails_closed(origin):
    path = "/g/g-fixture-project-1/c/" + str(UUID(int=1))
    with pytest.raises(wake.WakeBlocked, match="INVALID_CHAT_BINDING"):
        wake.normalize_chat(origin + path)


@pytest.mark.parametrize("conversation", [
    "", "fixture", str(UUID(int=1)).replace("-", ""),
    str(UUID(int=10)).upper(), "%30" + str(UUID(int=1))[1:],
])
def test_project_invalid_or_encoded_conversation_fails_closed(conversation):
    with pytest.raises(wake.WakeBlocked, match="INVALID_CHAT_BINDING"):
        wake.normalize_chat("https://chatgpt.com/g/g-fixture-project-1/c/" + conversation)


class FakeAdapter:
    def __init__(self, binding, failure=None):
        self.binding, self.failure = binding, failure
        self.submits = self.proofs = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def check(self, text):
        if self.failure == "check":
            raise wake.WakeBlocked("DRAFT_PRESENT")

    def submit(self, text, before_insert=lambda: None, before_click=lambda: None):
        before_insert()
        before_click()
        assert json.loads(self.binding.state_path.read_text())["events"][EVENT]["status"] == "AMBIGUOUS"
        self.submits += 1
        if self.failure == "submit":
            raise RuntimeError("private browser exception " + self.binding.chat_url)

    def generation_state(self):
        return "BUSY"

    def prove(self, text):
        self.proofs += 1
        if self.failure == "proof":
            raise wake.WakeBlocked("SUBMISSION_UNPROVEN")


def test_delivery_persists_attempt_before_submit_and_submitted_after_proof(binding):
    adapter = FakeAdapter(binding)
    assert deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter) == {
        "event_id": EVENT, "status": "SUBMITTED", "reason": "EXACT_USER_TURN_PROVEN",
    }
    assert adapter.submits == adapter.proofs == 1
    data = json.loads(binding.state_path.read_text())
    assert data["version"] == 2
    assert data["events"][EVENT]["status"] == "SUBMITTED"

    def forbidden(_):
        pytest.fail("duplicate must not connect to the browser")

    assert deliver(EVENT, wake.REPOSITORY, binding, forbidden) == {
        "event_id": EVENT, "status": "NOOP", "reason": "ALREADY_SUBMITTED",
    }


@pytest.mark.parametrize("failure", ["submit", "proof"])
def test_ambiguous_attempt_never_resends_or_exposes_private_browser_data(binding, failure):
    adapter = FakeAdapter(binding, failure)
    receipt = deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)
    assert receipt["status"] == "BLOCKED"
    assert set(receipt) == {"event_id", "status", "reason"}
    assert binding.chat_url not in json.dumps(receipt)
    assert binding.cdp_endpoint not in json.dumps(receipt)
    assert json.loads(binding.state_path.read_text())["events"][EVENT]["status"] == "AMBIGUOUS"
    adapter.failure = "proof"
    assert deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)["reason"] == "ATTEMPT_REQUIRES_HUMAN"
    assert adapter.submits == 1


def test_existing_attempting_entry_is_preserved_without_browser_or_recovery(binding):
    data = {"version": 1, "events": {EVENT: "ATTEMPTING",
            "terminal:RESULT:RUN-fixture-other:" + "b" * 40: "SUBMITTED"}}
    binding.state_path.write_text(json.dumps(data))
    assert deliver(EVENT, wake.REPOSITORY, binding,
                        lambda _: pytest.fail("ATTEMPTING must not attach or resend")) == {
        "event_id": EVENT, "status": "BLOCKED", "reason": "ATTEMPT_REQUIRES_HUMAN",
    }
    migrated = json.loads(binding.state_path.read_text())
    assert migrated["events"][EVENT] == wake.record("AMBIGUOUS")
    assert migrated["events"]["terminal:RESULT:RUN-fixture-other:" + "b" * 40]["status"] == "SUBMITTED"


def test_pre_submit_failure_has_no_attempt_or_composer_modification(binding):
    adapter = FakeAdapter(binding, "check")
    assert deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)["reason"] == "DRAFT_PRESENT"
    assert json.loads(binding.state_path.read_text())["events"][EVENT]["status"] == "DEFERRED"
    assert adapter.submits == adapter.proofs == 0


@pytest.mark.parametrize("data", [
    {"version": 1, "events": {EVENT: "UNKNOWN"}},
    {"version": 1, "events": []},
    {"version": True, "events": {}},
    {"version": 1, "events": {}, "private": "untrusted"},
])
def test_ambiguous_state_fails_before_browser_connection(binding, data):
    binding.state_path.write_text(json.dumps(data))
    assert deliver(EVENT, wake.REPOSITORY, binding, lambda _: pytest.fail("must not attach"))["reason"] == "STATE_AMBIGUOUS"


def test_corrupt_state_and_stale_lock_block(binding):
    binding.state_path.write_text("not-json")
    assert deliver(EVENT, wake.REPOSITORY, binding)["reason"] == "LOCAL_METADATA_INVALID"
    binding.state_path.with_name("state.json.lock").touch()
    assert deliver(EVENT, wake.REPOSITORY, binding)["reason"] == "STATE_LOCKED_OR_UNAVAILABLE"


def test_interrupted_pending_write_blocks_before_browser_connection(binding):
    binding.state_path.with_name("state.json.pending").write_text("interrupted")
    assert deliver(EVENT, wake.REPOSITORY, binding, lambda _: pytest.fail("must not attach"))["reason"] == "STATE_WRITE_UNCERTAIN"


def test_state_write_failure_prevents_submit(binding, monkeypatch):
    adapter = FakeAdapter(binding)
    def unavailable(self, data):
        raise wake.WakeBlocked("STATE_WRITE_UNCERTAIN")
    monkeypatch.setattr(wake.State, "write", unavailable)
    assert deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)["reason"] == "STATE_WRITE_UNCERTAIN"
    assert adapter.submits == 0


def test_final_state_write_failure_keeps_attempting_and_prevents_resend(binding, monkeypatch):
    adapter = FakeAdapter(binding)
    original = wake.State.write
    def unavailable_after_proof(self, data):
        if data["events"][EVENT]["status"] == "SUBMITTED":
            raise wake.WakeBlocked("STATE_WRITE_UNCERTAIN")
        original(self, data)
    monkeypatch.setattr(wake.State, "write", unavailable_after_proof)
    assert deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)["reason"] == "STATE_WRITE_UNCERTAIN"
    assert json.loads(binding.state_path.read_text())["events"][EVENT]["status"] == "AMBIGUOUS"
    adapter.failure = "proof"
    assert deliver(EVENT, wake.REPOSITORY, binding, lambda _: adapter)["reason"] == "ATTEMPT_REQUIRES_HUMAN"
    assert adapter.submits == 1


def test_bounded_state_never_evicts_old_events(binding):
    events = {f"terminal:RESULT:RUN-fixture-{i}:" + "a" * 40: "SUBMITTED" for i in range(wake.MAX_EVENTS)}
    binding.state_path.write_text(json.dumps({"version": 1, "events": events}))
    assert deliver(EVENT, wake.REPOSITORY, binding)["reason"] == "STATE_CAPACITY_REQUIRES_HUMAN"
    assert json.loads(binding.state_path.read_text())["events"] == events


def write_config(monkeypatch, binding, data=None):
    path = binding.state_path.parent / "binding.json"
    path.write_text(json.dumps(data if data is not None else {
        "chat_url": binding.chat_url, "cdp_endpoint": binding.cdp_endpoint,
        "state_path": str(binding.state_path),
    }))
    monkeypatch.setenv("AIOS_LOCAL_CHAT_WAKE_CONFIG", str(path))
    return path


def test_missing_and_valid_machine_binding(monkeypatch, binding):
    monkeypatch.delenv("AIOS_LOCAL_CHAT_WAKE_CONFIG", raising=False)
    with pytest.raises(wake.WakeBlocked, match="BINDING_MISSING"):
        wake.load_binding()
    write_config(monkeypatch, binding)
    assert wake.load_binding() == binding


@pytest.mark.parametrize("field,value", [
    ("chat_url", "https://example.invalid/"), ("chat_url", None),
    ("chat_url", " " + synthetic_url()), ("chat_url", synthetic_url() + "?private=value"),
    ("chat_url", synthetic_url() + "#fragment"),
    ("cdp_endpoint", "http://example.invalid:9222"),
    ("cdp_endpoint", "http://localhost:9222/?private=value"),
    ("cdp_endpoint", "http://user:private@localhost:9222"),
    ("cdp_endpoint", " http://localhost:9222"), ("cdp_endpoint", None),
    ("state_path", "relative.json"),
])
def test_malformed_binding_fails_closed(monkeypatch, binding, field, value):
    data = {"chat_url": binding.chat_url, "cdp_endpoint": binding.cdp_endpoint,
            "state_path": str(binding.state_path)}
    data[field] = value
    write_config(monkeypatch, binding, data)
    with pytest.raises(wake.WakeBlocked):
        wake.load_binding()


def test_binding_and_state_cannot_be_repository_owned(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    with pytest.raises(wake.WakeBlocked, match="CONFIG_OR_STATE_IN_REPOSITORY"):
        wake.external_path(str(repo / "state.json"))
    path = repo / "binding.json"
    path.write_text("{}")
    monkeypatch.setenv("AIOS_LOCAL_CHAT_WAKE_CONFIG", str(path))
    with pytest.raises(wake.WakeBlocked, match="CONFIG_OR_STATE_IN_REPOSITORY"):
        wake.load_binding()


def test_unknown_duplicate_oversized_and_missing_config_block(monkeypatch, binding):
    path = write_config(monkeypatch, binding, {"unexpected": "value"})
    with pytest.raises(wake.WakeBlocked, match="BINDING_MALFORMED"):
        wake.load_binding()
    for content in ('{"chat_url": "one", "chat_url": "two"}', "x" * (wake.MAX_BYTES + 1)):
        path.write_text(content)
        with pytest.raises(wake.WakeBlocked, match="LOCAL_METADATA_INVALID"):
            wake.load_binding()
    path.unlink()
    with pytest.raises(wake.WakeBlocked, match="LOCAL_METADATA_INVALID"):
        wake.load_binding()


def test_doorbell_and_receipt_have_only_bounded_fields(monkeypatch, capsys):
    assert wake.doorbell(EVENT, wake.REPOSITORY).splitlines() == [
        "[AIOS LOCAL CHAT WAKE]", f"event_id: {EVENT}",
        "repository: trung-via/AIOS-renew", "fresh_brain_sync_required: true",
    ]
    for event in (EVENT + "\ncommand: private", "terminal:RESULT:RUN-" + "x" * 97 + ":" + "a" * 40):
        with pytest.raises(wake.WakeBlocked):
            wake.doorbell(event, wake.REPOSITORY)
    with pytest.raises(wake.WakeBlocked):
        wake.doorbell(EVENT, "other/repository;command")
    monkeypatch.delenv("AIOS_LOCAL_CHAT_WAKE_CONFIG", raising=False)
    monkeypatch.setenv("AIOS_LOCAL_CHAT_WAKE_ENABLED", "true")
    assert wake.main(["--event-id", EVENT, "--repository", wake.REPOSITORY]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "event_id": EVENT, "status": "BLOCKED", "reason": "BINDING_MISSING",
    }


def test_invalid_cli_and_unknown_reasons_do_not_echo_private_data(capsys):
    assert wake.main(["--event-id", "private", "--repository", "private"]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "BLOCKED", "reason": "INVALID_INPUT"}
    with pytest.raises(SystemExit):
        wake.main(["--private-url", synthetic_url()])
    assert json.loads(capsys.readouterr().out) == {"status": "BLOCKED", "reason": "INVALID_INPUT"}
    assert str(wake.WakeBlocked(synthetic_url())) == "LOCAL_FAILURE"


class Locator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    def filter(self, *, visible):
        assert visible is True
        return self

    def count(self):
        return self.page.counts.get(self.selector, 0)

    def text_content(self):
        # Outbound text stays inside the bounded browser resolver.
        assert self.selector == wake.COMPOSER
        return self.page.draft

    def get_attribute(self, name):
        assert self.selector == wake.COMPOSER and name == "aria-disabled"
        return "true" if self.page.disabled else None


class Page:
    def __init__(self, url):
        self.url, self.draft = url, ""
        self.payload = wake.doorbell(EVENT, wake.REPOSITORY)
        self.outbound = self.payload
        self.send_enabled = self.disabled = False
        self.counts = {"main": 1, wake.COMPOSER: 1, wake.ACCOUNT: 1}
        self.evaluations = []
        self.resolutions = []
        self.race = None

    def locator(self, selector):
        assert selector in {"main", wake.COMPOSER, wake.ACCOUNT, wake.STOP, wake.NONREGULAR, wake.LOGIN}
        return Locator(self, selector)

    def wait_for_function(self, script, *, arg, timeout):
        if script == wake.WAIT_USER_TURN:
            assert arg["text"] == self.payload and timeout == 5000
            if self.evaluate(wake.RESOLVE_USER_TURN, arg) != "EXACT":
                raise RuntimeError("bounded outbound proof absent")
            return
        assert script == wake.ACCEPT_INSERT and arg["text"] == self.payload and timeout == 3000
        if self.counts.get(wake.SEND, 0) != 1 or not self.send_enabled or self.draft != self.payload:
            raise RuntimeError("scoped readiness absent")

    def evaluate(self, script, args):
        assert args["text"] == self.payload
        assert args["composer"] == wake.COMPOSER and args["account"] == wake.ACCOUNT
        if script == wake.RESOLVE_USER_TURN:
            self.resolutions.append(script)
            count = self.counts.get("outbound", 0)
            if not count or self.outbound.replace("\r\n", "\n") != args["text"]:
                return "ABSENT"
            return "EXACT" if count == 1 else "AMBIGUOUS"
        self.evaluations.append(script)
        if script == wake.INSERT:
            if self.race == "insert":
                self.draft = "Human draft"
                return False
            self.draft = args["text"]
            self.counts[wake.SEND] = 1
            self.send_enabled = True
        elif script == wake.CLICK:
            if self.race == "send":
                self.draft += " Human edit"
                return False
            assert self.draft == args["text"]
            self.draft = ""
            self.counts["outbound"] = 1
            self.send_enabled = False
        elif script == wake.ACCEPT_INSERT:
            return self.draft == args["text"] and self.counts.get(wake.SEND, 0) == 1 and self.send_enabled
        elif script == wake.PROVE_SEND:
            count = self.counts.get(wake.SEND, 0)
            return count == 0 or (count == 1 and not self.send_enabled)
        else:
            pytest.fail("unexpected browser script")
        return True


def adapter_for(binding, *pages):
    adapter = wake.BrowserAdapter(binding)
    adapter.browser = SimpleNamespace(contexts=[SimpleNamespace(pages=list(pages))])
    adapter.page = pages[0] if pages else None
    return adapter


class LocalSurfaceAdapter(wake.BrowserAdapter):
    """Replace CDP acquisition only; exercise the production delivery methods."""

    def __init__(self, binding, page):
        super().__init__(binding)
        self.browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[page])])
        self.page = page

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def completion_dom_result(binding, scenario="complete", text=None):
    """Run the production witness with traps on every non-user content read."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the isolated JavaScript DOM harness")
    arguments = dict(text=text or wake.doorbell(EVENT, wake.REPOSITORY),
                     userTurn=wake.USER_TURN, userBubble=wake.USER_BUBBLE,
                     turnContainer=wake.TURN_CONTAINER)
    harness = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8')), args = input.args;
const nodes = [];
function element(attrs = {}, parent = null, tagName = 'DIV') {
  const e = {attrs, parentElement: parent, tagName, children: [], hidden: false, noRects: false,
    hasAttribute(name) {return name in this.attrs;},
    getAttribute(name) {
      if (!['data-message-author-role', 'data-turn', 'data-testid', 'data-turn-key',
            'aria-hidden', 'hidden'].includes(name)) throw Error('unbounded attribute');
      return this.attrs[name] ?? null;
    },
    getClientRects() {return this.noRects ? [] : [1];},
    contains(child) {
      for (let p = child; p; p = p.parentElement) if (p === this) return true;
      return false;
    },
    matches(selector) {
      if (selector === 'main') return this.tagName === 'MAIN';
      let negate = null;
      const split = selector.split(':not(');
      if (split.length === 2) {selector = split[0]; negate = split[1].slice(0, -1);}
      const attrs = [...selector.matchAll(/\[([a-z-]+)(?:="([^"]*)")?\]/g)];
      if (!attrs.length || attrs.map(m => m[0]).join('') !== selector)
        throw Error('unbounded selector');
      return attrs.every(([, key, value]) => key in this.attrs &&
        (value === undefined || this.attrs[key] === value)) && (!negate || !this.matches(negate));
    },
    querySelectorAll(selector) {
      return nodes.filter(n => n !== this && this.contains(n) &&
        selector.split(', ').some(s => n.matches(s)));
    },
    querySelector(selector) {return this.querySelectorAll(selector)[0] ?? null;},
    get nextElementSibling() {
      if (!this.parentElement) return null;
      return this.parentElement.children[this.parentElement.children.indexOf(this) + 1] ?? null;
    },
    get textContent() {
      if (!this.exactUser) throw Error('non-user content read');
      return this.body;
    },
    get innerText() {throw Error('rendered content read');},
    get innerHTML() {throw Error('HTML read');},
    get outerHTML() {throw Error('HTML read');}
  };
  if (parent) parent.children.push(e);
  nodes.push(e); return e;
}
function move(e, parent, index = parent.children.length) {
  if (e.parentElement) e.parentElement.children.splice(e.parentElement.children.indexOf(e), 1);
  e.parentElement = parent; parent.children.splice(index, 0, e);
}
const root = element(), main = element({}, root, 'MAIN'), list = element({}, main);
const turn = (role, number, parent = list) => element({'data-turn-key': 'fixture-' + number,
  'data-testid': 'conversation-turn-' + number, 'data-turn': role}, parent, 'ARTICLE');
const wakeTurn = turn('user', 8), user = element({'data-user-message-bubble': ''}, wakeTurn);
user.exactUser = true; user.body = args.text;
const response = turn('assistant', 9);
const assistant = element({'data-message-author-role': 'assistant'}, response);
// Assistant, transcript, wrapper and page content getters all throw, even on the success path.
const name = input.scenario;
if (name === 'legacy') {delete user.attrs['data-user-message-bubble']; user.attrs['data-message-author-role'] = 'user';}
if (name === 'both') user.attrs['data-message-author-role'] = 'user';
if (name === 'absent') user.body = 'different synthetic user turn';
if (name === 'missing_user') delete user.attrs['data-user-message-bubble'];
if (name === 'hidden_user') user.hidden = true;
if (name === 'hidden_wake') wakeTurn.attrs['hidden'] = '';
if (name === 'duplicate_user') {
  const duplicate = element({'data-user-message-bubble': ''}, wakeTurn);
  duplicate.exactUser = true; duplicate.body = args.text;
}
if (name === 'missing_response') {response.attrs = {}; assistant.attrs = {};}
if (name === 'hidden_response') response.hidden = true;
if (name === 'no_response_rects') response.noRects = true;
if (name === 'hidden_assistant') assistant.hidden = true;
if (name === 'hidden_wrapper') {
  const wrapper = element({'aria-hidden': 'true'}, response);
  move(assistant, wrapper);
}
if (['duplicate_response', 'hidden_duplicate_response'].includes(name)) {
  const duplicate = turn('assistant', 9);
  duplicate.hidden = name === 'hidden_duplicate_response';
  element({'data-message-author-role': 'assistant'}, duplicate);
}
if (name === 'duplicate_ordinal_elsewhere') {
  const otherList = element({}, main), duplicate = turn('assistant', 9, otherList);
  duplicate.hidden = true;
}
if (name === 'duplicate_assistant') element({'data-message-author-role': 'assistant'}, response);
if (name === 'nested_assistant') element({'data-message-author-role': 'assistant'}, assistant);
if (name === 'nested_response') turn('assistant', 10, response);
if (name === 'nested_wake') {const nested = turn('user', 7, wakeTurn); move(user, nested);}
if (name === 'nested_pair') {
  const outer = turn('assistant', 7); move(wakeTurn, outer); move(response, outer);
}
if (name === 'missing_role') delete assistant.attrs['data-message-author-role'];
if (name === 'unknown_role') assistant.attrs['data-message-author-role'] = 'tool';
if (name === 'mixed_roles') element({'data-message-author-role': 'tool'}, response);
if (name === 'role_ambiguous_wrapper') list.attrs['data-message-author-role'] = 'assistant';
if (name === 'role_ambiguous_turn') response.attrs['data-turn'] = 'user';
if (name === 'role_ambiguous_descendant') assistant.attrs['data-turn'] = 'user';
if (name === 'user_marker_in_assistant') {
  move(user, response); user.exactUser = false; // Reading this role-ambiguous node is forbidden.
}
if (name === 'intervening_user') {
  const intervening = turn('user', 9); move(intervening, list, 1);
  response.attrs['data-testid'] = 'conversation-turn-10';
}
if (name === 'virtualized_gap') response.attrs['data-testid'] = 'conversation-turn-10';
if (name === 'placeholder') {const placeholder = element({}, list); move(placeholder, list, 1);}
if (name === 'later_user') turn('user', 10);
if (name === 'missing_ordinal') delete response.attrs['data-testid'];
if (name === 'missing_turn_key') delete wakeTurn.attrs['data-turn-key'];
if (name === 'outside_main') {move(wakeTurn, root); move(response, root);}
if (name === 'duplicate_main') element({}, root, 'MAIN');
if (name === 'identity_bound') {
  for (let i = 0; i < 256; i++) element({'data-user-message-bubble': ''}, list);
}
global.getComputedStyle = e => ({visibility: e.hidden ? 'hidden' : 'visible', display: 'block'});
global.document = {querySelectorAll(selector) {
  if (![args.userTurn + ', ' + args.userBubble, 'main'].includes(selector))
    throw Error('unbounded page query');
  return nodes.filter(e => selector.split(', ').some(s => e.matches(s)));
}};
process.stdout.write(JSON.stringify(eval('(' + input.script + ')')(args)));
"""
    process = subprocess.run(
        [node, "-e", harness], input=json.dumps(dict(args=arguments, scenario=scenario,
                                                    script=wake.PROVE_WAKE_COMPLETION)),
        capture_output=True, text=True, check=True, timeout=10,
    )
    assert process.stdout in {"true", "false"}  # No browser metadata or content export.
    return json.loads(process.stdout)


class CompletionPage(Page):
    """Keep production surface gates and execute the real structural script."""

    def __init__(self, binding, scenario="complete", race=None):
        super().__init__(binding.chat_url)
        self.binding, self.scenario, self.completion_race = binding, scenario, race
        self.completions = []

    def evaluate(self, script, arguments):
        if script == wake.PROVE_WAKE_COMPLETION:
            assert set(arguments) == {"text", "userTurn", "userBubble", "turnContainer"}
            assert arguments["text"] == self.payload
            self.completions.append(script)
            result = completion_dom_result(self.binding, self.scenario, arguments["text"])
            if self.completion_race:
                self.completion_race(self)
            return result
        return super().evaluate(script, arguments)


def submitted_with_pending(binding, scenario="complete", proof=True):
    page = CompletionPage(binding, scenario)
    holder = LocalSurfaceAdapter(binding, page)
    if not proof:
        wait_for_function = page.wait_for_function

        def wait_without_submission_proof(script, **kwargs):
            if script == wake.WAIT_USER_TURN:
                raise RuntimeError("proof unavailable")
            return wait_for_function(script, **kwargs)

        page.wait_for_function = wait_without_submission_proof
    first = deliver(EVENT, wake.REPOSITORY, binding, lambda _: holder)
    assert first["status"] == ("SUBMITTED" if proof else "BLOCKED")
    second = EVENT.replace("RUN-fixture-001", "RUN-fixture-002")
    state = wake.State(binding.state_path)
    with state.locked() as data:
        data["events"][second] = wake.record()
        state.write(data)
    assert stored(binding)["flight"] == dict(event_id=EVENT, seen_busy=False)
    return page, holder, second


@pytest.mark.parametrize("scenario", ["complete", "legacy", "both"])
def test_exact_completed_wake_releases_only_pointer_and_sends_fresh_second_once(binding, scenario):
    page, holder, second = submitted_with_pending(binding, scenario)
    before = stored(binding)
    projection, next_adapter, attachments = Projection(), RecoveryAdapter(binding), []

    def factory(bound):
        assert bound == binding
        attachments.append(bound)
        return holder if len(attachments) == 1 else next_adapter

    receipts = wake.operate(wake.REPOSITORY, binding, adapter_factory=factory, projection=projection)
    after = stored(binding)
    assert receipts == [dict(event_id=second, status="SUBMITTED", reason="EXACT_USER_TURN_PROVEN")]
    assert page.completions == [wake.PROVE_WAKE_COMPLETION]
    assert page.evaluations.count(wake.CLICK) == 1  # The first wake is never resent.
    assert next_adapter.submits == [wake.doorbell(second, wake.REPOSITORY)]
    assert projection.calls == [EVENT, second, second, second]
    assert after["events"][EVENT] == before["events"][EVENT] == wake.record("SUBMITTED", binding.generation)
    assert after["bindings"] == before["bindings"] and after["tombstones"] == before["tombstones"]
    assert set(after) == set(before) and after["version"] == before["version"] == 2
    assert after["flight"] == dict(event_id=second, seen_busy=False)
    assert deliver(EVENT, wake.REPOSITORY, binding, lambda _: pytest.fail("dedupe must not attach"))["reason"] == "ALREADY_SUBMITTED"
    assert deliver(second, wake.REPOSITORY, binding, lambda _: pytest.fail("dedupe must not attach"))["reason"] == "ALREADY_SUBMITTED"
    next_adapter.generation = "IDLE"  # IDLE alone still cannot release the new flight.
    wake.operate(wake.REPOSITORY, binding, adapter_factory=lambda _: next_adapter, projection=projection)
    assert len(next_adapter.submits) == 1 and stored(binding)["flight"]["event_id"] == second


@pytest.mark.parametrize("scenario", [
    "absent", "missing_user", "hidden_user", "hidden_wake", "duplicate_user",
    "missing_response", "hidden_response", "no_response_rects", "hidden_assistant", "hidden_wrapper",
    "duplicate_response", "hidden_duplicate_response", "duplicate_ordinal_elsewhere", "duplicate_assistant",
    "nested_assistant", "nested_response", "nested_wake", "nested_pair", "missing_role", "unknown_role",
    "mixed_roles", "role_ambiguous_wrapper", "role_ambiguous_turn", "role_ambiguous_descendant",
    "user_marker_in_assistant", "intervening_user", "virtualized_gap", "placeholder", "later_user",
    "missing_ordinal", "missing_turn_key", "outside_main", "duplicate_main", "identity_bound",
])
def test_idle_without_unambiguous_exact_completion_holds_pending_without_send(binding, scenario):
    assert completion_dom_result(binding, scenario) is False
    page, holder, second = submitted_with_pending(binding, scenario)
    before = stored(binding)
    receipts = wake.operate(wake.REPOSITORY, binding, adapter_factory=lambda _: holder, projection=Projection())
    after = stored(binding)
    assert receipts == [dict(event_id=second, status="DEFERRED", reason="LANE_IN_FLIGHT")]
    assert after["flight"] == before["flight"] == dict(event_id=EVENT, seen_busy=False)
    assert after["events"][EVENT] == before["events"][EVENT]
    assert page.evaluations.count(wake.CLICK) == 1 and len(page.completions) == 1


@pytest.mark.parametrize("fault", ["busy", "draft", "disabled", "account", "login", "nonregular", "target", "duplicate_target", "adapter_error"])
def test_completion_target_surface_and_generation_uncertainty_never_sends_pending(binding, fault):
    page, holder, second = submitted_with_pending(binding)
    if fault == "busy": page.counts[wake.STOP] = 1
    if fault == "draft": page.draft = "Synthetic Human draft"
    if fault == "disabled": page.disabled = True
    if fault == "account": page.counts[wake.ACCOUNT] = 0
    if fault == "login": page.counts[wake.LOGIN] = 1
    if fault == "nonregular": page.counts[wake.NONREGULAR] = 1
    if fault == "target": page.url = synthetic_url(50)
    if fault == "duplicate_target": holder.browser.contexts[0].pages.append(Page(binding.chat_url))
    if fault == "adapter_error":
        holder.completed_wake = lambda _: (_ for _ in ()).throw(RuntimeError("adapter unavailable"))
    receipts = wake.operate(wake.REPOSITORY, binding, adapter_factory=lambda _: holder, projection=Projection())
    assert receipts[-1]["reason"] == "LANE_IN_FLIGHT"
    assert stored(binding)["flight"] == dict(event_id=EVENT, seen_busy=fault == "busy")
    assert not page.completions and page.evaluations.count(wake.CLICK) == 1


@pytest.mark.parametrize("movement", ["generation", "target", "surface", "draft"])
def test_completion_rechecks_exact_idle_target_after_structural_witness(binding, movement):
    page, holder, second = submitted_with_pending(binding)
    def race(target):
        if movement == "generation": target.counts[wake.STOP] = 1
        if movement == "target": target.url = synthetic_url(50)
        if movement == "surface": target.counts[wake.ACCOUNT] = 0
        if movement == "draft": target.draft = "Synthetic Human draft"
    page.completion_race = race
    receipts = wake.operate(wake.REPOSITORY, binding, adapter_factory=lambda _: holder, projection=Projection())
    assert receipts[-1]["reason"] == "LANE_IN_FLIGHT"
    assert stored(binding)["flight"] == dict(event_id=EVENT, seen_busy=False)
    assert page.evaluations.count(wake.CLICK) == 1 and len(page.completions) == 1


@pytest.mark.parametrize("boundary", ["preflight", "insert", "click", "binding", "draft", "generation"])
def test_completion_release_cannot_bypass_second_subject_fresh_send_barriers(binding, boundary):
    page, holder, second = submitted_with_pending(binding)
    projection = Projection()
    selected = [binding]
    calls = []
    if boundary == "preflight":
        projection.value = lambda event: "UNRESOLVED" if event == EVENT else "UNKNOWN"
    def race(stage):
        if stage == boundary:
            projection.value = lambda event: "RESOLVED" if event == second else "UNRESOLVED"
        if boundary == "binding" and stage == "click":
            selected[0] = wake.Binding(binding.chat_url, binding.cdp_endpoint, binding.state_path, 1)
    block = {"draft": "DRAFT_PRESENT", "generation": "GENERATION_ACTIVE"}.get(boundary)
    next_adapter = RecoveryAdapter(binding, block=block, race=race)
    def factory(bound):
        calls.append(bound)
        return holder if len(calls) == 1 else next_adapter
    receipts = wake.operate(wake.REPOSITORY, binding, adapter_factory=factory, projection=projection,
                            binding_provider=lambda: selected[0])
    assert not next_adapter.submits and page.evaluations.count(wake.CLICK) == 1
    assert stored(binding)["flight"] is None
    assert stored(binding)["events"][EVENT] == wake.record("SUBMITTED", binding.generation)
    assert receipts[-1]["status"] == ("NOOP" if boundary in {"insert", "click"} else "DEFERRED")


def test_canonical_resolution_retires_stale_holder_without_completion_or_browser(binding):
    page, holder, second = submitted_with_pending(binding)
    receipts = wake.operate(wake.REPOSITORY, binding,
                            adapter_factory=lambda _: pytest.fail("canonical resolution requires no browser"),
                            projection=Projection("RESOLVED"))
    assert all(receipt["reason"] == "CANONICALLY_RESOLVED" for receipt in receipts)
    assert stored(binding)["flight"] is None
    assert stored(binding)["events"][EVENT]["status"] == "RESOLVED_NOOP"
    assert stored(binding)["events"][second]["status"] == "RESOLVED_NOOP"
    assert not page.completions and page.evaluations.count(wake.CLICK) == 1


def test_ambiguous_attempt_cannot_use_completion_witness_or_automatically_resend(binding):
    page, holder, second = submitted_with_pending(binding, proof=False)
    receipts = wake.operate(wake.REPOSITORY, binding, adapter_factory=lambda _: holder, projection=Projection())
    assert receipts[-1]["reason"] == "LANE_IN_FLIGHT"
    assert stored(binding)["events"][EVENT]["status"] == "AMBIGUOUS"
    assert stored(binding)["flight"] == dict(event_id=EVENT, seen_busy=False)
    assert not page.completions and page.evaluations.count(wake.CLICK) == 1


class SurfaceURLRacePage(Page):
    """An exact page moves between selection and the structural observation."""

    def __init__(self, url):
        super().__init__(url)
        self.url_reads = 0

    @property
    def url(self):
        self.url_reads += 1
        return self.target_url if self.url_reads % 2 else synthetic_url(99)

    @url.setter
    def url(self, value):
        self.target_url = value


def surface_cause_page(binding, conditions):
    page = (SurfaceURLRacePage if "target_url" in conditions else Page)(binding.chat_url)
    for condition in conditions:
        if condition == "disabled":
            page.disabled = True
        elif condition != "target_url":
            selector, number = {
                "main_missing": ("main", 0), "main_multiple": ("main", 2),
                "account_missing": (wake.ACCOUNT, 0), "account_multiple": (wake.ACCOUNT, 2),
                "login": (wake.LOGIN, 1), "nonregular": (wake.NONREGULAR, 1),
                "composer_missing": (wake.COMPOSER, 0), "composer_multiple": (wake.COMPOSER, 2),
            }[condition]
            page.counts[selector] = number
    return page


@pytest.mark.parametrize("conditions,cause", [
    (("target_url",), "TARGET_URL_MISMATCH"),
    (("main_missing",), "MAIN_NOT_UNIQUE"),
    (("main_multiple",), "MAIN_NOT_UNIQUE"),
    (("account_missing",), "ACCOUNT_NOT_UNIQUE"),
    (("account_multiple",), "ACCOUNT_NOT_UNIQUE"),
    (("login",), "LOGIN_PRESENT"),
    (("nonregular",), "NON_REGULAR_SURFACE"),
    (("composer_missing",), "COMPOSER_NOT_UNIQUE"),
    (("composer_multiple",), "COMPOSER_NOT_UNIQUE"),
    (("disabled",), "COMPOSER_DISABLED"),
    (("main_missing", "account_multiple"), "MULTIPLE_OR_AMBIGUOUS"),
    (("login", "nonregular"), "MULTIPLE_OR_AMBIGUOUS"),
    (("target_url", "disabled"), "MULTIPLE_OR_AMBIGUOUS"),
    (("composer_multiple", "login"), "MULTIPLE_OR_AMBIGUOUS"),
    (("main_missing", "account_missing", "login", "nonregular", "composer_missing"),
     "MULTIPLE_OR_AMBIGUOUS"),
])
def test_surface_cause_real_pre_submit_receipt_is_closed_and_operational_only(binding, conditions, cause):
    page = surface_cause_page(binding, conditions)
    page.draft = "Synthetic private Human text; account/session/credential fixture"
    page.outbound = "Synthetic private chat text"
    page.counts[wake.STOP] = 1
    adapter, projection = LocalSurfaceAdapter(binding, page), Projection()
    original_doorbell = wake.doorbell(EVENT, binding.repository)
    receipt = deliver(EVENT, binding.repository, binding, lambda _: adapter, projection=projection)
    assert receipt == dict(event_id=EVENT, status="DEFERRED", reason="SURFACE_UNPROVEN",
                           surface_cause=cause)
    assert isinstance(receipt["surface_cause"], str) and cause in wake.SURFACE_CAUSES
    assert projection.calls == [EVENT]
    assert not page.evaluations and not page.resolutions
    assert page.draft == "Synthetic private Human text; account/session/credential fixture"
    data = stored(binding)
    assert list(data["events"]) == [EVENT]
    assert data["events"][EVENT] == wake.record("DEFERRED", reason="SURFACE_UNPROVEN")
    assert data["flight"] is None and not data["tombstones"]
    assert '"surface_cause":' not in json.dumps(data)
    assert wake.doorbell(EVENT, binding.repository) == original_doorbell
    # Both consumers observe the same structural cause. Active generation may
    # retain its existing BUSY interpretation of a disabled composer only.
    assert adapter._observe_surface()[1] == cause
    if cause == "COMPOSER_DISABLED":
        assert adapter.generation_state() == "BUSY"
        page.counts[wake.STOP] = 0
    with pytest.raises(wake.WakeBlocked, match="^SURFACE_UNPROVEN$") as blocked:
        adapter.generation_state()
    assert blocked.value.surface_cause == cause
    encoded = json.dumps(receipt)
    for private in (page.draft, page.outbound, binding.chat_url, binding.cdp_endpoint,
                    str(binding.state_path), wake.ACCOUNT, wake.COMPOSER, wake.LOGIN, wake.NONREGULAR):
        assert private not in encoded


@pytest.mark.parametrize("supplied", [None, "private DOM/text/url/session/selector", 2, {}, []])
def test_surface_cause_annotation_rejects_unbounded_values(supplied):
    blocked = wake.WakeBlocked("SURFACE_UNPROVEN", surface_cause=supplied)
    assert str(blocked) == "SURFACE_UNPROVEN"
    assert blocked.surface_cause == "MULTIPLE_OR_AMBIGUOUS"
    assert wake.WakeBlocked("DRAFT_PRESENT", surface_cause="LOGIN_PRESENT").surface_cause is None


@pytest.mark.parametrize("condition,reason", [
    ("draft", "DRAFT_PRESENT"), ("generation", "GENERATION_ACTIVE"),
    ("outbound", "OUTBOUND_ALREADY_PRESENT"), ("wrong_target", "TARGET_PAGE_NOT_UNIQUE"),
])
def test_surface_cause_does_not_reclassify_other_pre_submit_guards(binding, condition, reason):
    page = Page(binding.chat_url)
    if condition == "draft":
        page.draft = "Synthetic Human draft"
    elif condition == "generation":
        page.counts[wake.STOP] = 1
    elif condition == "outbound":
        page.counts["outbound"] = 1
    else:
        page.url = synthetic_url(99)
    receipt = deliver(EVENT, binding.repository, binding, lambda _: LocalSurfaceAdapter(binding, page))
    assert receipt == dict(event_id=EVENT, status="DEFERRED", reason=reason)
    assert stored(binding)["events"][EVENT] == wake.record("DEFERRED", reason=reason)
    assert not page.evaluations


def test_surface_cause_deferred_real_surface_recovers_once_with_fresh_barriers(binding):
    page = surface_cause_page(binding, ("login",))
    adapter = LocalSurfaceAdapter(binding, page)
    assert deliver(EVENT, binding.repository, binding, lambda _: adapter)["surface_cause"] == "LOGIN_PRESENT"
    frozen = binding.state_path.read_bytes()
    assert stored(binding)["events"][EVENT] == wake.record("DEFERRED", reason="SURFACE_UNPROVEN")
    page.counts[wake.LOGIN] = 0  # The same page later becomes healthy.
    unknown = Projection("UNKNOWN")
    receipt = wake.operate(binding.repository, binding,
                           adapter_factory=lambda _: pytest.fail("unknown subject must not attach"),
                           projection=unknown)
    assert receipt == [dict(event_id=EVENT, status="DEFERRED", reason="CANONICAL_UNKNOWN")]
    assert unknown.calls == [EVENT] and not page.evaluations
    assert list(stored(binding)["events"]) == [EVENT]
    assert frozen != binding.state_path.read_bytes()  # Subject retained with updated reason.

    observations = []
    def observe(event_id):
        observations.append(tuple(page.evaluations))
        return "UNRESOLVED"
    projection = Projection(observe)
    receipt = wake.operate(binding.repository, binding, adapter_factory=lambda _: adapter,
                           projection=projection)
    assert receipt == [dict(event_id=EVENT, status="SUBMITTED", reason="EXACT_USER_TURN_PROVEN")]
    assert projection.calls == [EVENT, EVENT, EVENT]
    assert observations == [(), (), (wake.INSERT, wake.ACCEPT_INSERT)]
    assert page.evaluations == [wake.INSERT, wake.ACCEPT_INSERT, wake.CLICK, wake.PROVE_SEND]
    assert stored(binding)["events"][EVENT] == wake.record("SUBMITTED", binding.generation)
    assert deliver(EVENT, binding.repository, binding,
                   lambda _: pytest.fail("submitted duplicate must not attach"))["status"] == "NOOP"
    assert page.evaluations.count(wake.INSERT) == page.evaluations.count(wake.CLICK) == 1


def test_surface_cause_deferred_subject_resolves_without_retry_or_insertion(binding):
    page = surface_cause_page(binding, ("account_missing",))
    adapter = LocalSurfaceAdapter(binding, page)
    assert deliver(EVENT, binding.repository, binding, lambda _: adapter)["reason"] == "SURFACE_UNPROVEN"
    page.counts[wake.ACCOUNT] = 1
    projection = Projection("RESOLVED")
    assert wake.operate(binding.repository, binding,
                        adapter_factory=lambda _: pytest.fail("resolved subject must not attach"),
                        projection=projection) == [
        dict(event_id=EVENT, status="NOOP", reason="CANONICALLY_RESOLVED"),
    ]
    assert projection.calls == [EVENT]
    assert stored(binding)["events"][EVENT] == wake.record("RESOLVED_NOOP")
    assert deliver(EVENT, binding.repository, binding,
                   lambda _: pytest.fail("resolved duplicate must not attach"))["status"] == "NOOP"
    assert not page.evaluations


def test_surface_cause_disabled_generation_preserves_busy_then_idle_lane_release(binding):
    page = Page(binding.chat_url)
    adapter = LocalSurfaceAdapter(binding, page)
    assert deliver(EVENT, binding.repository, binding, lambda _: adapter)["status"] == "SUBMITTED"
    page.disabled = True
    page.counts[wake.STOP] = 1
    assert wake.operate(binding.repository, binding, adapter_factory=lambda _: adapter,
                        projection=Projection()) == []
    assert stored(binding)["flight"] == dict(event_id=EVENT, seen_busy=True)
    page.disabled = False
    page.counts[wake.STOP] = 0
    assert wake.operate(binding.repository, binding, adapter_factory=lambda _: adapter,
                        projection=Projection()) == []
    assert stored(binding)["flight"] is None
    assert page.evaluations.count(wake.INSERT) == page.evaluations.count(wake.CLICK) == 1


def test_surface_cause_recovery_post_submit_ambiguity_is_proof_only(binding):
    page = surface_cause_page(binding, ("nonregular",))
    adapter = LocalSurfaceAdapter(binding, page)
    assert deliver(EVENT, binding.repository, binding, lambda _: adapter)["surface_cause"] == "NON_REGULAR_SURFACE"
    page.counts[wake.NONREGULAR] = 0
    page.outbound = "Synthetic unrelated user turn"  # Click occurs but exact proof is absent.
    receipt = wake.operate(binding.repository, binding, adapter_factory=lambda _: adapter,
                           projection=Projection())
    assert receipt == [dict(event_id=EVENT, status="BLOCKED", reason="SUBMISSION_UNPROVEN")]
    assert stored(binding)["events"][EVENT] == wake.record(
        "AMBIGUOUS", binding.generation, "ATTEMPT_REQUIRES_HUMAN")
    attempts = tuple(page.evaluations)
    for _ in range(2):
        assert deliver(EVENT, binding.repository, binding, lambda _: adapter) == dict(
            event_id=EVENT, status="BLOCKED", reason="ATTEMPT_REQUIRES_HUMAN")
        assert tuple(page.evaluations) == attempts
    page.outbound = page.payload
    assert deliver(EVENT, binding.repository, binding, lambda _: adapter) == dict(
        event_id=EVENT, status="SUBMITTED", reason="EXACT_USER_TURN_PROVEN")
    assert page.evaluations.count(wake.INSERT) == page.evaluations.count(wake.CLICK) == 1


# A selector double for the three bounded account and two composer branches.
# It models CSS union identity and visibility without any live browser content.
LEGACY_COMPOSER = '#prompt-textarea[contenteditable="true"]'
STRUCTURAL_COMPOSER = 'main form [contenteditable="true"][role="textbox"][aria-multiline="true"]'
PROFILE_ARIA = 'button[aria-label*="profile" i]'


def surface_element(tag="div", ancestors=(), visible=True, **attrs):
    return SimpleNamespace(tag=tag, ancestors=ancestors, visible=visible, attrs=attrs)


def surface_matches(element, branch):
    attrs = element.attrs
    if branch in ('[data-testid="profile-button"]', '[data-testid="accounts-profile-button"]'):
        return attrs.get("data-testid") == branch.split('"')[1]
    if branch == PROFILE_ARIA:
        return element.tag == "button" and "profile" in attrs.get("aria-label", "").lower()
    if branch == LEGACY_COMPOSER:
        return attrs.get("id") == "prompt-textarea" and attrs.get("contenteditable") == "true"
    if branch == STRUCTURAL_COMPOSER:
        ancestors = element.ancestors
        scoped = "main" in ancestors and "form" in ancestors[ancestors.index("main") + 1:]
        return scoped and all(attrs.get(key) == value for key, value in (
            ("contenteditable", "true"), ("role", "textbox"), ("aria-multiline", "true")))
    pytest.fail("unexpected selector branch")


class SurfaceLocator(Locator):
    def count(self):
        if self.selector in {wake.ACCOUNT, wake.COMPOSER}:
            # Select each element once, rather than summing branch counts.
            return sum(element.visible and any(surface_matches(element, branch.strip())
                       for branch in self.selector.split(",")) for element in self.page.elements)
        return super().count()


class SurfacePage(Page):
    def __init__(self, url, elements):
        super().__init__(url)
        self.elements = elements

    def locator(self, selector):
        super().locator(selector)  # Preserve the fixture's read boundary.
        return SurfaceLocator(self, selector)


def account_element(shape="fallback", **overrides):
    attrs = {"aria-label": "Open FiXtUrE PROFILE menu"} if shape != "legacy" else {}
    if shape in {"legacy", "both"}:
        attrs["data-testid"] = "profile-button"
    if shape == "legacy_accounts":
        attrs = {"data-testid": "accounts-profile-button"}
    attrs.update(overrides)
    return surface_element(tag="button", **attrs)


def composer_element(shape="fallback", ancestors=("main", "form"), **overrides):
    attrs = {"contenteditable": "true"}
    if shape != "legacy":
        attrs.update({"role": "textbox", "aria-multiline": "true"})
    if shape in {"legacy", "both"}:
        attrs["id"] = "prompt-textarea"
    attrs.update(overrides)
    return surface_element(ancestors=ancestors, **attrs)


def test_bounded_selector_contracts_and_unchanged_stop():
    assert wake.ACCOUNT == ('[data-testid="profile-button"], '
                            '[data-testid="accounts-profile-button"], ' + PROFILE_ARIA)
    assert wake.COMPOSER == LEGACY_COMPOSER + ", " + STRUCTURAL_COMPOSER
    assert wake.SEND == '[data-testid="send-button"], button[type="submit"][aria-label="Send"]'
    assert wake.STOP == '[data-testid="stop-button"]'


@pytest.mark.parametrize("account_shape", ["legacy", "legacy_accounts", "fallback", "both"])
@pytest.mark.parametrize("composer_shape", ["legacy", "fallback", "both"])
def test_bounded_surface_compatibility_and_union_identity(binding, account_shape, composer_shape):
    page = SurfacePage(binding.chat_url, [account_element(account_shape), composer_element(composer_shape)])
    adapter = adapter_for(binding, page)
    assert adapter.visible(wake.ACCOUNT).count() == adapter.visible(wake.COMPOSER).count() == 1
    adapter.check(page.payload)
    adapter.submit(page.payload)
    adapter.prove(page.payload)
    assert page.evaluations == [wake.INSERT, wake.ACCEPT_INSERT, wake.CLICK, wake.PROVE_SEND]


@pytest.mark.parametrize("kind", ["account", "composer"])
@pytest.mark.parametrize("number", [0, 2])
def test_bounded_surface_zero_or_ambiguous_matches_block_check_and_prove(binding, kind, number):
    accounts = [account_element() for _ in range(number if kind == "account" else 1)]
    composers = [composer_element() for _ in range(number if kind == "composer" else 1)]
    # Distinct legacy/fallback elements also constitute ambiguity.
    if number == 2:
        (accounts if kind == "account" else composers)[0] = (
            account_element("legacy") if kind == "account" else composer_element("legacy"))
    page = SurfacePage(binding.chat_url, accounts + composers)
    adapter = adapter_for(binding, page)
    with pytest.raises(wake.WakeBlocked, match="SURFACE_UNPROVEN"):
        adapter.submit(page.payload)
    assert page.evaluations == []
    page.counts["outbound"] = 1
    with pytest.raises(wake.WakeBlocked, match="SUBMISSION_UNPROVEN"):
        adapter.prove(page.payload)


@pytest.mark.parametrize("ancestors", [(), ("form",), ("main",), ("form", "main")])
def test_structural_composer_lookalikes_outside_main_form_fail_closed(binding, ancestors):
    page = SurfacePage(binding.chat_url, [account_element(), composer_element(ancestors=ancestors)])
    with pytest.raises(wake.WakeBlocked, match="SURFACE_UNPROVEN"):
        adapter_for(binding, page).submit(page.payload)
    assert page.evaluations == []


@pytest.mark.parametrize("attrs", [
    {"contenteditable": "false"}, {"role": "searchbox"}, {"aria-multiline": "false"},
    {"contenteditable": None}, {"role": None}, {"aria-multiline": None},
])
def test_structural_composer_requires_every_attribute(binding, attrs):
    page = SurfacePage(binding.chat_url, [account_element(), composer_element(**attrs)])
    with pytest.raises(wake.WakeBlocked, match="SURFACE_UNPROVEN"):
        adapter_for(binding, page).check(page.payload)


@pytest.mark.parametrize("tag,label", [("div", "fixture profile"), ("button", "fixture settings")])
def test_profile_fallback_requires_button_and_profile_aria(binding, tag, label):
    page = SurfacePage(binding.chat_url, [
        surface_element(tag=tag, **{"aria-label": label}), composer_element()])
    with pytest.raises(wake.WakeBlocked, match="SURFACE_UNPROVEN"):
        adapter_for(binding, page).check(page.payload)


def test_hidden_bounded_matches_do_not_create_ambiguity(binding):
    hidden_account, hidden_composer = account_element("legacy"), composer_element("legacy")
    hidden_account.visible = hidden_composer.visible = False
    page = SurfacePage(binding.chat_url, [account_element(), composer_element(), hidden_account, hidden_composer])
    adapter_for(binding, page).check(page.payload)


@pytest.mark.parametrize("condition,reason", [
    ("draft", "DRAFT_PRESENT"), ("generation", "GENERATION_ACTIVE"),
    ("login", "SURFACE_UNPROVEN"), ("nonregular", "SURFACE_UNPROVEN"),
    ("outbound", "OUTBOUND_ALREADY_PRESENT"),
])
def test_fallback_surface_retains_preflight_safety(binding, condition, reason):
    page = SurfacePage(binding.chat_url, [account_element(), composer_element()])
    if condition == "draft":
        page.draft = "Synthetic Human draft"
    else:
        selector = {"generation": wake.STOP, "login": wake.LOGIN,
                    "nonregular": wake.NONREGULAR, "outbound": "outbound"}[condition]
        page.counts[selector] = 1
    with pytest.raises(wake.WakeBlocked, match=reason):
        adapter_for(binding, page).submit(page.payload)
    assert page.evaluations == []


@pytest.mark.parametrize("number", [0, 2])
def test_zero_or_multiple_target_pages_block(binding, number):
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[Page(binding.chat_url) for _ in range(number)])])
    with pytest.raises(wake.WakeBlocked, match="TARGET_PAGE_NOT_UNIQUE"):
        wake.BrowserAdapter.select_page(browser, binding.chat_url)


def test_duplicate_target_across_contexts_blocks(binding):
    browser = SimpleNamespace(contexts=[
        SimpleNamespace(pages=[Page(binding.chat_url + suffix)]) for suffix in ("", "/")
    ])
    with pytest.raises(wake.WakeBlocked, match="TARGET_PAGE_NOT_UNIQUE"):
        wake.BrowserAdapter.select_page(browser, binding.chat_url)


def test_unrelated_pages_are_ignored_without_navigation(binding):
    target = Page(binding.chat_url + "/")
    unrelated = [Page(synthetic_url(2)), Page("https://example.invalid/")]
    adapter = adapter_for(binding, target, *unrelated)
    assert adapter.select_page(adapter.browser, binding.chat_url) is target
    adapter.check(target.payload)
    assert not target.evaluations and all(not p.evaluations for p in unrelated)


@pytest.mark.parametrize("unrelated_url", [
    synthetic_project_url(project="g-fixture-project-2"),
    synthetic_project_url(project="g-Fixture-project-1"),
    synthetic_project_url(number=2), synthetic_url(),
    synthetic_project_url() + "/extra", synthetic_project_url() + "?fixture=value",
    synthetic_project_url() + "#fixture",
    synthetic_project_url(project="%67-fixture-project-1"),
])
def test_project_selection_requires_complete_identity_without_inference(unrelated_url):
    url = synthetic_project_url()
    unrelated = Page(unrelated_url)
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[unrelated])])
    with pytest.raises(wake.WakeBlocked, match="TARGET_PAGE_NOT_UNIQUE"):
        wake.BrowserAdapter.select_page(browser, url)
    target = Page(url + "/")
    browser.contexts.append(SimpleNamespace(pages=[target]))
    assert wake.BrowserAdapter.select_page(browser, url) is target
    assert not unrelated.evaluations and not target.evaluations


def test_project_change_with_same_conversation_blocks_before_insert_or_proof(tmp_path):
    url = synthetic_project_url()
    binding = wake.Binding(url, "http://127.0.0.1:9222", tmp_path / "state.json")
    page = Page(url)
    adapter = adapter_for(binding, page)
    page.url = synthetic_project_url(project="g-fixture-project-2")
    with pytest.raises(wake.WakeBlocked, match="TARGET_PAGE_NOT_UNIQUE"):
        adapter.submit(page.payload)
    assert page.evaluations == []
    page.counts["outbound"] = 1
    with pytest.raises(wake.WakeBlocked, match="TARGET_PAGE_NOT_UNIQUE"):
        adapter.prove(page.payload)


@pytest.mark.parametrize("condition,reason", [
    ("logged_out", "SURFACE_UNPROVEN"), ("login", "SURFACE_UNPROVEN"),
    ("wrong_surface", "SURFACE_UNPROVEN"), ("missing_composer", "SURFACE_UNPROVEN"),
    ("disabled", "SURFACE_UNPROVEN"), ("draft", "DRAFT_PRESENT"),
    ("generating", "GENERATION_ACTIVE"), ("prior_outbound", "OUTBOUND_ALREADY_PRESENT"),
])
def test_browser_preflight_blocks_before_composer_modification(binding, condition, reason):
    page = Page(binding.chat_url)
    if condition == "logged_out":
        page.counts[wake.ACCOUNT] = 0
    elif condition == "login":
        page.counts[wake.LOGIN] = 1
    elif condition == "wrong_surface":
        page.counts[wake.NONREGULAR] = 1
    elif condition == "missing_composer":
        page.counts[wake.COMPOSER] = 0
    elif condition == "disabled":
        page.disabled = True
    elif condition == "draft":
        page.draft = "Human draft"
    elif condition == "generating":
        page.counts[wake.STOP] = 1
    else:
        page.counts["outbound"] = 1
    with pytest.raises(wake.WakeBlocked, match=reason):
        adapter_for(binding, page).submit(page.payload)
    assert page.evaluations == []


def test_exact_submission_proof_reads_only_outbound_and_composer(binding):
    page = Page(binding.chat_url)
    adapter = adapter_for(binding, page)
    adapter.submit(page.payload)
    # Generation after send is permitted without extracting its output.
    page.counts[wake.STOP] = 1
    adapter.prove(page.payload)
    assert page.evaluations == [wake.INSERT, wake.ACCEPT_INSERT, wake.CLICK, wake.PROVE_SEND]
    assert len(page.resolutions) == 3
    assert set(page.resolutions) == {wake.RESOLVE_USER_TURN}


@pytest.mark.parametrize("scenario,expected", [
    ("legacy", "EXACT"), ("legacy_child", "EXACT"),
    ("bubble", "EXACT"), ("both", "EXACT"), ("crlf", "EXACT"),
    ("other_bubble", "EXACT"),
    ("zero", "ABSENT"), ("page_only", "ABSENT"), ("assistant_only", "ABSENT"),
    ("partial", "ABSENT"), ("prefix", "ABSENT"), ("suffix", "ABSENT"),
    ("spaces", "ABSENT"), ("double_newline", "ABSENT"), ("case", "ABSENT"),
    ("cr_only", "ABSENT"), ("hidden", "AMBIGUOUS"), ("no_rects", "AMBIGUOUS"),
    ("hidden_legacy", "AMBIGUOUS"), ("no_turn", "AMBIGUOUS"),
    ("nested_turns", "AMBIGUOUS"), ("duplicate_bubbles", "AMBIGUOUS"),
    ("duplicate_same_turn", "AMBIGUOUS"), ("hidden_duplicate", "AMBIGUOUS"),
    ("duplicate_legacy", "AMBIGUOUS"), ("mixed_distinct", "AMBIGUOUS"),
    ("nested_markers", "AMBIGUOUS"), ("nested_mismatched_marker", "AMBIGUOUS"),
    ("other_bubble_same_turn", "AMBIGUOUS"), ("assistant_ancestor", "AMBIGUOUS"),
    ("both_no_turn", "AMBIGUOUS"), ("assistant_descendant", "AMBIGUOUS"),
])
def test_bounded_outbound_resolver_and_shared_pre_post_send_contract(binding, scenario, expected):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the isolated JavaScript DOM harness")
    args = wake.BrowserAdapter(binding).arguments(wake.doorbell(EVENT, wake.REPOSITORY))
    harness = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8')), args = input.args;
const nodes = [];
function element(attrs = {}, parentElement = null, body = '') {
  const e = {attrs, parentElement, body, hidden: false, noRects: false,
    getAttribute(name) {return this.attrs[name] ?? null;},
    getClientRects() {return this.noRects ? [] : [1];},
    matches(selector) {
      if (selector === args.userTurn) return this.attrs['data-message-author-role'] === 'user';
      if (selector === args.userBubble) return 'data-user-message-bubble' in this.attrs;
      if (selector === args.turnContainer) return 'data-turn-key' in this.attrs;
      throw Error('unbounded selector');
    },
    contains(child) {
      for (let p = child; p; p = p.parentElement) if (p === this) return true;
      return false;
    },
    querySelector(selector) {
      if (selector !== '[data-message-author-role]:not([data-message-author-role="user"])')
        throw Error('unbounded descendant query');
      return nodes.find(e => e !== this && this.contains(e) &&
        'data-message-author-role' in e.attrs && e.attrs['data-message-author-role'] !== 'user') ?? null;
    },
    get textContent() {
      if (this.unreadable) throw Error('unbounded text read');
      return this.body + nodes.filter(n => n.parentElement === this).map(n => n.textContent).join('');
    }
  };
  nodes.push(e); return e;
}
const root = element(), turn = element({'data-turn-key': 'fixture'}, root);
const bubble = parent => element({'data-user-message-bubble': ''}, parent, args.text);
const legacy = parent => element({'data-message-author-role': 'user'}, parent, args.text);
// Unmarked page text and assistant output must never be read, even if exact.
const pageText = element({}, root, args.text), assistant = element(
  {'data-message-author-role': 'assistant'}, root, args.text);
pageText.unreadable = assistant.unreadable = true;
const name = input.scenario;
let candidate;
if (['zero', 'page_only', 'assistant_only'].includes(name)) {
  // No user-specific candidates, despite page-global exact text.
} else if (['legacy', 'legacy_child', 'hidden_legacy', 'duplicate_legacy'].includes(name)) {
  candidate = legacy(root); // Historical fixtures need no turn-key ancestor.
  if (name === 'legacy_child') {candidate.body = ''; element({}, candidate, args.text);}
  if (name === 'hidden_legacy') candidate.hidden = true;
  if (name === 'duplicate_legacy') legacy(root);
} else {
  candidate = bubble(turn);
  if (['both', 'both_no_turn'].includes(name)) candidate.attrs['data-message-author-role'] = 'user';
  if (name === 'crlf') candidate.body = args.text.replace(/\n/g, '\r\n');
  if (name === 'partial') candidate.body = args.text.split('\n')[1];
  if (name === 'prefix') candidate.body = ' ' + args.text;
  if (name === 'suffix') candidate.body = args.text + '\n';
  if (name === 'spaces') candidate.body = args.text.replace(/\n/g, ' ');
  if (name === 'double_newline') candidate.body = args.text.replace(/\n/g, '\n\n');
  if (name === 'case') candidate.body = args.text.toLowerCase();
  if (name === 'cr_only') candidate.body = args.text.replace(/\n/g, '\r');
  if (name === 'hidden') candidate.hidden = true;
  if (name === 'no_rects') candidate.noRects = true;
  if (['no_turn', 'both_no_turn'].includes(name)) candidate.parentElement = root;
  if (name === 'nested_turns') candidate.parentElement = element({'data-turn-key': 'nested'}, turn);
  if (['duplicate_bubbles', 'hidden_duplicate'].includes(name)) {
    const duplicate = bubble(element({'data-turn-key': 'second'}, root));
    duplicate.hidden = name === 'hidden_duplicate';
  }
  if (name === 'duplicate_same_turn') bubble(turn);
  if (name === 'mixed_distinct') legacy(root);
  if (['nested_markers', 'nested_mismatched_marker'].includes(name)) {
    const wrapper = element({'data-message-author-role': 'user'}, turn,
      name === 'nested_mismatched_marker' ? 'extra' : '');
    candidate.parentElement = wrapper;
  }
  if (['other_bubble', 'other_bubble_same_turn'].includes(name)) {
    bubble(name === 'other_bubble' ? element({'data-turn-key': 'other'}, root) : turn).body =
      'Unrelated synthetic user message';
  }
  if (name === 'assistant_ancestor') candidate.parentElement = assistant;
  if (name === 'assistant_descendant') {
    const child = element({'data-message-author-role': 'assistant'}, candidate, args.text);
    child.unreadable = true;
  }
}
global.getComputedStyle = e => ({visibility: e.hidden ? 'hidden' : 'visible'});
global.document = {querySelectorAll(selector) {
  if (selector !== args.userTurn + ', ' + args.userBubble) throw Error('page-global discovery');
  return nodes.filter(e => e.matches(args.userTurn) || e.matches(args.userBubble));
}};
const resolved = eval('(' + input.resolve + ')')(args);
let waited = false, waitRejected = false;
try {waited = eval('(' + input.wait + ')')(args);}
catch (error) {
  if (error.message !== 'SUBMISSION_UNPROVEN') throw error;
  waitRejected = true;
}
process.stdout.write(JSON.stringify({resolved, waited, waitRejected}));
"""
    process = subprocess.run(
        [node, "-e", harness], input=json.dumps({"args": args, "scenario": scenario,
                                               "resolve": wake.RESOLVE_USER_TURN,
                                               "wait": wake.WAIT_USER_TURN}),
        capture_output=True, text=True, check=True, timeout=10,
    )
    result = json.loads(process.stdout)
    assert result == {"resolved": expected, "waited": expected == "EXACT",
                      "waitRejected": expected == "AMBIGUOUS"}

    class ResolvedPage(Page):
        def evaluate(self, script, arguments):
            if script == wake.RESOLVE_USER_TURN:
                assert arguments == args
                self.resolutions.append(script)
                return result["resolved"]
            return super().evaluate(script, arguments)

    page = ResolvedPage(binding.chat_url)
    adapter = adapter_for(binding, page)
    if expected == "ABSENT":
        adapter.check(page.payload)
    else:
        with pytest.raises(wake.WakeBlocked, match="OUTBOUND_ALREADY_PRESENT"):
            adapter.check(page.payload)
    if expected == "EXACT":
        adapter.prove(page.payload)
        assert page.evaluations == [wake.PROVE_SEND]
    else:
        with pytest.raises(wake.WakeBlocked, match="SUBMISSION_UNPROVEN"):
            adapter.prove(page.payload)
        assert not page.evaluations
    assert set(page.resolutions) == {wake.RESOLVE_USER_TURN}


@pytest.mark.parametrize("condition", ["missing_turn", "duplicate_turn", "wrong_text", "draft", "send_enabled", "send_multiple", "wrong_chat", "second_target"])
def test_submission_proof_fails_closed(binding, condition):
    page = Page(binding.chat_url)
    adapter = adapter_for(binding, page)
    adapter.submit(page.payload)
    if condition == "missing_turn":
        page.counts["outbound"] = 0
    elif condition == "duplicate_turn":
        page.counts["outbound"] = 2
    elif condition == "wrong_text":
        # No whitespace-normalized substitute is eligible for bounded proof.
        page.outbound = page.payload.replace("\n", " ")
    elif condition == "draft":
        page.draft = "Human draft"
    elif condition == "send_enabled":
        page.send_enabled = True
    elif condition == "send_multiple":
        page.counts[wake.SEND] = 2
    elif condition == "wrong_chat":
        page.url = synthetic_url(2)
    else:
        adapter.browser.contexts[0].pages.append(Page(binding.chat_url))
    with pytest.raises((wake.WakeBlocked, RuntimeError)):
        adapter.prove(page.payload)


@pytest.mark.parametrize("stage", ["insert", "send"])
def test_human_draft_race_blocks_without_clearing_human_text(binding, stage):
    page = Page(binding.chat_url)
    page.race = stage
    with pytest.raises(wake.WakeBlocked, match="INSERT_BLOCKED|SEND_BLOCKED"):
        adapter_for(binding, page).submit(page.payload)
    assert "Human" in page.draft
    assert page.counts.get("outbound", 0) == 0


@pytest.mark.parametrize("send_count,enabled", [(0, False), (2, True), (1, False)])
def test_application_acceptance_blocks_before_click(binding, send_count, enabled):
    class UnacceptedPage(Page):
        def evaluate(self, script, args):
            result = super().evaluate(script, args)
            if script == wake.INSERT:
                self.counts[wake.SEND] = send_count
                self.send_enabled = enabled
            return result

    page = UnacceptedPage(binding.chat_url)
    with pytest.raises(wake.WakeBlocked, match="INSERT_BLOCKED"):
        adapter_for(binding, page).submit(page.payload)
    assert wake.CLICK not in page.evaluations
    assert page.draft == page.payload and page.counts.get("outbound", 0) == 0


def test_target_uniqueness_rechecked_after_staging(binding):
    page = Page(binding.chat_url)
    adapter = adapter_for(binding, page)
    evaluate = page.evaluate

    def duplicate_after_insert(script, args):
        result = evaluate(script, args)
        if script == wake.INSERT:
            adapter.browser.contexts[0].pages.append(Page(binding.chat_url))
        return result

    page.evaluate = duplicate_after_insert
    with pytest.raises(wake.WakeBlocked, match="TARGET_PAGE_NOT_UNIQUE"):
        adapter.submit(page.payload)
    assert page.evaluations == [wake.INSERT] and page.draft == page.payload


def test_human_edit_during_application_wait_is_preserved(binding, monkeypatch):
    page = Page(binding.chat_url)
    wait = Page.wait_for_function

    def edit_while_waiting(target, script, **kwargs):
        wait(target, script, **kwargs)
        page.draft += " Human edit"

    monkeypatch.setattr(Page, "wait_for_function", edit_while_waiting)
    with pytest.raises(wake.WakeBlocked, match="INSERT_BLOCKED"):
        adapter_for(binding, page).submit(page.payload)
    assert page.draft == page.payload + " Human edit"
    assert wake.CLICK not in page.evaluations and page.counts.get("outbound", 0) == 0


def test_client_attaches_and_disconnects_without_browser_launch(monkeypatch, binding):
    page = Page(binding.chat_url)
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[page])])
    calls = []
    def connect(endpoint, *, timeout):
        calls.append((endpoint, timeout))
        return browser
    driver = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect), stop=lambda: calls.append("disconnect"))
    module = SimpleNamespace(sync_playwright=lambda: SimpleNamespace(start=lambda: driver))
    import sys
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    page.set_default_timeout = lambda timeout: calls.append(timeout)
    with wake.BrowserAdapter(binding) as adapter:
        assert adapter.page is page
    assert calls == [(binding.cdp_endpoint, 10000), 3000, "disconnect"]


def test_scripts_guard_identity_surface_and_human_edit_in_same_turn():
    for script in (wake.INSERT, wake.ACCEPT_INSERT, wake.CLICK):
        for guard in ("location.href.replace", "visible(account).length === 1", "visible(stop).length",
                      "visible(nonregular).length", "visible(login).length"):
            assert guard in script
    assert "box.textContent === ''" in wake.INSERT
    assert "visible(composer)[0] === box" in wake.INSERT
    assert wake.INSERT.index("box.focus()") < wake.INSERT.index("if (!empty() ||") < wake.INSERT.index("document.execCommand")
    assert "equivalent(boxes[0])" in wake.CLICK
    assert "const button = ready();" in wake.CLICK and "button.click()" in wake.CLICK
    for script in (wake.ACCEPT_INSERT, wake.CLICK, wake.PROVE_SEND):
        assert wake._SEND_GUARDS in script
        assert "forms[0].querySelectorAll(send)" in script
        assert "visible(send)" not in script
        assert "document.querySelectorAll(send)" not in script


@pytest.mark.parametrize("shape", ["legacy", "live", "both"])
@pytest.mark.parametrize("phase", ["ready", "click", "proof"])
@pytest.mark.parametrize("scenario,ready,proven", [
    ("single", True, False),
    ("absent", False, True),
    ("disabled", False, True),
    ("aria_disabled", False, True),
    ("multiple", False, False),
    ("multiple_disabled", False, False),
    ("mixed_shapes", False, False),
    ("hidden_extra", True, False),
    ("hidden_only", False, True),
    ("outside_only", False, True),
    ("other_form_only", False, True),
    ("outside_extra", True, False),
    ("unrelated_inside", False, True),
    ("no_form", False, False),
    ("nested_forms", False, False),
    ("no_composer", False, False),
    ("multiple_composers", False, False),
])
def test_exact_form_send_resolver_on_synthetic_dom(binding, shape, phase, scenario, ready, proven):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the isolated JavaScript DOM harness")
    args = wake.BrowserAdapter(binding).arguments(wake.doorbell(EVENT, wake.REPOSITORY))
    harness = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const args = input.args;
global.location = {href: args.url};
global.getComputedStyle = e => ({visibility: e.hidden ? 'hidden' : 'visible'});
let clicks = 0, formQueries = 0;
const element = (tagName, attrs = {}, parentElement = null) => ({
  tagName, attrs, parentElement, disabled: false,
  getAttribute(name) {return this.attrs[name] ?? null;},
  getClientRects() {return this.noRects ? [] : [1];},
  click() {clicks++;}
});
const main = element('MAIN'), form = element('FORM', {}, main);
const otherForm = element('FORM', {}, main), nested = element('FORM', {}, form);
const box = Object.assign(element('DIV', {}, form), {
  innerText: args.text, textContent: args.text, childNodes: []
});
let boxes = [box], controls = [];
const legacy = '[data-testid="send-button"]';
const live = 'button[type="submit"][aria-label="Send"]';
function matches(e, branch) {
  if (branch === legacy) return e.attrs['data-testid'] === 'send-button';
  if (branch === live) return e.tagName === 'BUTTON' && e.attrs.type === 'submit' &&
    e.attrs['aria-label'] === 'Send';
  throw Error('unbounded selector');
}
function contains(root, e) {
  for (let parent = e.parentElement; parent; parent = parent.parentElement)
    if (parent === root) return true;
  return false;
}
for (const root of [form, otherForm, nested]) {
  root.contains = e => contains(root, e);
  root.querySelectorAll = selector => {
    if (root !== form || selector !== args.send) throw Error('wrong control surface');
    formQueries++;
    // CSS union deduplicates an element matching both exact branches.
    return controls.filter(e => contains(root, e) &&
      selector.split(',').some(branch => matches(e, branch.trim())));
  };
}
function control(shape = input.shape, parent = form) {
  const attrs = {};
  if (shape !== 'live') attrs['data-testid'] = 'send-button';
  if (shape !== 'legacy') Object.assign(attrs, {type: 'submit', 'aria-label': 'Send'});
  return element('BUTTON', attrs, parent);
}
function configure(name) {
  box.parentElement = form; boxes = [box]; controls = [control()];
  if (name === 'absent') controls = [];
  if (name === 'disabled') controls[0].disabled = true;
  if (name === 'aria_disabled') controls[0].attrs['aria-disabled'] = 'true';
  if (name === 'multiple') controls.push(control());
  if (name === 'multiple_disabled') {
    controls.push(control()); controls.forEach(e => e.disabled = true);
  }
  if (name === 'mixed_shapes') controls = [control('legacy'), control('live')];
  if (name === 'hidden_extra') {
    controls.push(Object.assign(control(), {hidden: true}));
    controls.push(Object.assign(control(), {noRects: true}));
  }
  if (name === 'hidden_only') controls[0].hidden = true;
  if (name === 'outside_only') controls = [control('legacy', main), control('live', main)];
  if (name === 'other_form_only') controls = [control('legacy', otherForm), control('live', otherForm)];
  if (name === 'outside_extra') controls.push(control('legacy', main), control('live', otherForm));
  if (name === 'unrelated_inside') controls = [
    element('BUTTON', {type: 'button', 'aria-label': 'Send'}, form),
    element('BUTTON', {type: 'submit', 'aria-label': 'send'}, form),
    element('BUTTON', {type: 'submit', 'aria-label': 'Send feedback'}, form),
    element('DIV', {type: 'submit', 'aria-label': 'Send'}, form),
    element('BUTTON', {type: 'submit', 'aria-label': 'Other'}, form)
  ];
  if (name === 'no_form') box.parentElement = main;
  if (name === 'nested_forms') box.parentElement = nested;
  if (name === 'no_composer') boxes = [];
  if (name === 'multiple_composers') boxes.push(element('DIV', {}, form));
}
global.document = {querySelectorAll(selector) {
  if (selector === args.send) throw Error('global Send discovery');
  if (selector === args.composer) return boxes;
  if (selector === args.account) return [element('BUTTON')];
  if (selector === 'main') return [main];
  if ([args.stop, args.login, args.nonregular].includes(selector)) return [];
  throw Error('unexpected page query');
}};
configure(input.phase === 'click' ? 'single' : input.scenario);
if (input.phase === 'click') {
  if (!eval('(' + input.accept + ')')(args)) throw Error('baseline readiness failed');
  configure(input.scenario); // Re-resolve after a change at the final click boundary.
}
if (input.phase === 'proof') box.innerText = box.textContent = '';
const script = input.phase === 'ready' ? input.accept : input.phase === 'click' ? input.click : input.proof;
const result = eval('(' + script + ')')(args);
process.stdout.write(JSON.stringify({result, clicks, formQueries}));
"""
    process = subprocess.run(
        [node, "-e", harness], input=json.dumps({"args": args, "shape": shape,
                                               "phase": phase, "scenario": scenario,
                                               "accept": wake.ACCEPT_INSERT, "click": wake.CLICK,
                                               "proof": wake.PROVE_SEND}),
        capture_output=True, text=True, check=True, timeout=10,
    )
    result = json.loads(process.stdout)
    assert result["result"] is (proven if phase == "proof" else ready)
    assert result["clicks"] == (1 if phase == "click" and ready else 0)
    if scenario not in {"no_form", "nested_forms", "no_composer", "multiple_composers"}:
        assert result["formQueries"] >= 1


@pytest.mark.parametrize("scenario", [
    "valid", "draft", "focus_draft", "focus_navigation", "busy", "wrong_surface",
    "logged_out", "send_edit", "send_disabled", "wrong_project", "focus_project", "send_project",
    "fallback", "overlap", "legacy_accounts", "hidden_extras", "login", "missing_composer",
    "duplicate_account", "duplicate_composer", "focus_account_ambiguous", "focus_composer_ambiguous",
    "send_account_missing", "send_account_ambiguous", "send_composer_missing", "send_composer_ambiguous",
    "dom_only", "input_edit", "input_navigation", "input_busy", "input_login", "input_main_missing",
    "insert_disabled", "send_zero", "send_multiple", "send_aria_disabled", "send_busy", "send_login",
    "blocks_double", "blocks_missing_separator", "blocks_mixed", "blocks_root_text",
    "flat_double", "trailing_newline", "leading_space", "case_changed", "missing_line",
    "extra_line", "reordered_lines", "collapsed_spaces", "blocks_triple", "blocks_extra_node",
    "blocks_nested", "blocks_hidden", "blocks_trailing", "blocks_substituted",
    "send_flat_double", "send_trailing_newline", "send_blocks_substituted",
    "send_blocks_double", "send_blocks_missing_separator", "send_surface_disabled",
    "blocks_single", "flat_missing_separator", "flat_triple", "trailing_space", "crlf",
    "blocks_hidden_extra", "native_edit", "focus_lost", "selection_missing",
    "insert_multiple", "insert_aria_disabled",
])
def test_real_browser_scripts_on_synthetic_dom(binding, scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the isolated JavaScript DOM harness")
    payload = wake.doorbell(EVENT, wake.REPOSITORY)
    arguments = wake.BrowserAdapter(binding).arguments(payload)
    # Execute the actual browser programs; no browser, network or history exists.
    harness = r"""
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const args = input.args;
global.location = {href: args.url};
global.getComputedStyle = e => ({visibility: e.hidden ? 'hidden' : 'visible'});
let clicks = 0, inserts = 0, notifications = 0, appText = '';
const element = () => ({getClientRects: () => [1], getAttribute: () => null});
const textNode = text => ({nodeType: 3, textContent: text});
const box = Object.assign(element(), {textContent: '', innerText: '', childNodes: [], focus() {
  document.activeElement = box;
  if (input.scenario === 'focus_draft') box.textContent = box.innerText = 'Human draft';
  if (input.scenario === 'focus_navigation') location.href = 'https://example.invalid/';
  if (input.scenario === 'focus_project') location.href = input.other_url;
  if (input.scenario === 'focus_account_ambiguous') elements[accountLegacy].push(element());
  if (input.scenario === 'focus_composer_ambiguous') elements[composerLegacy].push(element());
  if (input.scenario === 'focus_lost') document.activeElement = null;
}, dispatchEvent(event) {
  if (event.type !== 'input' || !event.bubbles || !event.composed ||
      event.inputType !== 'insertText' || event.data !== args.text) throw Error('bad notification');
  notifications++;
  if (input.scenario === 'input_edit') setFlat(args.text + ' Human edit');
  if (input.scenario === 'input_navigation') location.href = input.other_url;
  if (input.scenario === 'input_busy') elements[args.stop] = [element()];
  if (input.scenario === 'input_login') elements[args.login] = [element()];
  if (input.scenario === 'input_main_missing') elements.main = [];
  if (input.scenario !== 'dom_only') {
    appText = box.innerText;
    replace(args.send, [button]);
    button.disabled = input.scenario === 'insert_disabled';
    if (input.scenario === 'insert_multiple') replace(args.send, [button, element()]);
    if (input.scenario === 'insert_aria_disabled') button.getAttribute = () => 'true';
  }
  return true;
}});
const button = Object.assign(element(), {disabled: false, click() {clicks++;}});
const accountLegacy = '[data-testid="profile-button"]';
const accountOtherLegacy = '[data-testid="accounts-profile-button"]';
const accountFallback = 'button[aria-label*="profile" i]';
const composerLegacy = '#prompt-textarea[contenteditable="true"]';
const composerFallback = 'main form [contenteditable="true"][role="textbox"][aria-multiline="true"]';
const account = element();
const fallback = input.scenario === 'fallback', overlap = input.scenario === 'overlap';
const otherLegacy = input.scenario === 'legacy_accounts';
const elements = {
  [composerLegacy]: fallback ? [] : [box], [composerFallback]: fallback || overlap ? [box] : [],
  [accountLegacy]: fallback || otherLegacy ? [] : [account],
  [accountOtherLegacy]: otherLegacy ? [account] : [],
  [accountFallback]: fallback || overlap ? [account] : [],
  [args.send]: [], [args.stop]: [], [args.nonregular]: [], [args.login]: [], main: [element()]};
// CSS selector lists produce a union of element identities, not branch counts.
const select = selector => [...new Set(selector.split(',').flatMap(branch => elements[branch.trim()] || []))];
const replace = (selector, matches) => {
  for (const branch of selector.split(',')) elements[branch.trim()] = matches;
};
const form = {tagName: 'FORM', parentElement: null,
  contains: e => e === box || select(args.send).includes(e), querySelectorAll: select};
box.parentElement = form;
global.document = {
  querySelectorAll: selector => {
    if (selector === args.send) throw Error('page-level Send discovery');
    return select(selector);
  },
  createRange: () => ({selectNodeContents(node) {
    if (node !== box || box.textContent !== '') throw Error('unsafe selection');
  }, collapse(value) {if (value !== true) throw Error('noncollapsed selection');}}),
  execCommand: (command, unused, text) => {
    if (command !== 'insertText') throw Error('unexpected modification');
    inserts++; setRepresentation(input.scenario.startsWith('send_') ? 'valid' : input.scenario, text);
    if (input.scenario === 'native_edit') setFlat(text + ' Human edit');
    return true;
  },
};
global.window = {getSelection: () => input.scenario === 'selection_missing' ? null :
  ({removeAllRanges() {}, addRange() {}})};
global.InputEvent = class {constructor(type, options) {this.type = type; Object.assign(this, options);}};
function setFlat(text) {
  box.textContent = box.innerText = text; box.childNodes = [textNode(text)];
}
function setRepresentation(scenario, text) {
  const name = scenario.replace(/^send_/, '');
  const lines = text.split('\n');
  if (name.startsWith('blocks_')) {
    const contents = name === 'blocks_substituted' ? lines.map((s, i) => i === 2 ? s + ' ' : s) : lines;
    box.childNodes = contents.map((line, i) => Object.assign(element(), {
      nodeType: 1, tagName: name === 'blocks_mixed' && i % 2 ? 'DIV' : 'P',
      textContent: line, childNodes: [textNode(line)]}));
    if (name === 'blocks_root_text') box.childNodes[0] = textNode(contents[0]);
    box.textContent = contents.join('');
    box.innerText = contents.join(name === 'blocks_missing_separator' ? '' :
      name === 'blocks_single' ? '\n' : name === 'blocks_triple' ? '\n\n\n' : '\n\n');
    if (name === 'blocks_mixed') box.innerText = contents[0] + '\n' + contents[1] + '\n\n' + contents[2] + contents[3];
    if (name === 'blocks_extra_node') box.childNodes.push(textNode(''));
    if (name === 'blocks_nested') box.childNodes[1].childNodes = [{nodeType: 1}];
    if (name === 'blocks_hidden') box.childNodes[1].hidden = true;
    if (name === 'blocks_trailing') box.innerText += '\n';
    if (name === 'blocks_hidden_extra') {
      box.childNodes.push(Object.assign(textNode('extra'), {hidden: true}));
      box.textContent += 'extra';
      box.innerText = text;
    }
    return;
  }
  const substitutions = {
    flat_double: text.replace(/\n/g, '\n\n'), trailing_newline: text + '\n',
    leading_space: ' ' + text, case_changed: text.toLowerCase(),
    missing_line: lines.slice(1).join('\n'), extra_line: text + '\nextra',
    reordered_lines: [lines[1], lines[0], ...lines.slice(2)].join('\n'),
    collapsed_spaces: text.replace(/ /g, ''),
    flat_missing_separator: lines.join(''), flat_triple: lines.join('\n\n\n'),
    trailing_space: text + ' ', crlf: lines.join('\r\n'),
  };
  setFlat(substitutions[name] === undefined ? text : substitutions[name]);
}
if (input.scenario === 'draft') box.textContent = box.innerText = 'Human draft';
if (input.scenario === 'busy') elements[args.stop] = [element()];
if (input.scenario === 'wrong_surface') replace(args.nonregular, [element()]);
if (input.scenario === 'login') replace(args.login, [element()]);
if (input.scenario === 'logged_out') replace(args.account, []);
if (input.scenario === 'missing_composer') replace(args.composer, []);
if (input.scenario === 'duplicate_account') replace(args.account, [account, element()]);
if (input.scenario === 'duplicate_composer') replace(args.composer, [box, element()]);
if (input.scenario === 'hidden_extras') {
  elements[accountFallback].push(Object.assign(element(), {hidden: true}));
  elements[composerFallback].push(Object.assign(element(), {getClientRects: () => []}));
}
if (input.scenario === 'wrong_project') location.href = input.other_url;
const inserted = eval('(' + input.insert + ')')(args);
const accepted = inserted && eval('(' + input.accept + ')')(args);
if (input.scenario === 'send_edit') box.textContent = box.innerText = args.text + ' Human edit';
if (input.scenario === 'send_disabled') button.disabled = true;
if (input.scenario === 'send_zero') replace(args.send, []);
if (input.scenario === 'send_multiple') replace(args.send, [button, element()]);
if (input.scenario === 'send_aria_disabled') button.getAttribute = () => 'true';
if (input.scenario === 'send_surface_disabled') box.getAttribute = () => 'true';
if (input.scenario === 'send_busy') elements[args.stop] = [element()];
if (input.scenario === 'send_login') elements[args.login] = [element()];
if (['send_flat_double', 'send_trailing_newline', 'send_blocks_substituted',
     'send_blocks_double', 'send_blocks_missing_separator'].includes(input.scenario))
  setRepresentation(input.scenario, args.text);
if (input.scenario === 'send_project') location.href = input.other_url;
if (input.scenario === 'send_account_missing') replace(args.account, []);
if (input.scenario === 'send_account_ambiguous') replace(args.account, [account, element()]);
if (input.scenario === 'send_composer_missing') replace(args.composer, []);
if (input.scenario === 'send_composer_ambiguous') replace(args.composer, [box, element()]);
const sent = accepted ? eval('(' + input.click + ')')(args) : false;
process.stdout.write(JSON.stringify({inserted, accepted, sent, inserts, clicks,
                                   notifications, appText, draft: box.textContent}));
"""
    process = subprocess.run(
        [node, "-e", harness], input=json.dumps({"args": arguments, "scenario": scenario,
                                               "other_url": synthetic_project_url(project="g-fixture-project-2"),
                                               "insert": wake.INSERT, "accept": wake.ACCEPT_INSERT,
                                               "click": wake.CLICK}),
        capture_output=True, text=True, check=True, timeout=10,
    )
    result = json.loads(process.stdout)
    if scenario in {"valid", "fallback", "overlap", "legacy_accounts", "hidden_extras",
                    "blocks_double", "blocks_single", "blocks_missing_separator", "blocks_mixed", "blocks_root_text",
                    "send_blocks_double", "send_blocks_missing_separator"}:
        assert result["inserted"] is result["accepted"] is result["sent"] is True
        assert result["inserts"] == result["notifications"] == result["clicks"] == 1
        assert result["appText"]  # Application state was activated by the input notification.
        if not scenario.startswith("blocks_") and not scenario.startswith("send_blocks_"):
            assert result["draft"] == result["appText"] == payload
    else:
        assert result["sent"] is False and result["clicks"] == 0
        if scenario in {"draft", "focus_draft", "focus_navigation", "focus_project", "busy",
                        "wrong_surface", "logged_out", "wrong_project", "login", "missing_composer",
                        "duplicate_account", "duplicate_composer", "focus_account_ambiguous",
                        "focus_composer_ambiguous", "focus_lost", "selection_missing"}:
            assert result["inserted"] is False and result["inserts"] == 0
        if scenario in {"flat_double", "trailing_newline", "leading_space", "case_changed",
                        "missing_line", "extra_line", "reordered_lines", "collapsed_spaces",
                        "blocks_triple", "blocks_extra_node", "blocks_nested", "blocks_hidden",
                        "blocks_trailing", "blocks_substituted", "blocks_hidden_extra",
                        "flat_missing_separator", "flat_triple", "trailing_space", "crlf",
                        "native_edit", "input_edit", "input_navigation", "input_busy",
                        "input_login", "input_main_missing"}:
            assert result["inserted"] is False and result["accepted"] is False
        if scenario in {"send_flat_double", "send_trailing_newline", "send_blocks_substituted"}:
            assert result["accepted"] is True  # A later payload substitution is rejected at click.
        if scenario == "dom_only":
            assert result["inserted"] is True and result["accepted"] is False
            assert result["draft"] == payload and result["appText"] == ""
        if scenario in {"insert_disabled", "insert_multiple", "insert_aria_disabled"}:
            assert result["accepted"] is False
        if scenario in {"draft", "focus_draft"}:
            assert result["draft"] == "Human draft" and result["notifications"] == 0
        if scenario in {"send_edit", "input_edit", "native_edit"}:
            assert result["draft"] == payload + " Human edit"
