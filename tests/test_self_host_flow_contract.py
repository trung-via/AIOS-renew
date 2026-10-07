"""Bounded documentation/navigation regressions; no execution or live wake.

Runtime owns running these checks and constructing canonical EVIDENCE. These
checks supplement the existing context, dispatch and wake safety tests.
"""

from copy import deepcopy
import hashlib
from pathlib import Path
import re

import pytest
import yaml

from aios_renew.brain_context import BrainContextError, load_flow_cards


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ENTRY = "AIOS-SELF-HOST-END-TO-END-FLOW-v1.md"
REDIRECTS = (
    "CHATGPT_PROJECT_CONTRACT.md",
    "AIOS-BRAIN-PORTABILITY.md",
    "AIOS-H4-ORIGIN-AFFINE-UNATTENDED-WAKE-v1.md",
    "AIOS-LOCAL-REGULAR-CHAT-WAKE-v1.md",
    "AIOS-H4A3-ACK-ONLY-EXACT-CHAT-CONFORMANCE-v1.md",
    "AIOS-H4A4-DURABLE-WAKE-RECOVERY-CONFORMANCE-v1.md",
    "AIOS-H4A5-COMPLETE-ATTENTION-COVERAGE-CONFORMANCE-v1.md",
    "AIOS-H4B-REGULAR-CHAT-BRAIN-RESUME-CONFORMANCE-v1.md",
    "AIOS-H4C0-ORIGIN-CAPTURE-CONFORMANCE-v1.md",
    "AIOS-RENEW-BRAIN-TASK-AUTHORING-CONTRACT.md",
    "AIOS-BRAIN-RUNTIME-SEMANTIC-HANDOFF-HARDENING-v1.md",
    "AIOS-GITHUB-CHATGPT-BRAIN-WAKE-AUTOMATION-AUDIT-v1.md",
)


def document(name=ENTRY):
    return (DOCS / name).read_text(encoding="utf-8")


def section(text, heading):
    """A named section includes subheadings, ending at its next peer/parent."""
    lines = text.splitlines()
    index = lines.index(heading)
    level = len(heading) - len(heading.lstrip("#"))
    end = next((i for i in range(index + 1, len(lines))
                if re.match(r"#{1," + str(level) + r"} ", lines[i])), len(lines))
    return "\n".join(lines[index + 1:end]).strip()


def table(text):
    return [tuple(cell.strip() for cell in line.strip("|").split("|"))
            for line in text.splitlines() if line.startswith("| ")
            and not line.startswith("| ---")]


def test_one_entrypoint_and_cleanup_are_present_together():
    entry = document()
    assert "Status: CURRENT NORMATIVE OPERATIONAL NAVIGATION" in entry
    assert "single current Brain-facing normative operational-navigation" in entry
    assert "same complete candidate" in entry
    consolidation = section(entry, "## 10. Section-scoped consolidation and retained authorities")
    assert "one candidate/activation" in consolidation
    assert "an additive document with competing current generic" in consolidation
    for name in REDIRECTS:
        leaf = document(name)
        targets = re.findall(r"\]\(([^)]+)\)", leaf)
        assert any(target.split("#")[0] == ENTRY for target in targets), name
        assert "REPLACE_WITH_POINTER" in leaf, name
        assert "RETAIN_NORMATIVE" in leaf, name
        assert f"]({name})" in consolidation, name
        assert "Status: CURRENT NORMATIVE OPERATIONAL NAVIGATION" not in leaf


