from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "daily-rss-update.yml"
CHECKOUT_SHA = "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0"
SETUP_PYTHON_SHA = "ece7cb06caefa5fff74198d8649806c4678c61a1"


def _load() -> tuple[str, dict[str, object]]:
    text = WORKFLOW.read_text(encoding="utf-8")
    parsed = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict)
    return text, parsed


def _job(parsed: dict[str, object]) -> dict[str, object]:
    jobs = parsed["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["update"]
    assert isinstance(job, dict)
    return job


def _raw_steps(job: dict[str, object]) -> list[dict[str, object]]:
    raw_steps = job["steps"]
    assert isinstance(raw_steps, list)
    assert all(isinstance(step, dict) for step in raw_steps)
    return raw_steps


def _steps(job: dict[str, object]) -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
    for raw_step in _raw_steps(job):
        steps.append({str(key): str(value) for key, value in raw_step.items()})
    return steps


def test_daily_workflow_has_exact_triggers_permissions_and_concurrency() -> None:
    _, parsed = _load()
    triggers = parsed["on"]
    assert isinstance(triggers, dict)
    schedule = triggers["schedule"]
    assert schedule == [{"cron": "0 0 * * *"}]
    assert "workflow_dispatch" in triggers
    assert parsed["permissions"] == {"contents": "write", "pages": "write"}
    assert parsed["concurrency"] == {
        "group": "daily-rss-update",
        "cancel-in-progress": "false",
    }
    job = _job(parsed)
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == "30"


def test_daily_workflow_pins_actions_and_uses_optional_unpaywall_secret() -> None:
    text, parsed = _load()
    job = _job(parsed)
    steps = _steps(job)
    uses = [step["uses"] for step in steps if "uses" in step]
    assert uses == [
        f"actions/checkout@{CHECKOUT_SHA}",
        f"actions/setup-python@{SETUP_PYTHON_SHA}",
    ]
    raw_by_name = {step["name"]: step for step in _raw_steps(job) if "name" in step}
    assert raw_by_name["Check out repository"]["with"] == {
        "ref": "main",
        "fetch-depth": "0",
    }
    assert "python-version: '3.12'" in text
    assert job["env"] == {"UNPAYWALL_EMAIL": "${{ secrets.UNPAYWALL_EMAIL }}"}
    assert "@main" not in text
    assert not re.search(r"uses:\s+[^\s]+@(v?\d+(?:\.\d+)*)\s*$", text, re.MULTILINE)


def test_daily_workflow_restores_then_updates_and_limits_change_scope() -> None:
    text, parsed = _load()
    steps = _steps(_job(parsed))
    runs = "\n".join(step.get("run", "") for step in steps)
    restore_at = runs.index("cp docs/data/papers.db data/papers.db")
    update_at = runs.index("python -m paper_radar update")
    scope_at = runs.index("git diff --name-only")
    assert restore_at < update_at < scope_at
    assert "mkdir -p data" in runs
    assert "python -m pip install ." in runs
    assert 'tee "$RUNNER_TEMP/update-result.json"' in runs
    assert "git diff --name-only HEAD --" in runs
    assert "grep -vFx 'docs/data/papers.db'" in runs
    assert "git add -- docs/data/papers.db" in runs
    assert "git diff --cached --quiet" in runs
    assert 'git commit -m "chore(data): daily RSS update"' in runs
    assert "git pull --rebase origin main" in runs
    assert "git push origin HEAD:main" in runs
    assert "--force" not in runs
    assert "git add ." not in runs

    by_name = {step["name"]: step for step in steps if "name" in step}
    update = by_name["Update RSS database"]
    assert "set -euo pipefail" in update["run"]


def test_daily_workflow_checks_guide_sync_before_any_database_update() -> None:
    _, parsed = _load()
    steps = _steps(_job(parsed))
    names = [step["name"] for step in steps]

    install_at = names.index("Install application")
    guide_check_at = names.index("Check Guide configuration sync")
    restore_at = names.index("Restore incremental working database")
    update_at = names.index("Update RSS database")
    commit_at = names.index("Commit database update")
    assert install_at < guide_check_at < restore_at < update_at < commit_at

    guide_check = steps[guide_check_at]
    assert guide_check["run"].splitlines() == [
        "set -euo pipefail",
        "python scripts/render_site_guide.py --check",
    ]


def test_daily_workflow_skips_empty_commit_and_always_requests_pages_build() -> None:
    _, parsed = _load()
    job = _job(parsed)
    steps = _steps(job)
    by_name = {step["name"]: step for step in steps if "name" in step}
    detect = by_name["Detect database change"]
    summary = by_name["Write update summary"]
    commit = by_name["Commit database update"]
    pages = by_name["Request GitHub Pages build"]
    assert detect["id"] == "changes"
    assert "database_changed=false" in detect["run"]
    assert "database_changed=true" in detect["run"]
    assert summary["if"] == "${{ always() }}"
    assert '[[ -f "$RUNNER_TEMP/update-result.json" ]]' in summary["run"]
    assert "Update failed before producing a result." in summary["run"]
    assert commit["if"] == "steps.changes.outputs.database_changed == 'true'"
    assert "if" not in pages
    assert "gh api --method POST" in pages["run"]
    assert "repos/${GITHUB_REPOSITORY}/pages/builds" in pages["run"]

    raw_by_name = {step["name"]: step for step in _raw_steps(job) if "name" in step}
    assert raw_by_name["Request GitHub Pages build"]["env"] == {
        "GH_TOKEN": "${{ github.token }}"
    }


def test_head_diff_enumerates_staged_unexpected_tracked_file(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test Bot"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test-bot@users.noreply.github.com"],
        cwd=tmp_path,
        check=True,
    )
    database = tmp_path / "docs" / "data" / "papers.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"initial database")
    unexpected = tmp_path / "README.md"
    unexpected.write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "docs/data/papers.db", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    unexpected.write_text("staged unexpected change\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "README.md"], cwd=tmp_path, check=True)
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    changed = result.stdout.splitlines()
    rejected = [path for path in changed if path != "docs/data/papers.db"]
    assert changed == ["README.md"]
    assert rejected == ["README.md"]


def test_daily_workflow_has_no_personal_token_or_hardcoded_email() -> None:
    text, _ = _load()
    lowered = text.lower()
    assert "personal_access_token" not in lowered
    assert "github_pat" not in lowered
    assert "@example." not in lowered
    assert "@gmail." not in lowered
    assert "@qq." not in lowered
    assert "secrets.unpaywall_email" in lowered
    assert "github.token" in lowered
