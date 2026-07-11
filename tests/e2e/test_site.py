from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import BrowserContext, Page, Route, expect


def _wait_ready(page: Page) -> None:
    page.wait_for_function(
        "document.querySelector('#article-list')?.getAttribute('aria-busy') === 'false'"
    )


def _query(page: Page) -> dict[str, list[str]]:
    return parse_qs(urlparse(page.url).query)


def _visible_uids(page: Page) -> set[str]:
    return set(
        page.locator(".article-card").evaluate_all(
            "cards => cards.map(card => card.dataset.articleUid)"
        )
    )


def test_local_noto_sans_sc_typography_is_applied(page: Page, site_url: str) -> None:
    font_requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            font_requests.append(request.url)
            if "/fonts/noto-sans-sc/" in request.url
            else None
        ),
    )

    response = page.goto(site_url)
    assert response is not None and response.ok
    _wait_ready(page)
    loaded_faces = page.evaluate(
        """async () => (await document.fonts.load(
            '400 16px "Noto Sans SC Variable"',
            '最新论文'
        )).length"""
    )

    assert loaded_faces > 0
    assert font_requests
    assert all(
        (urlparse(request_url).scheme, urlparse(request_url).netloc)
        == (urlparse(site_url).scheme, urlparse(site_url).netloc)
        for request_url in font_requests
    )
    assert "Noto Sans SC Variable" in page.locator("body").evaluate(
        "element => getComputedStyle(element).fontFamily"
    )
    assert "Noto Sans SC Variable" in page.locator(".article-card h2").first.evaluate(
        "element => getComputedStyle(element).fontFamily"
    )
    expect(page.locator(".eyebrow").first).to_have_css("font-size", "13px")
    expect(page.locator(".filter-index").first).to_have_css("font-size", "13px")
    expect(page.locator(".edition")).to_have_css("font-size", "11px")
    expect(page.locator(".search-label")).to_have_css("font-size", "11px")
    expect(page.locator(".brand-mark")).to_have_css("font-family", "Georgia, serif")


def test_font_request_failure_keeps_site_usable(
    browser_context: BrowserContext,
    site_url: str,
) -> None:
    fallback_page = browser_context.new_page()
    fallback_page.set_default_timeout(7_000)
    intercepted: list[str] = []

    def abort_font(route: Route) -> None:
        intercepted.append(route.request.url)
        route.abort()

    fallback_page.route("**/fonts/noto-sans-sc/*.woff2", abort_font)
    try:
        response = fallback_page.goto(site_url)
        assert response is not None and response.ok
        _wait_ready(fallback_page)
        expect(fallback_page.locator(".article-card")).to_have_count(20)
        search = fallback_page.locator("#search")
        search.fill("Matrix")
        expect(search).to_have_value("Matrix")
        assert intercepted
        assert fallback_page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ) is True
    finally:
        fallback_page.close()


def test_initial_real_http_load_has_no_browser_errors(
    page: Page,
    site_url: str,
    published_site: Any,
) -> None:
    response = page.goto(site_url)
    assert response is not None and response.ok
    _wait_ready(page)
    expect(page.locator("#result-count")).to_have_text(str(published_site.article_count))
    expect(page.locator(".article-card")).to_have_count(20)
    expect(page.locator("#database-summary")).to_contain_text(str(published_site.article_count))
    status = page.locator("#status")
    expect(status).to_have_attribute("aria-live", "polite")
    expect(status).to_have_class("status status-sr-only")
    expect(status).to_contain_text("第 1 页")


def test_share_url_restores_search_tag_and_journal_after_refresh(page: Page, site_url: str) -> None:
    page.goto(f"{site_url}/?q=AlScN&tag=baw&journal=apl")
    _wait_ready(page)
    expect(page.locator("#search")).to_have_value("AlScN")
    expect(page.locator("#journal")).to_have_value("apl")
    expect(page.locator('[data-tag="baw"]')).to_be_checked()
    expect(page.locator("#result-count")).to_have_text("1")
    assert _visible_uids(page) == {"unicode"}
    before = page.url
    page.reload()
    _wait_ready(page)
    assert page.url == before
    expect(page.locator("#result-count")).to_have_text("1")
    expect(page.locator('[data-tag="baw"]')).to_be_checked()


