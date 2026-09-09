from pathlib import Path


def test_approved_remediation_workflow_is_bounded_manual_self_hosted_surface() -> None:
    root = Path(__file__).parents[1]
    source = (root / ".github/workflows/aios-approved-remediation-wakeup.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in source
    assert "pull_request:" not in source and "push:" not in source
    assert "actions/checkout" not in source
    assert "runs-on: [self-hosted, windows, x64, aios-renew]" in source
    assert "contents: read" in source
    assert source.count("aios approved-remediation-wakeup ") == 1
    assert "${{ inputs.correction_dispatch_id }}" in source
    assert "${{ inputs.source_run_id }}" in source
    assert "${{ inputs.finding_id }}" in source
    assert "${{ inputs.executor }}" in source
