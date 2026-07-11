from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext, Error, Page, Playwright, sync_playwright

from paper_radar.config import FeedConfig, TopicConfig
from paper_radar.database import (
    connect_database,
    create_run,
    finish_run,
    initialize_database,
    register_journal,
    replace_article_tags,
    upsert_article,
)
from paper_radar.models import ArticleRecord
from paper_radar.validation import publish_database, validate_database


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class PublishedSite:
    root: Path
    database: Path
    article_count: int


class _QuietHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".db": "application/octet-stream",
        ".js": "text/javascript; charset=utf-8",
        ".wasm": "application/wasm",
    }

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


@pytest.fixture(scope="session")
def published_site(tmp_path_factory: pytest.TempPathFactory) -> PublishedSite:
    workspace = tmp_path_factory.mktemp("paper-radar-e2e")
    site_root = workspace / "site"
    shutil.copytree(REPOSITORY_ROOT / "docs", site_root)
    working = workspace / "working.db"
    published = site_root / "data" / "papers.db"
    # The fixture publishes its own small orthogonal database. A real repository snapshot may
    # already be present in docs/, so remove only the temporary copy before exercising publish.
    published.unlink(missing_ok=True)

    connection = connect_database(working)
    try:
        initialize_database(connection)
        feeds = (
            FeedConfig("apl", "Applied Physics Letters", "aip", "https://example.test/apl.xml"),
            FeedConfig(
                "ieee-tu",
                "IEEE Transactions on Ultrasonics",
                "ieee",
                "https://example.test/ieee-tu.xml",
            ),
            FeedConfig(
                "ieee-sensors",
                "IEEE Sensors Journal",
                "ieee",
                "https://example.test/ieee-sensors.xml",
            ),
        )
        for feed in feeds:
            register_journal(connection, feed)

        topics = {
            "baw": TopicConfig("baw", "Bulk acoustic wave", ("BAW",)),
            "saw": TopicConfig("saw", "Surface acoustic wave", ("SAW",)),
            "ultrasound": TopicConfig("ultrasound", "Ultrasound", ("ultrasound",)),
        }
        records: list[tuple[ArticleRecord, tuple[str, ...]]] = [
            (
                ArticleRecord(
                    uid="malicious",
                    doi="10.9999/malicious",
                    journal_id="apl",
                    title='<svg onload="window.__e2eXss=1">dangerous title</svg>',
                    abstract=None,
                    authors=(),
                    published_at="2026-04-02T00:00:00+00:00",
                    article_type="research",
                    article_url="javascript:window.__e2eXss=2",
                    normalized_url=None,
                    oa_status="unknown",
                    source_feed_url=feeds[0].feed_url,
                    metadata_status="partial",
                ),
                ("baw",),
            ),
            (
                ArticleRecord(
                    uid="unicode",
                    doi="10.9999/unicode",
                    journal_id="apl",
                    title="Álpha AlScN 中文声学 resonator",
                    abstract="Unicode fixture for bulk and surface acoustic waves.",
                    authors=("张伟", "Ada Example"),
                    published_at="2026-04-01T00:00:00+00:00",
                    article_type="review",
                    article_url="https://example.test/articles/unicode",
                    normalized_url="https://example.test/articles/unicode",
                    oa_status="open",
                    source_feed_url=feeds[0].feed_url,
                    metadata_status="enriched",
                    enriched_fields=("title", "authors", "abstract", "oa_status"),
                ),
                ("baw", "saw"),
            ),
        ]
        matrix_cases = (
            ("apl", "research", "open", ("baw",)),
            ("apl", "review", "closed", ("saw",)),
            ("apl", "editorial", "unknown", ("ultrasound",)),
            ("ieee-tu", "research", "closed", ("baw", "saw")),
            ("ieee-tu", "review", "open", ("saw",)),
            ("ieee-tu", "editorial", "unknown", ("baw",)),
            ("ieee-sensors", "research", "unknown", ("ultrasound",)),
            ("ieee-sensors", "review", "closed", ("baw",)),
            ("ieee-sensors", "editorial", "open", ("saw", "ultrasound")),
            ("apl", "research", "open", ("baw", "ultrasound")),
        )
        matrix_start = datetime(2026, 3, 1, tzinfo=UTC)
        for index, (journal_id, article_type, oa_status, tag_ids) in enumerate(matrix_cases):
            feed = next(item for item in feeds if item.id == journal_id)
            records.append(
                (
                    ArticleRecord(
                        uid=f"matrix-{index:02d}",
                        doi=f"10.9999/matrix-{index:02d}",
                        journal_id=journal_id,
                        title=f"Matrix {index:02d} acoustic device",
                        abstract=f"Orthogonal matrix fixture {index:02d}.",
                        authors=(f"Matrix Author {index:02d}",),
                        published_at=(matrix_start + timedelta(days=index)).isoformat(),
                        article_type=article_type,
                        article_url=f"https://example.test/matrix/{index:02d}",
                        normalized_url=f"https://example.test/matrix/{index:02d}",
                        oa_status=oa_status,
                        source_feed_url=feed.feed_url,
                        metadata_status="rss_only",
                    ),
                    tag_ids,
                )
            )

        filler_start = datetime(2026, 1, 1, tzinfo=UTC)
        filler_feed = next(item for item in feeds if item.id == "ieee-sensors")
        for index in range(20):
            records.append(
                (
                    ArticleRecord(
                        uid=f"filler-{index:02d}",
                        doi=f"10.9999/filler-{index:02d}",
                        journal_id=filler_feed.id,
                        title=f"Filler {index:02d} reliability study",
                        abstract=None if index == 7 else f"Pagination filler {index:02d}.",
                        authors=() if index == 7 else (f"Filler Author {index:02d}",),
                        published_at=None
                        if index == 7
                        else (filler_start + timedelta(days=index)).isoformat(),
                        article_type="editorial",
                        article_url=f"https://example.test/filler/{index:02d}",
                        normalized_url=f"https://example.test/filler/{index:02d}",
                        oa_status="unknown",
                        source_feed_url=filler_feed.feed_url,
                        metadata_status="rss_only",
                    ),
                    ("ultrasound",),
                )
            )

        for record, tag_ids in records:
            assert upsert_article(connection, record) == "inserted"
            replace_article_tags(connection, record.uid, [topics[tag_id] for tag_id in tag_ids])
        run_id = create_run(connection)
        finish_run(
            connection,
            run_id,
            status="ok",
            inserted=len(records),
            updated=0,
            skipped=0,
            failed=0,
            notes="independent browser fixture",
        )
    finally:
        connection.close()

    report = publish_database(working, published)
    assert report.article_count == len(records)
    assert report.journal_count == 3
    assert validate_database(published).schema_version == 3
    return PublishedSite(site_root, published, len(records))


