from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, expect


def _wait_ready(page: Page) -> None:
    page.wait_for_function(
        "document.querySelector('#article-list')?.getAttribute('aria-busy') === 'false'"
    )


def _query(page: Page) -> dict[str, list[str]]:
    return parse_qs(urlparse(page.url).query)


def test_initial_real_http_load_has_no_browser_errors(
    page: Page,
    site_url: str,
    published_site: Any,
) -> None:
    console_errors: list[str] = []
    page_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    page.on("pageerror", lambda error: page_errors.append(str(error)))

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
    assert console_errors == []
    assert page_errors == []


def test_share_url_restores_search_tag_and_journal_after_refresh(page: Page, site_url: str) -> None:
    page.goto(f"{site_url}/?q=AlScN&tag=baw&journal=apl")
    _wait_ready(page)
    expect(page.locator("#search")).to_have_value("AlScN")
    expect(page.locator("#journal")).to_have_value("apl")
    expect(page.locator('[data-tag="baw"]')).to_be_checked()
    count = int(page.locator("#result-count").inner_text())
    assert count >= 1
    expect(page.locator(".article-card h2")).to_contain_text(["AlScN"] * min(count, 20))
    before = page.url
    page.reload()
    _wait_ready(page)
    assert page.url == before
    expect(page.locator("#result-count")).to_have_text(str(count))
    expect(page.locator('[data-tag="baw"]')).to_be_checked()


def test_desktop_filters_pagination_clear_and_real_history(page: Page, site_url: str) -> None:
    page.goto(site_url)
    _wait_ready(page)
    page.get_by_role("button", name="下一页").click()
    expect(page.locator('[aria-current="page"]')).to_have_text("2")
    assert _query(page)["page"] == ["2"]

    page.locator("#search").fill("Paper")
    page.locator("#journal").select_option("ieee-tu")
    page.locator("#oa-status").select_option("closed")
    page.locator('[data-tag="baw"]').check()
    page.locator("#sort").select_option("oldest")
    page.wait_for_function("document.querySelector('#result-count').textContent !== '32'")
    query = _query(page)
    assert query["q"] == ["Paper"]
    assert query["journal"] == ["ieee-tu"]
    assert query["oa"] == ["closed"]
    assert query["tag"] == ["baw"]
    assert query["sort"] == ["oldest"]
    assert "page" not in query
    assert int(page.locator("#result-count").inner_text()) >= 1

    page.locator("#clear-filters").click()
    expect(page.locator("#result-count")).to_have_text("32")
    assert urlparse(page.url).query == ""
    expect(page.locator("#active-filter-count")).to_have_text("0")

    page.evaluate("history.pushState(null, '', '?q=Paper+01')")
    page.evaluate("history.pushState(null, '', '?q=Paper+02')")
    page.go_back()
    expect(page.locator("#search")).to_have_value("Paper 01")
    expect(page.locator("#result-count")).to_have_text("1")
    page.go_forward()
    expect(page.locator("#search")).to_have_value("Paper 02")
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
