from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _is_ignored(path: str) -> bool:
    completed = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", "--", path],
        cwd=ROOT,
        check=False,
    )
    assert completed.returncode in {0, 1}
    return completed.returncode == 0


def test_release_ignore_matrix_keeps_only_the_validated_snapshot_trackable() -> None:
    ignored = {
        ".env.local",
        ".env.production",
        "update.log",
        "papers.db.bak",
        "papers.db.backup",
        "papers.db.2026-07-11",
        "data/papers.db",
        "data/papers.db-wal",
        "data/papers.db-shm",
        "data/papers.db-journal",
        "data/papers.db.bak",
        "data/papers.db.backup",
        "data/papers.db.2026-07-11",
        "docs/data/papers.db-wal",
        "docs/data/papers.db-shm",
        "docs/data/papers.db.bak",
        "docs/data/papers.db.backup",
        "docs/data/archive.db",
        "docs/data/staged.tmp",
    }
    for path in sorted(ignored):
        assert _is_ignored(path), path

    assert not _is_ignored(".env.example")
    assert not _is_ignored("docs/data/papers.db")


def test_env_template_exists_and_is_tracked() -> None:
    assert (ROOT / ".env.example").is_file()
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env.example"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_beginner_commands_and_update_json_paths_are_accurate() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    update_script = (ROOT / "scripts" / "update.ps1").read_text(encoding="utf-8")

    assert "py -3 -m venv .venv" in readme
    assert "py -0p" in readme
    assert "Python 3.11" in readme
    assert "py -3.11 -m venv" not in readme
    assert "py -3 -m venv .venv" in update_script
    for field in (
        "result.status",
        "result.inserted",
        "result.updated",
        "result.skipped",
        "result.failed",
        "result.successful_feeds",
        "result.failed_feeds",
        "publish_allowed",
    ):
        assert f"`{field}`" in readme


def test_readme_documents_daily_cloud_update_and_recovery() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    workflow = ROOT / ".github" / "workflows" / "daily-rss-update.yml"

    assert workflow.is_file()
    for phrase in (
        "每天北京时间 08:00",
        "不需要打开 Codex",
        "不需要保持本地电脑开机",
        "Daily RSS Update",
        "Run workflow",
        "UNPAYWALL_EMAIL",
        "连续 60 天",
        "docs/data/papers.db",
    ):
        assert phrase in readme


def test_readme_accurately_describes_automation_timing_and_current_deployment() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for phrase in (
        "UTC 00:00",
        "可能延迟",
        "`Commit database update`",
        "最近一次成功更新（自动或手动）",
        "首次自行部署或重新配置 GitHub Pages",
    ):
        assert phrase in readme

    assert "尚未创建或推送 GitHub 仓库" not in readme
    assert "本 README 不填写尚不存在的 URL" not in readme
