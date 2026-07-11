from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "guide-sync-check.yml"
CHECKOUT_SHA = "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0"
SETUP_PYTHON_SHA = "ece7cb06caefa5fff74198d8649806c4678c61a1"


def _load() -> tuple[str, dict[str, object]]:
    text = WORKFLOW.read_text(encoding="utf-8")
    parsed = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict)
    return text, parsed


def test_guide_sync_workflow_is_read_only_on_push_and_pull_request() -> None:
    text, parsed = _load()

    assert parsed["on"] == {"push": "", "pull_request": ""}
    assert parsed["permissions"] == {"contents": "read"}
    jobs = parsed["jobs"]
    assert isinstance(jobs, dict)
    assert list(jobs) == ["guide-sync"]
    job = jobs["guide-sync"]
    assert isinstance(job, dict)
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == "10"

    lowered = text.lower()
    for forbidden in (
        "paper_radar update",
        "papers.db",
        "git add",
        "git commit",
        "git push",
        "contents: write",
        "pages: write",
        "github.token",
    ):
        assert forbidden not in lowered


def test_guide_sync_workflow_pins_actions_and_runs_exact_check_after_install() -> None:
    text, parsed = _load()
    jobs = parsed["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["guide-sync"]
    assert isinstance(job, dict)
    raw_steps = job["steps"]
    assert isinstance(raw_steps, list)
    assert all(isinstance(step, dict) for step in raw_steps)
    steps = [{str(key): str(value) for key, value in step.items()} for step in raw_steps]

    assert [step["name"] for step in steps] == [
        "Check out repository",
        "Set up Python",
        "Install application",
        "Check Guide configuration sync",
    ]
    assert [step["uses"] for step in steps if "uses" in step] == [
        f"actions/checkout@{CHECKOUT_SHA}",
        f"actions/setup-python@{SETUP_PYTHON_SHA}",
    ]
    by_name = {step["name"]: step for step in steps}
    assert "python -m pip install ." in by_name["Install application"]["run"]
    assert by_name["Check Guide configuration sync"]["run"].splitlines() == [
        "set -euo pipefail",
        "python scripts/render_site_guide.py --check",
    ]
    assert "python-version: '3.12'" in text
    assert "@main" not in text
    assert not re.search(r"uses:\s+[^\s]+@(v?\d+(?:\.\d+)*)\s*$", text, re.MULTILINE)