@pytest.fixture(scope="session")
def site_url(published_site: PublishedSite) -> Iterator[str]:
    handler = partial(_QuietHandler, directory=str(published_site.root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _launch_browser(playwright: Playwright) -> Browser:
    failures: list[str] = []
    for label, options in (("Microsoft Edge", {"channel": "msedge"}), ("Chromium", {})):
        try:
            return playwright.chromium.launch(headless=True, **options)
        except Error as error:
            failures.append(f"{label}: {str(error).splitlines()[0]}")
    diagnostic = "No Playwright-compatible browser is installed. " + " | ".join(failures)
    if os.environ.get("PAPER_RADAR_ALLOW_BROWSER_SKIP") == "1":
        pytest.skip(diagnostic)
    pytest.fail(diagnostic)


@pytest.fixture(scope="module")
def e2e_browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        browser = _launch_browser(playwright)
        yield browser
        browser.close()


@pytest.fixture
def browser_context(e2e_browser: Browser) -> Iterator[BrowserContext]:
    context = e2e_browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        yield context
    finally:
        context.close()


@pytest.fixture
def page(browser_context: BrowserContext, site_url: str) -> Iterator[Page]:
    page = browser_context.new_page()
    page.set_default_timeout(7_000)
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "requestfailed",
        lambda request: (
            failed_requests.append(f"{request.url}: {request.failure}")
            if request.url.startswith(site_url)
            else None
        ),
    )
    try:
        yield page
        assert console_errors == [], "console errors: " + " | ".join(console_errors)
        assert page_errors == [], "page errors: " + " | ".join(page_errors)
        assert failed_requests == [], "same-origin request failures: " + " | ".join(failed_requests)
    finally:
        page.close()
