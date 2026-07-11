from __future__ import annotations

from collections.abc import Sequence
from html import escape
from pathlib import Path

from paper_radar.config import FeedConfig, TopicCatalog


GUIDE_START = "<!-- GUIDE:START -->"
GUIDE_END = "<!-- GUIDE:END -->"
PUBLISHER_LABELS = {
    "nature": "Nature Portfolio",
    "ieee": "IEEE",
    "aip": "AIP Publishing",
    "wiley": "Wiley",
}


class GuideRenderError(ValueError):
    """Raised when the generated Guide cannot safely replace its target region."""


def render_guide(feeds: Sequence[FeedConfig], catalog: TopicCatalog) -> str:
    """Render the deterministic, configuration-backed Guide section."""
    enabled_feeds = tuple(feed for feed in feeds if feed.enabled)
    publisher_sections = _render_publishers(enabled_feeds)
    taxonomy_sections = _render_taxonomy(catalog)
    feed_count = len(enabled_feeds)
    group_count = len(catalog.groups)
    topic_count = len(catalog.topics)

    return "\n".join(
        (
            '<section id="guide" class="info-section guide-section" '
            'aria-labelledby="guide-title" data-drawer-background>',
            '  <p class="eyebrow">GUIDE / 03</p><h2 id="guide-title">说明</h2>',
            "  <p>GitHub 云端每天北京时间 08:00 计划触发更新，繁忙时可能延迟；"
            "不需要打开 Codex 或保持本地电脑开机。本站聚合公开 RSS 元数据，"
            "自动标签只用于初步筛选，不能替代人工分类。</p>",
            '  <div class="guide-stats" aria-label="运行与配置摘要">',
            '    <span class="guide-stat"><strong>08:00</strong> 北京时间计划更新</span>',
            f'    <span class="guide-stat"><strong>{feed_count}</strong> SOURCES</span>',
            f'    <span class="guide-stat"><strong>{group_count}</strong> DIRECTIONS</span>',
            '    <span class="guide-stat"><strong>GitHub Actions</strong> 云端运行</span>',
            "  </div>",
            '  <section class="guide-subsection" aria-labelledby="guide-feeds-title">',
            '    <div class="guide-subheading"><h3 id="guide-feeds-title">每天检查的 RSS 列表</h3>',
            f'      <span>{feed_count} SOURCES</span></div>',
            '    <div class="guide-grid guide-feed-groups">',
            publisher_sections,
            "    </div>",
            "  </section>",
            '  <section class="guide-subsection" aria-labelledby="guide-topics-title">',
            '    <div class="guide-subheading"><h3 id="guide-topics-title">标签与关键词</h3>',
            f'      <span>{group_count} DIRECTIONS / {topic_count} PRECISE TAGS</span></div>',
            '    <div class="guide-grid guide-topic-groups">',
            taxonomy_sections,
            "    </div>",
            "  </section>",
            '  <p class="guide-boundary">本站只聚合公开 RSS 元数据，文章版权归原出版商所有；'
            "OA 状态和自动标签可能不完整或误判。</p>",
            "</section>",
        )
    )


def replace_guide_region(source: str, rendered_guide: str) -> str:
    """Replace exactly one valid Guide marker region while preserving surroundings."""
    if source.count(GUIDE_START) != 1 or source.count(GUIDE_END) != 1:
        raise GuideRenderError("index must contain exactly one Guide marker pair")

    start = source.index(GUIDE_START)
    end = source.index(GUIDE_END)
    if start >= end:
        raise GuideRenderError("Guide start marker must precede the end marker")

    prefix = source[: start + len(GUIDE_START)]
    suffix = source[end:]
    return f"{prefix}\n{rendered_guide}\n{suffix}"


def render_file(
    index_path: Path,
    *,
    feeds: Sequence[FeedConfig],
    catalog: TopicCatalog,
    check: bool,
) -> bool:
    """Check or update an index file; return whether it was already in sync."""
    source = index_path.read_text(encoding="utf-8")
    rendered = replace_guide_region(source, render_guide(feeds, catalog))
    in_sync = rendered == source
    if not check and not in_sync:
        index_path.write_text(rendered, encoding="utf-8")
    return in_sync


def _render_publishers(feeds: Sequence[FeedConfig]) -> str:
    sections: list[str] = []
    for publisher, label in PUBLISHER_LABELS.items():
        publisher_feeds = tuple(feed for feed in feeds if feed.publisher == publisher)
        if not publisher_feeds:
            continue
        open_attribute = " open" if not sections else ""
        lines = [
            f'      <details class="guide-group"{open_attribute}>',
            "        <summary><span>"
            f"{escape(label, quote=True)}</span><span>{len(publisher_feeds)} RSS</span></summary>",
            '        <div class="guide-group-content"><ul class="guide-feed-list">',
        ]
        for feed in publisher_feeds:
            name = escape(feed.name, quote=True)
            url = escape(feed.feed_url, quote=True)
            lines.extend(
                (
                    '          <li class="guide-feed">',
                    f'            <span class="guide-feed-name">{name}</span>',
                    f'            <a class="guide-feed-url" href="{url}" target="_blank" '
                    f'rel="noopener noreferrer">{url}</a>',
                    "          </li>",
                )
            )
        lines.extend(("        </ul></div>", "      </details>"))
        sections.append("\n".join(lines))
    return "\n".join(sections)


def _render_taxonomy(catalog: TopicCatalog) -> str:
    sections: list[str] = []
    for group_index, group in enumerate(catalog.groups):
        topics = tuple(topic for topic in catalog.topics if topic.group == group.id)
        open_attribute = " open" if group_index == 0 else ""
        group_number = f"{group_index + 1:02d}"
        lines = [
            f'      <details class="guide-group"{open_attribute}>',
            "        <summary><span>"
            f"{group_number} — {escape(group.label, quote=True)}</span>"
            f"<span>{len(topics)} TAGS</span></summary>",
            '        <div class="guide-group-content guide-tag-list">',
        ]
        for topic in topics:
            label = escape(topic.label, quote=True)
            keywords = " · ".join(escape(keyword, quote=True) for keyword in topic.keywords)
            lines.extend(
                (
                    '          <div class="guide-tag">',
                    f'            <div class="guide-tag-name">{label}</div>',
                    f'            <div class="guide-keywords">{keywords}</div>',
                    "          </div>",
                )
            )
        lines.extend(("        </div>", "      </details>"))
        sections.append("\n".join(lines))
    return "\n".join(sections)