def test_duplicate_generic_routes_are_removed_or_locally_historical():
    project = document("CHATGPT_PROJECT_CONTRACT.md")
    identity = section(project, "## 1. Project Identity")
    assert "REPLACE_WITH_POINTER" in identity and "```text" not in identity
    sync = section(project, "## 13. Brain Sync Protocol")
    assert ENTRY in sync and "REPLACE_WITH_POINTER" in sync
    assert "**AUTHOR_TASK before PRIMARY**" not in sync
    local = document("AIOS-LOCAL-REGULAR-CHAT-WAKE-v1.md")
    assert "Target shape:" not in local
    assert "On receipt, the regular Chat Brain must" not in local
    for name, heading in (
        ("AIOS-H4B-REGULAR-CHAT-BRAIN-RESUME-CONFORMANCE-v1.md", "## Existing contract and authority"),
        ("AIOS-H4B-REGULAR-CHAT-BRAIN-RESUME-CONFORMANCE-v1.md", "## Minimum fresh context and invalidation"),
        ("AIOS-BRAIN-RUNTIME-SEMANTIC-HANDOFF-HARDENING-v1.md", "### 8.2 H4B — Regular Chat Brain resume"),
    ):
        assert "REPLACE_WITH_POINTER" in section(document(name), heading)
    audit = document("AIOS-GITHUB-CHATGPT-BRAIN-WAKE-AUTOMATION-AUDIT-v1.md")
    for heading in ("## 1. Human objective", "## 4. Proposed minimal wake carrier",
                    "## 8. Brain behavior after wake", "## 9. Loop examples",
                    "## 12. Conformance gates before roadmap resumes"):
        body = section(audit, heading)
        assert body.startswith("HISTORICAL_ONLY:"), heading
        assert ENTRY in body
    disposition = section(audit, "## 20. Production disposition — Work retired from AIOS wake")
    assert disposition.startswith("RETAIN_NORMATIVE:")
    assert "Re-enabling Work for production wake requires a new" in disposition


def test_navigation_keeps_existing_authorities_and_freshness_precedence():
    body = section(document(), "## 1. Authority and minimum fresh context")
    for contract in (
        "Fresh canonical Git/lineage outranks navigation",
        "Human owns intent, priority, risk and delegation",
        "Brain owns WHAT/WHY", "Executor owns HOW", "Runtime",
        "Reviewer owns semantic verdict", "Publisher owns exact eligible publication",
        "H4 planning/conformance and immutable historical observations",
        "existing Unified State", "existing Flow Card result", "fails closed",
        "no second semantic flow resolver",
    ):
        assert contract in body
    assert "No repository-wide rediscovery" in body


def test_seven_card_registry_retains_closed_shape():
    cards = load_flow_cards(ROOT / ".ai/flow-cards.yaml")
    assert set(cards) == {"ARCHITECTURE", "TASK_AUTHORING", "SEMANTIC_REVIEW",
                          "REMEDIATION_AUTHORING", "REPAIR_AUTHORING", "DIAGNOSTIC", "RESEARCH"}
    fields = {"id", "entry_conditions", "required_context", "optional_context",
              "forbidden_context", "authority_owner", "decision_family_ref",
              "handoff_target", "expected_return_shape", "invalidation_rules"}
    assert all(set(card) == fields for card in cards.values())
    assert cards["SEMANTIC_REVIEW"]["authority_owner"] == "REVIEWER"
    assert all(cards[flow]["authority_owner"] == "BRAIN"
               for flow in cards if flow != "SEMANTIC_REVIEW")


@pytest.mark.parametrize("mutation", ["SELF_HOST", "operational_flow_ref"])
def test_navigation_cannot_extend_flow_cards(tmp_path, mutation):
    registry = {"format": "AIOS_FLOW_CARDS", "version": 1,
                "cards": deepcopy(list(load_flow_cards().values()))}
    if mutation == "SELF_HOST":
        registry["cards"].append({**registry["cards"][0], "id": "SELF_HOST"})
    else:
        registry["cards"][0]["operational_flow_ref"] = ENTRY
    path = tmp_path / "flow-cards.yaml"
    path.write_text(yaml.safe_dump(registry), encoding="utf-8")
    with pytest.raises(BrainContextError):
        load_flow_cards(path)


def test_impact_map_covers_all_six_boundaries_with_real_leaves():
    body = section(document(), "## 9. Bounded impact map before scope closure")
    rows = {row[0]: row for row in table(body)[1:]}
    assert set(rows) == {"AUTHORING", "PRIMARY", "REPAIR", "REMEDIATION", "PUBLICATION", "RETURN"}
    for boundary, row in rows.items():
        assert len(row) == 3 and row[2].endswith("?"), boundary
        targets = re.findall(r"\]\(([^)]+)\)", row[1])
        assert targets, boundary
        assert all((DOCS / target).resolve().is_file() for target in targets), boundary
    assert "not an execution" in body and "scope authorizer" in body
    assert "Runtime owns actual" in body


