from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    text = (ROOT / ".github/workflows/aios-local-chat-wake.yml").read_text(encoding="utf-8")
    return yaml.load(text, Loader=yaml.BaseLoader), text


def test_only_bounded_manual_dispatch_on_human_enabled_main():
    parsed, text = workflow()
    assert set(parsed["on"]) == {"workflow_dispatch"}
    inputs = parsed["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"event_id", "repository"}
    assert inputs["repository"]["options"] == ["trung-via/AIOS-renew"]
    assert parsed["permissions"] == {"contents": "read"}
    assert parsed["concurrency"]["cancel-in-progress"] == "false"
    job = parsed["jobs"]["deliver"]
    assert "vars.AIOS_LOCAL_CHAT_WAKE_ENABLED == 'true'" in job["if"]
    assert "github.ref == 'refs/heads/main'" in job["if"]
    assert "github.repository == 'trung-via/AIOS-renew'" in job["if"]
    assert job["runs-on"] == ["self-hosted", "windows", "x64", "aios-renew"]
    assert int(job["timeout-minutes"]) <= 5
    for forbidden in ("workflow_run", "schedule:", "issues:", "aios run", "aios repair", "aios remediate", "upload-artifact", "playwright install"):
        assert forbidden not in text


def test_machine_binding_is_not_a_workflow_input_or_github_configuration():
    parsed, text = workflow()
    steps = parsed["jobs"]["deliver"]["steps"]
    assert steps[0]["with"] == {"ref": "${{ github.sha }}", "persist-credentials": "false"}
    delivery = steps[-1]
    assert delivery["env"] == {
        "AIOS_WAKE_EVENT_ID": "${{ inputs.event_id }}",
        "AIOS_WAKE_REPOSITORY": "${{ inputs.repository }}",
    }
    assert "python -m aios_renew.local_chat_wake" in delivery["run"]
    assert "exit $LASTEXITCODE" in delivery["run"]
    assert ".[local-chat-wake]" in steps[-2]["run"]
    assert "secrets." not in text
    assert "chatgpt.com/c/" not in text
    import tomllib
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert not any("playwright" in item for item in project["project"]["dependencies"])
    assert project["project"]["optional-dependencies"]["local-chat-wake"] == ["playwright>=1.51,<2"]
