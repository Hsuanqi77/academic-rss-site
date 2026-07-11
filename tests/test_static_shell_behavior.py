from __future__ import annotations

import functools
import json
import sqlite3
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

import pytest
from playwright.sync_api import Browser, Error, Page, Playwright, sync_playwright


DOCS = Path(__file__).resolve().parents[1] / "docs"
SCHEMA = DOCS.parent / "src" / "paper_radar" / "schema.sql"


class _QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


@pytest.fixture(scope="session")
def static_site_url() -> Iterator[str]:
    handler = functools.partial(_QuietStaticHandler, directory=str(DOCS))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def shell_browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        browser = _launch_available_browser(playwright)
        yield browser
        browser.close()


def _launch_available_browser(playwright: Playwright) -> Browser:
    failures: list[str] = []
    for label, options in (("Microsoft Edge", {"channel": "msedge"}), ("Chromium", {})):
        try:
            return playwright.chromium.launch(headless=True, **options)
        except Error as error:
            failures.append(f"{label}: {str(error).splitlines()[0]}")
    pytest.skip("No Playwright-compatible browser is installed. " + " | ".join(failures))


@pytest.fixture(scope="session")
def paper_db_bytes(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    database_path = tmp_path_factory.mktemp("paper-radar-web") / "papers.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(SCHEMA.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO journals(id,name,publisher,feed_url) VALUES(?,?,?,?)",
            ("apl", "Applied Physics Letters", "aip", "https://example.test/apl.xml"),
        )
        connection.executemany(
            "INSERT INTO tags(id,label) VALUES(?,?)",
            (("baw", "Bulk acoustic wave"), ("saw", "Surface acoustic wave")),
        )
        article_sql = """
            INSERT INTO articles(
                uid,doi,journal_id,title,abstract,authors_json,published_at,article_type,
                article_url,normalized_url,oa_status,source_feed_url,first_seen_at,last_updated_at
            ) VALUES(?,?,'apl',?,?,?,?,?,?,?,?,?,?,?)
        """
        connection.execute(
            article_sql,
            (
                "malicious",
                "10.1/malicious",
                '<img src=x onerror="window.__xss=1">',
                None,
                "not-json",
                "2026-02-01",
                "research",
                "javascript:window.__xss=2",
                None,
                "open",
                "https://example.test/apl.xml",
                "2026-01-01",
                "2026-01-01",
            ),
        )
        connection.execute("INSERT INTO article_tags VALUES('malicious','baw')")
        for index in range(25):
            uid = f"paper-{index:02d}"
            connection.execute(
                article_sql,
                (
                    uid,
                    f"10.1/{uid}",
                    f"Paper {index:02d} AlScN",
                    f"Abstract {index:02d}",
                    json.dumps(["Ada", {"name": "Grace"}], ensure_ascii=False),
                    f"2026-01-{index + 1:02d}",
                    "review" if index % 5 == 0 else "research",
                    f"https://example.test/{uid}",
                    f"https://example.test/{uid}",
                    "open" if index % 2 == 0 else "closed",
                    "https://example.test/apl.xml",
                    "2026-01-01",
                    "2026-01-01",
                ),
            )
            connection.execute("INSERT INTO article_tags VALUES(?, 'baw')", (uid,))
            if index % 2 == 0:
                connection.execute("INSERT INTO article_tags VALUES(?, 'saw')", (uid,))
        connection.commit()
    finally:
        connection.close()
    return database_path.read_bytes()


@pytest.fixture
def page_factory(
    shell_browser: Browser,
    static_site_url: str,
    paper_db_bytes: bytes,
) -> Iterator[Callable[..., Page]]:
    pages: list[Page] = []

    def create(
        *,
        width: int = 1000,
        search: str = "",
        database_status: int = 200,
        database_body: bytes | None = None,
        init_script: str | None = None,
    ) -> Page:
        page = shell_browser.new_page(viewport={"width": width, "height": 800})
        page.set_default_timeout(5_000)
        if init_script:
            page.add_init_script(init_script)
            page.route(
                "**/js/app.js",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/javascript; charset=utf-8",
                    body="""
                      import { createAppController } from './controller.js';
                      import { DEFAULT_STATE, parseState, serializeState } from './state.js';
                      const controller = createAppController({
                        ...window.__PAPER_RADAR_DEPENDENCIES__,
                        defaultState: DEFAULT_STATE,
                        parseStateFn: parseState,
                        serializeStateFn: serializeState,
                      });
                      window.__testController = controller;
                      window.__testStart = controller.start();
                      window.addEventListener('pagehide', event => {
                        if (!event.persisted) controller.destroy();
                      });
                    """,
                ),
            )
        body = paper_db_bytes if database_body is None else database_body
        page.route(
            "**/data/papers.db*",
            lambda route: route.fulfill(
                status=database_status,
                body=body if database_status == 200 else b"",
                content_type="application/octet-stream",
            ),
        )
        page.goto(f"{static_site_url}{search}")
        pages.append(page)
        return page

    yield create
    for page in pages:
        if not page.is_closed():
            page.close()


