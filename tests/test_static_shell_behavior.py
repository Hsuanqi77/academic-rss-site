from __future__ import annotations

import functools
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Error, Page, Playwright, sync_playwright


DOCS = Path(__file__).resolve().parents[1] / "docs"


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


@pytest.fixture(scope="session")
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


@pytest.fixture
def mobile_page(shell_browser: Browser, static_site_url: str) -> Iterator[Page]:
    page = shell_browser.new_page(viewport={"width": 500, "height": 800})
    page.goto(static_site_url)
    page.wait_for_function("document.querySelector('#status')?.textContent.includes('界面已准备')")
    try:
        yield page
    finally:
        page.close()


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
          const about = document.querySelector('#about');
          about.inert = true;
          about.setAttribute('aria-hidden', 'false');
        }"""
    )

    mobile_page.locator("#open-filters").click()
    mobile_page.keyboard.press("Escape")
    assert mobile_page.evaluate("document.activeElement.id") == "open-filters"
    assert mobile_page.locator("#about").get_attribute("aria-hidden") == "false"
    assert mobile_page.locator("#about").evaluate("element => element.inert") is True

    mobile_page.locator("#open-filters").click()
    mobile_page.mouse.click(480, 400)
    assert mobile_page.evaluate("document.activeElement.id") == "open-filters"
    assert mobile_page.locator("#filters").get_attribute("role") is None
    assert mobile_page.locator("#filters").get_attribute("aria-modal") is None
    assert mobile_page.locator("#about").get_attribute("aria-hidden") == "false"
    assert mobile_page.locator("#about").evaluate("element => element.inert") is True


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
