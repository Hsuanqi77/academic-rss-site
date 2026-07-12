from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
FONT_DIR = DOCS / "fonts" / "noto-sans-sc"
NOTO_SANS_SC_PACKAGE = "@fontsource-variable/noto-sans-sc"
NOTO_SANS_SC_VERSION = "5.2.10"
NOTO_SANS_SC_TARBALL = (
    "https://registry.npmjs.org/@fontsource-variable/noto-sans-sc/-/"
    "noto-sans-sc-5.2.10.tgz"
)
NOTO_SANS_SC_INTEGRITY = (
    "sha512-zdk10i5HrDQTXI7ldD61zToX1fsgig8vDTsu7zB48SXOitWfuX0e5viZAwnkHuhwh"
    "096PU6X6i1AyAsbBCISpA=="
)
NOTO_SANS_SC_UPSTREAM_VERSION = "v40"
NOTO_SANS_SC_LICENSE = "OFL-1.1"
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
        self.id_counts: dict[str, int] = {}
        self.details_count = 0
        self.summary_count = 0
        self.unsafe_external_links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "html":
            self.lang = attributes.get("lang")
        if element_id := attributes.get("id"):
            self.ids.add(element_id)
            self.id_counts[element_id] = self.id_counts.get(element_id, 0) + 1
        if tag == "details":
            self.details_count += 1
        if tag == "summary":
            self.summary_count += 1
        if tag == "a" and (href := attributes.get("href")):
            if href.startswith(("http://", "https://")) and (
                attributes.get("target") != "_blank"
                or set((attributes.get("rel") or "").split())
                != {"noopener", "noreferrer"}
            ):
                self.unsafe_external_links.append(href)
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
    app = "\n".join(
        (DOCS / "js" / name).read_text(encoding="utf-8") for name in ("app.js", "controller.js")
    )
    css = (DOCS / "styles.css").read_text(encoding="utf-8")

    assert "aria-expanded" in app
    assert "Escape" in app
    assert "focus" in app
    assert "drawer-open" in app
    assert "prefers-reduced-motion" in css
    assert "overflow-x" in css
    assert "focus-visible" in css


def test_guide_has_accessible_native_disclosures_and_safe_links() -> None:
    html = (DOCS / "index.html").read_text(encoding="utf-8")
    parser = _ShellParser()
    parser.feed(html)

    assert parser.id_counts.get("guide") == 1
    assert parser.details_count == 16
    assert parser.summary_count == 16
    assert parser.unsafe_external_links == []


def test_guide_uses_approved_type_sizes_and_responsive_grid() -> None:
    css = (DOCS / "styles.css").read_text(encoding="utf-8")

    assert re.search(r"\.guide-group summary\s*\{[^}]*font-size:\s*13px", css, re.S)
    assert re.search(r"\.guide-tag-name\s*\{[^}]*font-size:\s*12px", css, re.S)
    assert re.search(r"\.guide-keywords\s*\{[^}]*font:\s*11px/1\.65", css, re.S)
    assert re.search(
        r"@media\s*\(max-width:\s*820px\)\s*\{.*?"
        r"\.guide-grid\s*\{[^}]*grid-template-columns:\s*1fr",
        css,
        re.S,
    )


def test_guide_disclosure_indicator_and_focus_outline_are_not_clipped() -> None:
    css = (DOCS / "styles.css").read_text(encoding="utf-8")
    group_rule = re.search(r"\.guide-group\s*\{([^}]+)\}", css)

    assert group_rule is not None
    assert "overflow: hidden" not in group_rule.group(1)
    assert re.search(
        r"\.guide-group summary::before\s*\{[^}]*content:\s*[\"'][\"']"
        r"[^}]*border-right:\s*2px\s+solid\s+currentColor"
        r"[^}]*border-bottom:\s*2px\s+solid\s+currentColor",
        css,
        re.S,
    )
    assert re.search(
        r"\.guide-group\[open\] summary::before\s*\{[^}]*transform:", css, re.S
    )


def test_typography_uses_local_noto_sans_sc_without_resizing_exclusions() -> None:
    css = (DOCS / "styles.css").read_text(encoding="utf-8")

    assert (
        '--ui: "Noto Sans SC Variable", "Microsoft YaHei UI", "PingFang SC", sans-serif;'
        in css
    )
    assert (
        '--editorial: "Noto Sans SC Variable", "Microsoft YaHei UI", "PingFang SC", '
        "sans-serif;"
        in css
    )
    eyebrow_rule = re.search(r"\.eyebrow, \.filter-index\s*\{([^}]+)\}", css)
    assert eyebrow_rule is not None
    assert "font: 700 13px/1.2 ui-monospace, Consolas, monospace;" in eyebrow_rule.group(1)

    exclusion_contracts = (
        r"\.edition\s*\{[^}]*font: 11px/1\.5 ui-monospace, Consolas, monospace;",
        r"\.search-label\s*\{[^}]*font: 700 11px/1 var\(--ui\);",
        r"footer\s*\{[^}]*font: 10px/1\.4 ui-monospace, Consolas, monospace;",
        r"\.badge\s*\{[^}]*font: 700 11px/1 var\(--ui\);",
        r"\.brand small\s*\{[^}]*font-size:\s*10px;",
        r"\.chip\s*\{[^}]*font-size: 11px;",
    )
    for contract in exclusion_contracts:
        assert re.search(contract, css), contract


