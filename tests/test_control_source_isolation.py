from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts" / "aios_control_entry.py"
WORKFLOWS = (
    "aios-self-hosted-wakeup.yml",
    "aios-approved-remediation-intent.yml",
    "aios-approved-remediation-wakeup.yml",
    "aios-self-hosted-repair-wakeup.yml",
    "aios-remote-approval.yml",
    "aios-remote-status.yml",
)


def test_every_self_host_control_surface_is_exact_sha_bound() -> None:
    for name in WORKFLOWS:
        path = ROOT / ".github" / "workflows" / name
        text = path.read_text(encoding="utf-8")
        workflow = yaml.load(text, Loader=yaml.BaseLoader)
        job = next(iter(workflow["jobs"].values()))
        env = job["env"]
        prepare = next(
            step
            for step in job["steps"]
            if step.get("name") == "Prepare exact transient control source"
        )

        assert env["AIOS_CONTROL_SHA"] == "${{ github.sha }}"
        assert env["AIOS_CONTROL_REF"] == "${{ github.ref }}"
        assert "AIOS_CONTROL_ROOT" not in env
        assert prepare["env"]["AIOS_CONTROL_ROOT"].startswith(
            "${{ runner.temp }}/aios-control-"
        )
        assert 'AIOS_CONTROL_ROOT=$env:AIOS_CONTROL_ROOT' in prepare["run"]
        assert "refs/heads/main" in text
        assert "fetch --quiet --no-tags --depth=1" in text
        assert "checkout --quiet --detach FETCH_HEAD" in text
        assert "rev-parse HEAD" in text
        assert "status --porcelain" in text
        assert "scripts/aios_control_entry.py" in text
        assert "actions/checkout" not in text
        assert "PYTHONPATH" not in text
        assert "pip install" not in text
        assert "Get-Command aios" not in text
        assert "git -C $env:AIOS_REPO_ROOT fetch" not in text
        assert "git -C $env:AIOS_REPO_ROOT checkout" not in text
        assert "git -C $env:AIOS_REPO_ROOT reset" not in text


def test_poisoned_persistent_candidate_is_not_imported_or_mutated(tmp_path: Path) -> None:
    control = tmp_path / "control"
    candidate = tmp_path / "candidate"
    trusted_package = control / "src" / "aios_renew"
    poisoned_package = candidate / "src" / "aios_renew"
    trusted_package.mkdir(parents=True)
    poisoned_package.mkdir(parents=True)
    (trusted_package / "__init__.py").write_text("", encoding="utf-8")
    (poisoned_package / "__init__.py").write_text("", encoding="utf-8")

    trusted_marker = tmp_path / "trusted.txt"
    poison_marker = tmp_path / "poison.txt"
    (trusted_package / "operator.py").write_text(
        "from pathlib import Path\n"
        "def main(argv):\n"
        f"    Path({str(trusted_marker)!r}).write_text('|'.join(argv), encoding='utf-8')\n"
        "    return 0\n",
        encoding="utf-8",
    )
    (poisoned_package / "operator.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(poison_marker)!r}).write_text('poison', encoding='utf-8')\n"
        "def main(argv): return 99\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "init", "--quiet", str(candidate)], check=True)
    subprocess.run(["git", "-C", str(candidate), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(candidate), "config", "user.name", "AIOS Test"], check=True)
    subprocess.run(["git", "-C", str(candidate), "add", "."], check=True)
    subprocess.run(["git", "-C", str(candidate), "commit", "--quiet", "-m", "candidate"], check=True)
    head_before = subprocess.check_output(
        ["git", "-C", str(candidate), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = candidate / "candidate-state.txt"
    dirty.write_text("preserve me", encoding="utf-8")
    status_before = subprocess.check_output(
        ["git", "-C", str(candidate), "status", "--porcelain"], text=True
    )

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(candidate / "src")
    completed = subprocess.run(
        [sys.executable, str(ENTRY), str(control), "remote-status", "dispatch-158"],
        cwd=candidate,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert trusted_marker.read_text(encoding="utf-8") == "remote-status|dispatch-158"
    assert not poison_marker.exists()
    assert subprocess.check_output(
        ["git", "-C", str(candidate), "rev-parse", "HEAD"], text=True
    ).strip() == head_before
    assert subprocess.check_output(
        ["git", "-C", str(candidate), "status", "--porcelain"], text=True
    ) == status_before
    assert dirty.read_text(encoding="utf-8") == "preserve me"


def test_control_entry_does_not_export_import_override(tmp_path: Path) -> None:
    source = tmp_path / "control"
    package = source / "src" / "aios_renew"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    observed = tmp_path / "environment.txt"
    (package / "operator.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "def main(argv):\n"
        f"    Path({str(observed)!r}).write_text(os.environ.get('PYTHONPATH', '<missing>'), encoding='utf-8')\n"
        "    return 0\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, str(ENTRY), str(source), "remote-status", "dispatch-158"],
        env=environment,
        check=False,
    )

    assert completed.returncode == 0
    assert observed.read_text(encoding="utf-8") == "<missing>"
