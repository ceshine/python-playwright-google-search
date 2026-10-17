"""Tests for launching the browser and creating contexts with Patchright's recommended setup."""

import json
import asyncio
import logging
from typing import Any, cast
from pathlib import Path

import pytest
from patchright.async_api import Browser, Playwright, Error as PlaywrightError

from playwright_google_search.browser_utils import launch_browser, create_browser_context


class FakeChromium:
    """Records launch options and fails for the channels listed in `missing_channels`."""

    def __init__(self, missing_channels: set[str]) -> None:
        self.missing_channels = missing_channels
        self.launches: list[dict[str, Any]] = []

    async def launch(self, **kwargs: Any) -> str:
        self.launches.append(kwargs)
        if kwargs.get("channel") in self.missing_channels:
            raise PlaywrightError(f"Chromium distribution '{kwargs['channel']}' is not found")
        return "browser"


class FakePlaywright:
    def __init__(self, missing_channels: set[str] | None = None) -> None:
        self.chromium = FakeChromium(missing_channels or set())


class FakeBrowser:
    """Records the options passed to `new_context`."""

    def __init__(self) -> None:
        self.context_options: dict[str, Any] = {}

    async def new_context(self, **kwargs: Any) -> str:
        self.context_options = kwargs
        return "context"


def _as_playwright(fake: FakePlaywright) -> Playwright:
    return cast(Playwright, cast(object, fake))


def _as_browser(fake: FakeBrowser) -> Browser:
    return cast(Browser, cast(object, fake))


def test_launch_browser_uses_chrome_channel_without_custom_flags() -> None:
    playwright = FakePlaywright()

    _ = asyncio.run(launch_browser(_as_playwright(playwright), headless=True))

    assert playwright.chromium.launches == [{"headless": True, "channel": "chrome"}]


def test_launch_browser_falls_back_to_bundled_chromium(caplog: pytest.LogCaptureFixture) -> None:
    playwright = FakePlaywright(missing_channels={"chrome"})

    with caplog.at_level(logging.WARNING):
        _ = asyncio.run(launch_browser(_as_playwright(playwright), headless=False))

    assert playwright.chromium.launches == [{"headless": False, "channel": "chrome"}, {"headless": False}]
    assert "Falling back to the bundled Chromium" in caplog.text


def test_launch_browser_without_channel_uses_bundled_chromium() -> None:
    playwright = FakePlaywright()

    _ = asyncio.run(launch_browser(_as_playwright(playwright), headless=False, channel=None))

    assert playwright.chromium.launches == [{"headless": False}]


def test_create_browser_context_keeps_browser_defaults(tmp_path: Path) -> None:
    state_file = tmp_path / "browser-state.json"
    _ = state_file.write_text("{}", encoding="utf-8")
    # Metadata saved by an earlier version, with an emulated fingerprint.
    _ = (tmp_path / "browser-state.fingerprint.json").write_text(
        json.dumps({"fingerprint": {"deviceName": "Desktop Chrome"}, "googleDomain": "https://www.google.com"}),
        encoding="utf-8",
    )
    browser = FakeBrowser()

    _, saved_state = asyncio.run(create_browser_context(_as_browser(browser), state_file, "ja-JP"))

    assert browser.context_options == {"no_viewport": True, "locale": "ja-JP", "storage_state": str(state_file)}
    assert saved_state == {"googleDomain": "https://www.google.com"}


def test_create_browser_context_without_saved_state(tmp_path: Path) -> None:
    browser = FakeBrowser()

    _, saved_state = asyncio.run(create_browser_context(_as_browser(browser), tmp_path / "missing.json", "en-US"))

    assert browser.context_options == {"no_viewport": True, "locale": "en-US", "storage_state": None}
    assert saved_state == {}
