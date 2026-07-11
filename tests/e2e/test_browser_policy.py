from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Error

from conftest import _launch_browser


class _FailingChromium:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def launch(self, **options: Any) -> None:
        self.calls.append(options)
        channel = options.get("channel", "bundled")
        raise Error(f"cannot launch {channel}")


class _FailingPlaywright:
    def __init__(self) -> None:
        self.chromium = _FailingChromium()


def test_missing_browsers_fail_by_default_with_complete_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PAPER_RADAR_ALLOW_BROWSER_SKIP", raising=False)
    playwright = _FailingPlaywright()

    with pytest.raises(pytest.fail.Exception, match="Microsoft Edge.*Chromium") as caught:
        _launch_browser(playwright)  # type: ignore[arg-type]

    assert "cannot launch msedge" in str(caught.value)
    assert "cannot launch bundled" in str(caught.value)
    assert playwright.chromium.calls == [
        {"headless": True, "channel": "msedge"},
        {"headless": True},
    ]


def test_missing_browsers_skip_only_with_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPER_RADAR_ALLOW_BROWSER_SKIP", "1")
    with pytest.raises(pytest.skip.Exception, match="No Playwright-compatible browser"):
        _launch_browser(_FailingPlaywright())  # type: ignore[arg-type]