def test_interactive_typography_has_medium_weight_without_duplicate_selectors() -> None:
    css = (DOCS / "styles.css").read_text(encoding="utf-8")
    selectors = (
        ".site-header nav a",
        ".filters input, .filters select, .search-row input, .search-row select",
        ".secondary, .filter-toggle, .icon-button",
        ".chip",
        ".pagination button",
    )

    for selector in selectors:
        rules = re.findall(rf"{re.escape(selector)}\s*\{{([^}}]+)\}}", css)
        assert len(rules) == 1, selector
        assert "font-weight: 500;" in rules[0], selector


def test_text_assets_do_not_contain_unicode_replacement_characters() -> None:
    for path in (DOCS / "index.html", DOCS / "styles.css", DOCS / "js" / "app.js"):
        assert "\ufffd" not in path.read_text(encoding="utf-8")


def test_noto_sans_sc_is_pinned_auditable_and_fully_local() -> None:
    metadata = (FONT_DIR / "FONT-METADATA.md").read_text(encoding="utf-8")
    license_text = (FONT_DIR / "LICENSE.txt").read_text(encoding="utf-8")
    checksums_text = (FONT_DIR / "SHA256SUMS").read_text(encoding="utf-8")
    css = (DOCS / "styles.css").read_text(encoding="utf-8")

    assert f"Package: `{NOTO_SANS_SC_PACKAGE}`" in metadata
    assert f"Version: `{NOTO_SANS_SC_VERSION}`" in metadata
    assert f"Tarball: `{NOTO_SANS_SC_TARBALL}`" in metadata
    assert f"Integrity: `{NOTO_SANS_SC_INTEGRITY}`" in metadata
    assert f"Upstream font version: `{NOTO_SANS_SC_UPSTREAM_VERSION}`" in metadata
    assert f"License: `{NOTO_SANS_SC_LICENSE}`" in metadata
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text

    marker_start = f"/* BEGIN VENDORED NOTO SANS SC {NOTO_SANS_SC_VERSION} */"
    marker_end = f"/* END VENDORED NOTO SANS SC {NOTO_SANS_SC_VERSION} */"
    assert css.count(marker_start) == 1
    assert css.count(marker_end) == 1
    vendored_css = css.split(marker_start, 1)[1].split(marker_end, 1)[0]
    faces = re.findall(r"@font-face\s*\{(.*?)\}", vendored_css, flags=re.DOTALL)
    assert len(faces) == 98
    assert all('font-family: "Noto Sans SC Variable";' in face for face in faces)
    assert all("font-display: swap;" in face for face in faces)
    assert all("font-weight: 100 900;" in face for face in faces)
    assert "fonts.googleapis.com" not in css
    assert "fonts.gstatic.com" not in css

    css_files = set(re.findall(r"\./fonts/noto-sans-sc/([^)'\"]+\.woff2)", vendored_css))
    directory_files = {path.name for path in FONT_DIR.glob("*.woff2")}
    allowed_filename = re.compile(r"noto-sans-sc-(?:\d+|latin)-wght-normal\.woff2")
    css_urls = re.findall(r"url\(([^)]+)\)", vendored_css)
    checksum_entries = {}
    for line in checksums_text.splitlines():
        digest, filename = line.split("  ", 1)
        checksum_entries[filename] = digest

    assert len(css_files) == 98
    assert css_files == directory_files == set(checksum_entries)
    assert sum((FONT_DIR / filename).stat().st_size for filename in directory_files) == 4_489_160
    assert all(allowed_filename.fullmatch(filename) for filename in directory_files)
    assert len(css_urls) == 98
    assert all(
        re.fullmatch(
            r"\./fonts/noto-sans-sc/noto-sans-sc-(?:\d+|latin)-wght-normal\.woff2",
            url,
        )
        for url in css_urls
    )
    assert "noto-sans-sc-latin-wght-normal.woff2" in css_files
    assert not any(
        excluded in filename
        for filename in css_files
        for excluded in ("cyrillic", "latin-ext", "vietnamese")
    )
    for filename in sorted(css_files):
        payload = (FONT_DIR / filename).read_bytes()
        assert payload.startswith(b"wOF2"), filename
        assert hashlib.sha256(payload).hexdigest() == checksum_entries[filename]
