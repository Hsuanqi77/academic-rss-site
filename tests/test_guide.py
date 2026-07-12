from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from paper_radar.config import FeedConfig, TopicCatalog, TopicConfig, TopicGroupConfig
from paper_radar.guide import (
    GUIDE_END,
    GUIDE_START,
    GuideRenderError,
    render_file,
    render_guide,
    replace_guide_region,
)


ROOT = Path(__file__).resolve().parents[1]


def _feeds() -> tuple[FeedConfig, ...]:
    return (
        FeedConfig(
            "nature",
            "Nature <Research>",
            "nature",
            "https://example.com/nature.rss?section=a&format=rss",
        ),
        FeedConfig(
            "disabled",
            "Disabled",
            "ieee",
            "https://example.com/off.rss",
            enabled=False,
        ),
        FeedConfig(
            "ieee",
            "IEEE Journal",
            "ieee",
            "https://example.com/ieee.rss",
        ),
    )


def _catalog() -> TopicCatalog:
    return TopicCatalog(
        groups=(
            TopicGroupConfig("acoustic-rf", "声学与射频器件", 1),
            TopicGroupConfig("devices", "电子器件", 2),
        ),
        topics=(
            TopicConfig(
                "baw",
                "BAW & filters",
                ("bulk acoustic wave", "RF <filter>"),
                "acoustic-rf",
            ),
            TopicConfig("transistor", "Transistor", ("FET",), "devices"),
        ),
    )


def _index_with_markers(content: str) -> str:
    return f"before\n{GUIDE_START}\n{content}\n{GUIDE_END}\nafter\n"


def test_render_guide_groups_enabled_feeds_and_topics_with_escaped_text() -> None:
    html = render_guide(_feeds(), _catalog())

    assert "Nature &lt;Research&gt;" in html
    assert "BAW &amp; filters" in html
    assert "RF &lt;filter&gt;" in html
    assert "https://example.com/nature.rss?section=a&amp;format=rss" in html
    assert "Disabled" not in html
    assert 'target="_blank" rel="noopener noreferrer"' in html
    assert html.index("Nature Portfolio") < html.index("IEEE")
    assert html.index("声学与射频器件") < html.index("电子器件")


def test_render_guide_orders_all_supported_publishers() -> None:
    publishers = ("nature", "aps", "aip", "ieee", "wiley", "elsevier", "aaas", "springer")
    feeds = tuple(
        FeedConfig(
            f"feed-{index}",
            f"Journal {index}",
            publisher,
            f"https://example.com/feed-{index}.rss",
        )
        for index, publisher in enumerate(publishers, start=1)
    )
    labels = (
        "Nature Portfolio",
        "American Physical Society",
        "AIP Publishing",
        "IEEE",
        "Wiley",
        "Elsevier",
        "AAAS",
        "Springer Nature",
    )

    html = render_guide(feeds, _catalog())

    positions = tuple(html.index(label) for label in labels)
    assert positions == tuple(sorted(positions))


def test_render_guide_uses_native_disclosures_and_actual_counts() -> None:
    html = render_guide(_feeds(), _catalog())

    assert '<section id="guide"' in html
    assert "2 SOURCES" in html
    assert "2 DIRECTIONS" in html
    assert "2 PRECISE TAGS" in html
    assert html.count("<details") == 4
    assert html.count("<summary") == 4
    assert html.count("<details class=\"guide-group\" open>") == 2
    assert "01 — 声学与射频器件" in html


def test_first_rendered_publisher_is_open_when_earlier_publishers_are_empty() -> None:
    ieee_only = (_feeds()[-1],)

    html = render_guide(ieee_only, _catalog())

    publisher_start = html.index("IEEE")
    publisher_details = html.rfind("<details", 0, publisher_start)
    publisher_tag = html[publisher_details : html.index(">", publisher_details) + 1]
    assert publisher_tag == '<details class="guide-group" open>'


def test_replace_guide_region_is_deterministic_and_preserves_surroundings() -> None:
    source = _index_with_markers("old")
    expected = _index_with_markers('<section id="guide">new</section>')

    rendered = replace_guide_region(source, '<section id="guide">new</section>')

    assert rendered == expected
    assert replace_guide_region(rendered, '<section id="guide">new</section>') == rendered


@pytest.mark.parametrize(
    "source",
    (
        "no markers",
        f"{GUIDE_START}\nmissing end",
        f"missing start\n{GUIDE_END}",
        f"{GUIDE_END}\n{GUIDE_START}",
        f"{GUIDE_START}\na\n{GUIDE_START}\nb\n{GUIDE_END}",
        f"{GUIDE_START}\na\n{GUIDE_END}\nb\n{GUIDE_END}",
    ),
)
def test_replace_guide_region_rejects_invalid_marker_layout(source: str) -> None:
    with pytest.raises(GuideRenderError):
        replace_guide_region(source, "new")


def test_renderer_check_mode_detects_drift_without_writing(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    stale = _index_with_markers("stale")
    index.write_text(stale, encoding="utf-8")

    assert render_file(index, feeds=_feeds(), catalog=_catalog(), check=True) is False
    assert index.read_text(encoding="utf-8") == stale


def test_renderer_write_mode_updates_file_and_then_reports_in_sync(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text(_index_with_markers("stale"), encoding="utf-8")

    assert render_file(index, feeds=_feeds(), catalog=_catalog(), check=False) is False
    updated = index.read_text(encoding="utf-8")
    assert "stale" not in updated
    assert "Nature &lt;Research&gt;" in updated
    assert render_file(index, feeds=_feeds(), catalog=_catalog(), check=False) is True
    assert render_file(index, feeds=_feeds(), catalog=_catalog(), check=True) is True


def test_render_file_does_not_write_when_markers_are_invalid(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    source = "<html>no guide markers</html>"
    index.write_text(source, encoding="utf-8")

    with pytest.raises(GuideRenderError):
        render_file(index, feeds=_feeds(), catalog=_catalog(), check=False)

    assert index.read_text(encoding="utf-8") == source


def test_site_guide_check_command_reports_the_tracked_page_is_current() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/render_site_guide.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