@pytest.fixture
def mobile_page(page_factory: Callable[..., Page]) -> Page:
    page = page_factory(width=500)
    page.wait_for_function(
        """() => document.querySelector('#status')?.textContent.includes('界面已准备')
          || document.querySelector('#article-list')?.getAttribute('aria-busy') === 'false'
          || document.querySelector('#status')?.classList.contains('error')"""
    )
    return page


@pytest.fixture
def app_page(page_factory: Callable[..., Page]) -> Page:
    page = page_factory()
    page.wait_for_function(
        "document.querySelector('#article-list')?.getAttribute('aria-busy') === 'false'"
    )
    return page


def test_mobile_drawer_becomes_modal_and_isolates_background(mobile_page: Page) -> None:
    mobile_page.locator("#open-filters").click()

    state = mobile_page.evaluate(
        """() => ({
          role: document.querySelector('#filters').getAttribute('role'),
          modal: document.querySelector('#filters').getAttribute('aria-modal'),
          expanded: document.querySelector('#open-filters').getAttribute('aria-expanded'),
          bodyLocked: document.body.classList.contains('drawer-open'),
          overlayHidden: document.querySelector('#filter-overlay').hidden,
          active: document.activeElement.id,
          backgrounds: [...document.querySelectorAll('[data-drawer-background]')].map(
            element => ({inert: element.inert, hidden: element.getAttribute('aria-hidden')})
          ),
        })"""
    )

    assert state["role"] == "dialog"
    assert state["modal"] == "true"
    assert state["expanded"] == "true"
    assert state["bodyLocked"] is True
    assert state["overlayHidden"] is False
    assert state["active"] == "close-filters"
    assert len(state["backgrounds"]) >= 5
    assert all(item["inert"] and item["hidden"] == "true" for item in state["backgrounds"])


def test_mobile_drawer_wraps_tab_focus_in_both_directions(mobile_page: Page) -> None:
    mobile_page.locator("#open-filters").click()

    mobile_page.keyboard.press("Shift+Tab")
    assert mobile_page.evaluate("document.activeElement.id") == "clear-filters"
    mobile_page.keyboard.press("Tab")
    assert mobile_page.evaluate("document.activeElement.id") == "close-filters"


def test_escape_and_overlay_restore_focus_and_exact_background_state(
    mobile_page: Page,
) -> None:
    mobile_page.evaluate(
        """() => {
          const guide = document.querySelector('#guide');
          guide.inert = true;
          guide.setAttribute('aria-hidden', 'false');
        }"""
    )

    mobile_page.locator("#open-filters").click()
    mobile_page.keyboard.press("Escape")
    assert mobile_page.evaluate("document.activeElement.id") == "open-filters"
    assert mobile_page.locator("#guide").get_attribute("aria-hidden") == "false"
    assert mobile_page.locator("#guide").evaluate("element => element.inert") is True

    mobile_page.locator("#open-filters").click()
    mobile_page.mouse.click(480, 400)
    assert mobile_page.evaluate("document.activeElement.id") == "open-filters"
    assert mobile_page.locator("#filters").get_attribute("role") is None
    assert mobile_page.locator("#filters").get_attribute("aria-modal") is None
    assert mobile_page.locator("#guide").get_attribute("aria-hidden") == "false"
    assert mobile_page.locator("#guide").evaluate("element => element.inert") is True


def test_guide_disclosures_are_keyboard_operable_and_use_two_columns_on_desktop(
    page_factory: Callable[..., Page],
) -> None:
    page = page_factory()
    first = page.locator("#guide details").first
    summary = first.locator("summary")

    assert first.get_attribute("open") == ""
    summary.focus()
    page.keyboard.press("Enter")
    assert first.get_attribute("open") is None
    page.keyboard.press("Enter")
    assert first.get_attribute("open") == ""
    assert "Nature Communications" in (page.locator("#guide").text_content() or "")
    assert "bulk acoustic wave" in (page.locator("#guide").text_content() or "")
    assert page.locator(".guide-grid").first.evaluate(
        "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
    ) == 2