def test_each_filter_and_combination_has_an_exact_orthogonal_result(
    page: Page,
    site_url: str,
) -> None:
    page.goto(site_url)
    _wait_ready(page)

    def assert_result(expected_uids: set[str]) -> None:
        expect(page.locator("#result-count")).to_have_text(str(len(expected_uids)))
        expect(page.locator("#status")).to_contain_text(f"已显示 {len(expected_uids)} 篇")
        assert _visible_uids(page) == expected_uids

    aip_uids = {
        "malicious",
        "unicode",
        "matrix-00",
        "matrix-01",
        "matrix-02",
        "matrix-09",
    }
    scenarios = (
        ("#journal", "apl", aip_uids),
        ("#publisher", "aip", aip_uids),
        ("#oa-status", "open", {"unicode", "matrix-00", "matrix-04", "matrix-08", "matrix-09"}),
        ("#article-type", "review", {"unicode", "matrix-01", "matrix-04", "matrix-07"}),
    )
    for selector, value, expected_uids in scenarios:
        page.locator(selector).select_option(value)
        assert_result(expected_uids)
        page.locator("#clear-filters").click()
        expect(page.locator("#result-count")).to_have_text("32")

    date_scenarios = (
        ("#date-from", "2026-04-01", {"malicious", "unicode"}),
        ("#date-to", "2026-01-03", {"filler-00", "filler-01", "filler-02"}),
    )
    for selector, value, expected_uids in date_scenarios:
        control = page.locator(selector)
        control.fill(value)
        control.press("Tab")
        assert_result(expected_uids)
        page.locator("#clear-filters").click()
        expect(page.locator("#result-count")).to_have_text("32")

    page.locator('[data-tag="baw"]').check()
    baw_uids = {
        "malicious",
        "unicode",
        "matrix-00",
        "matrix-03",
        "matrix-05",
        "matrix-07",
        "matrix-09",
    }
    assert_result(baw_uids)
    page.locator("#clear-filters").click()

    page.locator("#journal").select_option("ieee-tu")
    page.locator("#article-type").select_option("research")
    page.locator("#oa-status").select_option("closed")
    page.locator('[data-tag="saw"]').check()
    expect(page.locator("#result-count")).to_have_text("1")
    assert _visible_uids(page) == {"matrix-03"}


def test_desktop_filters_pagination_clear_and_real_history(page: Page, site_url: str) -> None:
    page.goto(site_url)
    _wait_ready(page)
    page.get_by_role("button", name="下一页").click()
    expect(page.locator('[aria-current="page"]')).to_have_text("2")
    assert _query(page)["page"] == ["2"]

    page.locator("#search").fill("Matrix")
    page.locator("#journal").select_option("ieee-tu")
    page.locator("#oa-status").select_option("closed")
    page.locator('[data-tag="baw"]').check()
    page.locator("#article-type").select_option("research")
    page.locator("#sort").select_option("oldest")
    page.wait_for_function("document.querySelector('#result-count').textContent !== '32'")
    query = _query(page)
    assert query["q"] == ["Matrix"]
    assert query["journal"] == ["ieee-tu"]
    assert query["oa"] == ["closed"]
    assert query["tag"] == ["baw"]
    assert query["type"] == ["research"]
    assert query["sort"] == ["oldest"]
    assert "page" not in query
    expect(page.locator("#result-count")).to_have_text("1")
    assert _visible_uids(page) == {"matrix-03"}

    page.locator("#clear-filters").click()
    expect(page.locator("#result-count")).to_have_text("32")
    assert urlparse(page.url).query == ""
    expect(page.locator("#active-filter-count")).to_have_text("0")

    page.evaluate("history.pushState(null, '', '?q=Filler+01')")
    page.evaluate("history.pushState(null, '', '?q=Filler+02')")
    page.go_back()
    expect(page.locator("#search")).to_have_value("Filler 01")
    expect(page.locator("#result-count")).to_have_text("1")
    page.go_forward()
    expect(page.locator("#search")).to_have_value("Filler 02")
    expect(page.locator("#result-count")).to_have_text("1")


def test_untrusted_title_and_url_never_become_active_content(page: Page, site_url: str) -> None:
    page.goto(f"{site_url}/?q=dangerous")
    _wait_ready(page)
    card = page.locator('[data-article-uid="malicious"]')
    expect(card.locator("h2")).to_contain_text("<svg onload=")
    expect(card.locator("h2 a")).to_have_count(0)
    expect(card.locator(".authors")).to_have_count(0)
    expect(card.locator(".abstract")).to_have_count(0)
    assert page.evaluate("window.__e2eXss") is None
    expect(page.locator('a[href^="javascript:"]')).to_have_count(0)


def test_mobile_drawer_is_modal_keyboard_safe_and_has_no_horizontal_overflow(
    page: Page,
    site_url: str,
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(site_url)
    _wait_ready(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth") is True

    page.locator("#open-filters").click()
    expect(page.locator("#filters")).to_have_attribute("role", "dialog")
    expect(page.locator("#filters")).to_have_attribute("aria-modal", "true")
    expect(page.locator("#filter-overlay")).to_be_visible()
    assert page.evaluate("document.activeElement.id") == "close-filters"
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("document.activeElement.id") == "clear-filters"
    page.keyboard.press("Escape")
    assert page.evaluate("document.activeElement.id") == "open-filters"

    page.locator("#open-filters").click()
    page.mouse.click(380, 400)
    expect(page.locator("#filter-overlay")).to_be_hidden()
    assert page.evaluate("document.activeElement.id") == "open-filters"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth") is True


def test_real_static_resource_and_database_response_contract(page: Page, site_url: str) -> None:
    for resource in ("/", "/sql-wasm.js", "/js/app.js", "/js/controller.js"):
        response = page.request.get(f"{site_url}{resource}")
        assert response.ok, resource
        assert len(response.body()) > 100, resource

    wasm = page.request.get(f"{site_url}/sql-wasm.wasm")
    assert wasm.ok
    assert "application/wasm" in wasm.headers.get("content-type", "")
    assert wasm.body().startswith(b"\x00asm")

    database = page.request.get(f"{site_url}/data/papers.db")
    assert database.ok
    assert "application/octet-stream" in database.headers.get("content-type", "")
    assert database.body().startswith(b"SQLite format 3\x00")
    missing = page.request.get(f"{site_url}/does-not-exist")
    assert missing.status == 404
