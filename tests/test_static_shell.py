from __future__ import annotations

import hashlib
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SQL_JS_1_10_2_SOURCES = {
    "sql-wasm.js": (
        "https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.2/sql-wasm.js",
        "3358bb12892642698c0804c85cba48de562bc2de324fe58a422f282832c79c01",
    ),
    "sql-wasm.wasm": (
        "https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.2/sql-wasm.wasm",
        "4c1c978826062f7b1bb6cc811503863b01415175d0e6dd9ce8a30a81a02c0afb",
    ),
}


class _ShellParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.landmarks: set[str] = set()
        self.links: list[str] = []
        self.scripts: list[str] = []
        self.lang: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "html":
            self.lang = attributes.get("lang")
        if element_id := attributes.get("id"):
            self.ids.add(element_id)
        if tag in {"header", "nav", "main", "aside", "section", "footer"}:
            self.landmarks.add(tag)
        if tag == "link" and attributes.get("href"):
            self.links.append(attributes["href"])
        if tag == "script" and attributes.get("src"):
            self.scripts.append(attributes["src"])


def test_static_shell_has_required_accessible_structure() -> None:
    html = (DOCS / "index.html").read_text(encoding="utf-8")
    parser = _ShellParser()
    parser.feed(html)

    assert parser.lang == "zh-CN"
    assert {"header", "nav", "main", "aside", "section"} <= parser.landmarks
    assert {
        "main-content",
        "filters",
        "open-filters",
        "close-filters",
        "filter-overlay",
        "status",
        "article-list",
        "pagination",
    } <= parser.ids
    assert "styles.css" in parser.links
    assert "sql-wasm.js" in parser.scripts
    assert "js/app.js" in parser.scripts
    assert all(not url.startswith(("/", "http://", "https://")) for url in parser.links)
    assert all(not url.startswith(("/", "http://", "https://")) for url in parser.scripts)
    assert 'class="skip-link"' in html
    assert 'aria-live="polite"' in html


def test_static_assets_are_local_and_sqljs_is_vendored() -> None:
    for name in (".nojekyll", "styles.css", "js/app.js", "sql-wasm.js", "sql-wasm.wasm"):
        assert (DOCS / name).exists(), name

    javascript = (DOCS / "sql-wasm.js").read_bytes()
    wasm = (DOCS / "sql-wasm.wasm").read_bytes()
    assert len(javascript) > 10_000
    assert b"sql.js" in javascript[:5_000].lower()
    assert len(wasm) > 100_000
    assert wasm.startswith(b"\x00asm")
    for name, (source, expected_sha256) in SQL_JS_1_10_2_SOURCES.items():
        assert "/sql.js/1.10.2/" in source
        assert hashlib.sha256((DOCS / name).read_bytes()).hexdigest() == expected_sha256


def test_shell_supports_drawer_accessibility_and_responsive_motion() -> None:
    app = (DOCS / "js" / "app.js").read_text(encoding="utf-8")
    css = (DOCS / "styles.css").read_text(encoding="utf-8")

    assert "aria-expanded" in app
    assert "Escape" in app
    assert "focus" in app
    assert "drawer-open" in app
    assert "prefers-reduced-motion" in css
    assert "overflow-x" in css
    assert "focus-visible" in css


def test_text_assets_do_not_contain_unicode_replacement_characters() -> None:
    for path in (DOCS / "index.html", DOCS / "styles.css", DOCS / "js" / "app.js"):
        assert "\ufffd" not in path.read_text(encoding="utf-8")