def test_guide_stacks_at_390px_without_overflow_and_keeps_approved_sizes(
    page_factory: Callable[..., Page],
) -> None:
    page = page_factory(width=390)

    assert page.locator(".guide-group summary").first.evaluate(
        "element => getComputedStyle(element).fontSize"
    ) == "13px"
    assert page.locator(".guide-tag-name").first.evaluate(
        "element => getComputedStyle(element).fontSize"
    ) == "12px"
    assert page.locator(".guide-keywords").first.evaluate(
        "element => getComputedStyle(element).fontSize"
    ) == "11px"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth") is True
    assert page.locator(".guide-grid").first.evaluate(
        "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
    ) == 1


def test_desktop_to_closed_mobile_moves_focus_before_inerting_drawer(
    mobile_page: Page,
) -> None:
    mobile_page.set_viewport_size({"width": 1000, "height": 800})
    mobile_page.locator("#date-from").focus()
    mobile_page.set_viewport_size({"width": 500, "height": 800})
    mobile_page.wait_for_function("document.querySelector('#filters').inert")

    assert mobile_page.evaluate("document.activeElement.id") == "open-filters"
    assert mobile_page.locator("#filters").get_attribute("aria-hidden") == "true"


def test_open_mobile_to_desktop_cleans_modal_state_and_repairs_focus(
    mobile_page: Page,
) -> None:
    mobile_page.locator("#open-filters").click()
    mobile_page.set_viewport_size({"width": 1000, "height": 800})
    mobile_page.wait_for_function("!document.body.classList.contains('drawer-open')")

    state = mobile_page.evaluate(
        """() => ({
          role: document.querySelector('#filters').getAttribute('role'),
          modal: document.querySelector('#filters').getAttribute('aria-modal'),
          inert: document.querySelector('#filters').inert,
          hidden: document.querySelector('#filters').getAttribute('aria-hidden'),
          active: document.activeElement.id,
          backgrounds: [...document.querySelectorAll('[data-drawer-background]')].map(
            element => ({inert: element.inert, hidden: element.getAttribute('aria-hidden')})
          ),
        })"""
    )

    assert state["role"] is None
    assert state["modal"] is None
    assert state["inert"] is False
    assert state["hidden"] is None
    assert state["active"] == "date-from"
    assert all(not item["inert"] and item["hidden"] is None for item in state["backgrounds"])


def test_application_loads_database_and_renders_untrusted_rows_safely(app_page: Page) -> None:
    assert app_page.locator("#result-count").text_content() == "26"
    assert app_page.locator(".article-card").count() == 20
    malicious = app_page.locator('[data-article-uid="malicious"]')
    assert '<img src=x onerror="window.__xss=1">' in malicious.locator("h2").inner_text()
    assert malicious.locator("h2 a").count() == 0
    assert malicious.locator(".authors").count() == 0
    assert malicious.locator(".abstract").count() == 0
    assert app_page.evaluate("window.__xss") is None
    assert app_page.locator('a[href^="javascript:"]').count() == 0
    assert app_page.locator("#journal option").all_text_contents() == [
        "全部期刊",
        "Applied Physics Letters",
    ]
    assert "review" in app_page.locator("#article-type option").evaluate_all(
        "options => options.map(option => option.value)"
    )
    assert app_page.locator("#tag-options input").count() == 2
    assert "26" in (app_page.locator("#database-summary").text_content() or "")


def test_url_initial_state_and_unavailable_options_are_reconciled(
    page_factory: Callable[..., Page],
) -> None:
    page = page_factory(search="?q=Paper&tag=baw&sort=oldest&page=2")
    page.wait_for_function(
        "document.querySelector('#article-list')?.getAttribute('aria-busy') === 'false'"
    )
    assert page.locator("#search").input_value() == "Paper"
    assert page.locator('[data-tag="baw"]').is_checked()
    assert page.locator("#sort").input_value() == "oldest"
    assert page.locator("#result-count").text_content() == "25"
    assert page.locator('[aria-current="page"]').text_content() == "2"
    assert page.locator(".article-card").count() == 5

    unavailable = page_factory(search="?journal=missing&tag=missing&page=4")
    unavailable.wait_for_function(
        "document.querySelector('#article-list')?.getAttribute('aria-busy') === 'false'"
    )
    assert unavailable.locator("#journal").input_value() == ""
    assert unavailable.evaluate("new URLSearchParams(location.search).has('journal')") is False
    assert unavailable.evaluate("new URLSearchParams(location.search).has('tag')") is False
    assert unavailable.evaluate("new URLSearchParams(location.search).get('page')") == "2"
    assert unavailable.locator("#result-count").text_content() == "26"


