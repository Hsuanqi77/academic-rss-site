from __future__ import annotations

from html import escape
import subprocess
from pathlib import Path

from paper_radar.config import load_feeds, load_topic_catalog
from paper_radar.guide import render_file


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


def test_readme_documents_guide_generation_and_full_reclassification() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for phrase in (
        "8 个一级方向",
        "56 个精细标签",
        "一级方向只用于组织“说明”",
        "精细标签是论文实际的自动分类和筛选单位",
        "`topics.yml` 是标签与关键词的唯一来源",
        "全部已存论文",
        "304 未修改",
        "没有新论文",
        "同一个事务",
        "完整单词边界",
        "连字符、Unicode 破折号和连续空白",
        "上下文门槛",
        "自动标签仅供初步筛选",
        "可能漏标或误标",
        "不是编辑或人工标注结论",
        "`feeds.yml` 是 RSS 来源的唯一来源",
        "不要手工编辑 `<!-- GUIDE:START -->` 和 `<!-- GUIDE:END -->` 之间的内容",
        "CI 和发布检查会拦截说明区与配置不同步",
        "Guide Sync Check 会在 push 和 pull request 时执行",
        "Daily RSS Update 也会在更新数据库前重复该检查",
    ):
        assert phrase in readme

    command_lines = {line.strip() for line in readme.splitlines()}
    for command in (
        ".\\.venv\\Scripts\\python.exe scripts/render_site_guide.py",
        ".\\.venv\\Scripts\\python.exe scripts/render_site_guide.py --check",
    ):
        assert command in command_lines


def test_production_guide_is_generated_from_release_configuration() -> None:
    feeds = load_feeds(ROOT / "feeds.yml")
    catalog = load_topic_catalog(ROOT / "topics.yml")
    index_path = ROOT / "docs" / "index.html"
    index = index_path.read_text(encoding="utf-8")

    assert render_file(index_path, feeds=feeds, catalog=catalog, check=True)
    assert sum(feed.enabled for feed in feeds) == 20
    assert len(catalog.groups) == 8
    assert len(catalog.topics) == 56
    assert "每天检查的 RSS 列表" in index
    assert "标签与关键词" in index
    assert "20 SOURCES" in index
    for group in catalog.groups:
        assert escape(group.label, quote=True) in index
    for topic in catalog.topics:
        assert escape(topic.label, quote=True) in index
    assert 'href="#about"' not in index