def test_task314_task315_positive_scenario_includes_durable_outer_repair():
    body = section(document(), "### Mandatory positive regression: TASK-314 → TASK-315")
    for edge in ("correction_preflight", "operator.run_repair_wakeup", "repair_dispatch",
                 "github_issue_repair_wakeup", "brain-repair-wakeup", "self-host-repair-wakeup"):
        assert f"`{edge}`" in body
    for fact in ("FINALIZE_CANDIDATE", "executor_required=false", "executor_required=true",
                 "malformed present state fails closed", "preflight/Unified State agreement",
                 "durable record version/replay", "nullable carrier/workflow selectors",
                 "Issue route or direct route"):
        assert fact in body
    repair_row = next(row for row in table(section(document(), "## 9. Bounded impact map before scope closure"))
                      if row[0] == "REPAIR")
    for path in ("src/aios_renew/correction_preflight.py", "src/aios_renew/operator.py",
                 "src/aios_renew/repair_dispatch.py", "src/aios_renew/github_issue_repair_wakeup.py",
                 ".github/workflows/aios-brain-repair-wakeup.yml",
                 ".github/workflows/aios-self-hosted-repair-wakeup.yml"):
        # operator is a shared PRIMARY leaf; REPAIR names its exact function.
        assert f"../{path}" in repair_row[1] or (
            path.endswith("operator.py") and "operator.run_repair_wakeup" in repair_row[1]
            and f"../{path}" in document())
    operator = (ROOT / "src/aios_renew/operator.py").read_text(encoding="utf-8")
    assert "def run_repair_wakeup(" in operator
    assert "executor_required = preflight.executor_required" in operator
    assert "observed_executor_required is not executor_required" in operator
    ingress = (ROOT / ".github/workflows/aios-brain-ingress.yml").read_text(encoding="utf-8")
    assert "workflow_id: 'aios-self-hosted-repair-wakeup.yml'" in ingress
    carrier = (ROOT / ".github/workflows/aios-brain-repair-wakeup.yml").read_text(encoding="utf-8")
    assert "uses: ./.github/workflows/aios-self-hosted-repair-wakeup.yml" in carrier


def test_failure_taxonomy_and_signal_outcomes_remain_separate():
    body = section(document(), "### Failure and signal boundaries")
    rows = {row[0]: row[1] for row in table(body)[1:]}
    pre_aios = rows["Pre-AIOS carrier, dispatch, runner/setup or admission failure"]
    assert "run_created=false" in pre_aios
    assert "no new canonical RUN/FAILURE/REPAIR lineage" in pre_aios
    signals = rows["CARRIER_ADMITTED / dispatch accepted / RUNNER_STARTED"]
    for outcome in ("RUN creation", "Runtime verification", "Reviewer PASS", "publication", "wake success"):
        assert outcome in signals
    assert "only this failure family can ground REPAIR" in rows["Admitted RUN FAILURE"]
    assert "not semantic REVIEW PASS or publication" in rows["RESULT / Runtime verification"]
    assert "not publication or delivery proof" in rows["REVIEW PASS / CHANGES_REQUIRED / BLOCKED"]
    assert "not wake arrival, semantic resume, planning closure" in rows["Publication report + canonical main inclusion"]
    assert "not RUN, verification, review, publication" in rows["Wake receipt / exact user-turn proof / ACK"]
    repair = section(document(), "## 6. REPAIR and REMEDIATION")
    assert "before a continuation RUN remains" in repair and "inventing a new continuation FAILURE" in repair