def test_search_tag_and_sort_updates_are_parameterized_and_reset_page(app_page: Page) -> None:
    app_page.locator("#search").fill("Paper 02")
    app_page.wait_for_function("document.querySelector('#result-count').textContent === '1'")
    assert app_page.evaluate("new URLSearchParams(location.search).get('q')") == "Paper 02"
    assert app_page.evaluate("new URLSearchParams(location.search).has('page')") is False

    app_page.locator("#search").fill("")
    app_page.wait_for_function("document.querySelector('#result-count').textContent === '26'")
    app_page.locator('[data-tag="saw"]').check()
    app_page.wait_for_function("document.querySelector('#result-count').textContent === '13'")
    assert app_page.evaluate("new URLSearchParams(location.search).getAll('tag')") == ["saw"]

    app_page.locator("#sort").select_option("oldest")
    app_page.wait_for_function(
        "document.querySelector('.article-card')?.dataset.articleUid === 'paper-00'"
    )
    assert app_page.evaluate("new URLSearchParams(location.search).get('sort')") == "oldest"


def test_pagination_clear_and_popstate_restore_the_view(app_page: Page) -> None:
    app_page.get_by_role("button", name="下一页").click()
    app_page.wait_for_function(
        "document.querySelector('[aria-current=\"page\"]')?.textContent === '2'"
    )
    assert app_page.evaluate("new URLSearchParams(location.search).get('page')") == "2"

    app_page.locator("#search").fill("Paper 24")
    app_page.wait_for_function("document.querySelector('#result-count').textContent === '1'")
    assert app_page.evaluate("new URLSearchParams(location.search).has('page')") is False

    app_page.locator("#clear-filters").click()
    app_page.wait_for_function("document.querySelector('#result-count').textContent === '26'")
    assert app_page.evaluate("location.search") == ""
    assert app_page.locator("#search").input_value() == ""
    assert app_page.locator("#active-filter-count").text_content() == "0"

    app_page.evaluate("history.pushState(null, '', '?q=Paper+03')")
    app_page.evaluate("history.pushState(null, '', '?q=Paper+04')")
    app_page.go_back()
    app_page.wait_for_function("document.querySelector('#result-count').textContent === '1'")
    assert app_page.locator("#search").input_value() == "Paper 03"
    app_page.go_forward()
    app_page.wait_for_function("document.querySelector('#search').value === 'Paper 04'")
    assert app_page.locator("#result-count").text_content() == "1"


def test_empty_and_database_error_states_are_readable(
    app_page: Page,
    page_factory: Callable[..., Page],
) -> None:
    app_page.locator("#search").fill("definitely-no-match")
    app_page.wait_for_function("document.querySelector('#result-count').textContent === '0'")
    assert app_page.locator(".empty-state").count() == 1
    assert "没有匹配" in (app_page.locator("#status").text_content() or "")

    error_page = page_factory(database_status=503)
    error_page.wait_for_function("document.querySelector('#status')?.classList.contains('error')")
    assert "HTTP 503" in (error_page.locator("#status").text_content() or "")
    assert error_page.locator("#article-list").get_attribute("aria-busy") == "false"
    assert "失败" in (error_page.locator("#database-summary").text_content() or "")


def test_post_open_failure_closes_database_once_without_masking_original_error(
    page_factory: Callable[..., Page],
) -> None:
    page = page_factory(
        init_script="""
          window.__closed = 0;
          window.__PAPER_RADAR_DEPENDENCIES__ = {
            loadDatabaseFn: async () => ({
              close() { window.__closed += 1; throw new Error('close failed'); },
            }),
            loadFilterOptionsFn: () => { throw new Error('options failed'); },
            queryArticlesFn: () => { throw new Error('query should not run'); },
          };
        """
    )
    page.wait_for_function("document.querySelector('#status')?.classList.contains('error')")
    assert "options failed" in (page.locator("#status").text_content() or "")
    assert "close failed" not in (page.locator("#status").text_content() or "")
    assert page.evaluate("window.__closed") == 1
    page.evaluate(
        """() => {
          dispatchEvent(new PageTransitionEvent('pagehide', { persisted: false }));
          dispatchEvent(new PageTransitionEvent('pagehide', { persisted: false }));
        }"""
    )
    assert page.evaluate("window.__closed") == 1


def test_query_busy_ready_announcement_pagination_focus_and_debounce_interlock(
    app_page: Page,
) -> None:
    status = app_page.locator("#status")
    assert status.is_hidden() is False
    assert "status-sr-only" in (status.get_attribute("class") or "")
    assert "26 篇" in (status.text_content() or "")
    assert "第 1 页" in (status.text_content() or "")

    next_button = app_page.get_by_role("button", name="下一页")
    next_button.focus()
    app_page.keyboard.press("Enter")
    app_page.wait_for_function(
        "document.querySelector('[aria-current=\"page\"]')?.textContent === '2'"
    )
    assert app_page.evaluate("document.activeElement.getAttribute('aria-current')") == "page"

    app_page.evaluate(
        """() => {
          window.__busyOldValues = [];
          window.__countMutations = [];
          new MutationObserver(records => {
            window.__busyOldValues.push(...records.map(record => record.oldValue));
          }).observe(document.querySelector('#article-list'), {
            attributes: true, attributeFilter: ['aria-busy'], attributeOldValue: true,
          });
          new MutationObserver(() => {
            window.__countMutations.push(document.querySelector('#result-count').textContent);
          }).observe(document.querySelector('#result-count'), { childList: true, subtree: true });
        }"""
    )
    app_page.locator("#search").fill("Paper 24")
    app_page.locator("#sort").select_option("oldest")
    app_page.wait_for_function("document.querySelector('#result-count').textContent === '1'")
    app_page.wait_for_timeout(300)
    assert app_page.evaluate("window.__busyOldValues") == ["false", "true"]
    assert app_page.locator("#article-list").get_attribute("aria-busy") == "false"
    assert app_page.evaluate("window.__countMutations") == ["1"]
    assert app_page.evaluate("new URLSearchParams(location.search).get('q')") == "Paper 24"


def test_startup_queries_once_and_pagehide_respects_bfcache(
    page_factory: Callable[..., Page],
) -> None:
    page = page_factory(
        init_script="""
          window.__closed = 0;
          window.__queries = 0;
          window.__PAPER_RADAR_DEPENDENCIES__ = {
            loadDatabaseFn: async () => ({ close() { window.__closed += 1; } }),
            loadFilterOptionsFn: () => ({
              journals: [], publishers: [], tags: [], articleTypes: [], oaStatuses: [],
            }),
            queryArticlesFn: () => {
              window.__queries += 1;
              return { rows: [], total: 7, page: 1, pageSize: 20 };
            },
          };
        """
    )
    page.wait_for_function(
        "document.querySelector('#article-list')?.getAttribute('aria-busy') === 'false'"
    )
    assert page.evaluate("window.__queries") == 1
    assert "7" in (page.locator("#database-summary").text_content() or "")
    page.evaluate("dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }))")
    assert page.evaluate("window.__closed") == 0
    page.evaluate(
        """() => {
          dispatchEvent(new PageTransitionEvent('pagehide', { persisted: false }));
          dispatchEvent(new PageTransitionEvent('pagehide', { persisted: false }));
        }"""
    )
    assert page.evaluate("window.__closed") == 1


def test_destroy_during_pending_load_swallows_close_failure_and_stays_idempotent(
    page_factory: Callable[..., Page],
) -> None:
    page = page_factory(
        init_script="""
          window.__closed = 0;
          window.__unhandled = [];
          addEventListener('unhandledrejection', event => window.__unhandled.push(String(event.reason)));
          window.__PAPER_RADAR_DEPENDENCIES__ = {
            loadDatabaseFn: () => new Promise(resolve => {
              window.__resolveLoad = () => resolve({
                close() { window.__closed += 1; throw new Error('pending close failed'); },
              });
            }),
            loadFilterOptionsFn: () => { throw new Error('must not initialize after destroy'); },
            queryArticlesFn: () => { throw new Error('must not query after destroy'); },
          };
        """
    )
    page.wait_for_function("typeof window.__resolveLoad === 'function'")
    page.evaluate("dispatchEvent(new PageTransitionEvent('pagehide', { persisted: false }))")
    page.evaluate("window.__resolveLoad()")
    page.wait_for_function("window.__closed === 1")
    page.evaluate(
        """() => {
          window.__testController.destroy();
          window.__testController.destroy();
        }"""
    )
    assert page.evaluate("window.__closed") == 1
    assert page.evaluate("window.__unhandled") == []