@pytest.mark.parametrize("name,headings,observations", [
    ("AIOS-H4A3-ACK-ONLY-EXACT-CHAT-CONFORMANCE-v1.md",
     ["## 4. Positive observation procedure", "## 6. ACK-only semantics and distinct completion owners"],
     ["1f3d0dac34c7cff6d768d580f31513df08ebb253", "354"]),
    ("AIOS-H4A4-DURABLE-WAKE-RECOVERY-CONFORMANCE-v1.md",
     ["## Bounded live proof matrix", "## Assessment record"], ["AMBIGUOUS", "RESOLVED_NOOP"]),
    ("AIOS-H4A5-COMPLETE-ATTENTION-COVERAGE-CONFORMANCE-v1.md",
     ["## Family/source matrix", "## Closure disposition — 2026-10-03"],
     ["37083846294", "11260375137", "INCOMPLETE"]),
    ("AIOS-H4B-REGULAR-CHAT-BRAIN-RESUME-CONFORMANCE-v1.md",
     ["## Bounded live procedure for Human/Brain observation", "## H4A5 residuals, reopen triggers and closure boundaries"],
     ["RUN-285-001", "UNPROVED", "TEMPORARY_WAKE_FIRST_CUTOVER_V2"]),
    ("AIOS-H4C0-ORIGIN-CAPTURE-CONFORMANCE-v1.md",
     ["## 9. Required post-publication two-regular-chat Human/Brain procedure", "## 14. Bounded authoring proof addition (TASK-309)"],
     ["1-of-2 observation", "origin-authoring-v1:"]),
    ("AIOS-H4-ORIGIN-AFFINE-UNATTENDED-WAKE-v1.md",
     ["### 7.3 Required production live matrix", "## 8. Final H4B semantic-resume proof", "## 9. H5 additional mandatory conformance"],
     ["H5 conditions 1–24 remain", "SECOND_SEPARATELY_AUTHORIZED_DEPLOYED_LIVE_LANE_UNAVAILABLE"]),
])
def test_h4_independent_requirements_and_historical_claims_are_preserved(name, headings, observations):
    text = document(name)
    classification = section(text, "## Navigation classification")
    assert "RETAIN_NORMATIVE" in classification and "HISTORICAL_ONLY" in classification
    assert all(heading in text.splitlines() for heading in headings)
    assert all(observation in text for observation in observations)
    assert not re.search(r"(?im)^Status:.*HISTORICAL_ONLY", text)


@pytest.mark.parametrize("name,start,end,digest", [
    ("AIOS-GITHUB-CHATGPT-BRAIN-WAKE-AUTOMATION-AUDIT-v1.md",
     "### 19.1 Live duplicate/reliability pair result", "\n## 20.",
     "f93250aac5652e0d092b7fcf66bf9d3486fbe0a1daa3564edf45f03e4ccd16db"),
    ("AIOS-H4A5-COMPLETE-ATTENTION-COVERAGE-CONFORMANCE-v1.md",
     "The Human-authorized publication replay of RUN-280-002", "\n\nScheduled local-wake runs",
     "45e7679e7bbf0536a4a5932abddfed6378fa633046d4951ddfdb85d02142b151"),
    ("AIOS-H4C0-ORIGIN-CAPTURE-CONFORMANCE-v1.md",
     "A later read-only probe over the already-consumed ambiguous routes", "\n\nThese are privacy-safe",
     "148a1e3d49cbe7a53ad34dc7b5c01ac43680800d95fd4d5900584f42afa06830"),
])
def test_bounded_historical_observations_keep_their_exact_text(name, start, end, digest):
    # These bounded observation snapshots predate TASK-318. Navigation cleanup
    # must not rewrite them, erase their limits or promote them to current proof.
    text = document(name)
    begin = text.index(start)
    finish = text.index(end, begin)
    observed = text[begin:finish].strip().encode("utf-8")
    assert hashlib.sha256(observed).hexdigest() == digest


def test_publication_turn_is_deferred_without_task308_activation():
    publish = section(document(), "## 7. PUBLISH")
    assert "TASK-308 remains unpublished/blocked" in publish
    assert "does not activate TASK-308" in publish
    assert "currently\npublished Project Contract and canonical lineage" in publish
    assert "No new general stop/continue rule is inferred" in publish
    assert "expected-main/CAS" in publish and "no force" in publish
    assert "TASK-110" in section(document("CHATGPT_PROJECT_CONTRACT.md"), "## 12. Publication")
    assert "observe publication outcome before manual fallback" in document("CHATGPT_PROJECT_CONTRACT.md")


def test_entrypoint_and_redirect_local_links_resolve():
    for name in (ENTRY, *REDIRECTS):
        text = document(name)
        targets = re.findall(r"\]\(([^)]+)\)", text)
        for target in targets:
            # Existing leaf links are outside this navigation change; all entrypoint
            # links and all newly added redirect links must resolve together.
            if name != ENTRY and target.split("#")[0] != ENTRY:
                continue
            path, _, anchor = target.partition("#")
            resolved = (DOCS / path).resolve()
            assert resolved.is_file(), (name, target)
            if anchor:
                headings = re.findall(r"(?m)^#{1,6} (.+)$", resolved.read_text(encoding="utf-8"))
                slugs = {re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
                         for heading in headings}
                assert anchor in slugs, (name, target)
