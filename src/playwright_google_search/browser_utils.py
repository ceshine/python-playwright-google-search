"""Shared utilities for launching Patchright browsers and managing contexts/state.

The browser follows Patchright's recommended setup: Google Chrome with its own flags, viewport and user agent, and no
fingerprint injection. Custom flags, a user agent from a device profile and spoofed browser properties are inconsistent
with the real browser, which makes it easier to detect.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from patchright.async_api import Browser, BrowserContext, Page, Playwright, Error as PlaywrightError

LOGGER = logging.getLogger(__name__)

# Metadata saved next to the state file, such as the Google domain chosen for searches.
FINGERPRINT_SUFFIX = ".fingerprint.json"
# Patchright recommends Google Chrome over the bundled Chromium.
DEFAULT_CHANNEL = "chrome"


async def launch_browser(
    playwright: Playwright,
    headless: bool,
    channel: str | None = DEFAULT_CHANNEL,
) -> Browser:
    """Launch the browser with Patchright's default flags.

    Args:
        playwright (Playwright): The Patchright instance.
        headless (bool): Whether to run the browser headless.
        channel (str | None): Browser channel to try first, e.g. `chrome`. `None` uses the bundled Chromium.

    Returns:
        Browser: The launched browser. Falls back to the bundled Chromium if `channel` cannot be launched, e.g. because
            Google Chrome is not installed.
    """
    LOGGER.info("Launching browser in %s mode...", "headless" if headless else "headed")

    if channel is not None:
        try:
            return await playwright.chromium.launch(headless=headless, channel=channel)
        except PlaywrightError as exc:
            LOGGER.warning(
                "Could not launch the %r browser channel. Falling back to the bundled Chromium: %s", channel, exc
            )
    return await playwright.chromium.launch(headless=headless)


async def create_browser_context(
    browser: Browser,
    state_file: Path,
    locale: str,
) -> tuple[BrowserContext, dict[str, Any]]:
    """Create a browser context with the browser's own viewport and user agent.

    Args:
        browser (Browser): The browser to create the context in.
        state_file (Path): Storage state (cookies, local storage) to restore, if the file exists.
        locale (str): Browser locale, e.g. `en-US`.

    Returns:
        tuple[BrowserContext, dict[str, Any]]: The context, and the metadata saved next to the state file.
    """
    saved_state: dict[str, Any] = {}
    fingerprint_file = state_file.with_suffix(FINGERPRINT_SUFFIX)
    if fingerprint_file.exists():
        LOGGER.info("Loading saved metadata from %s", fingerprint_file)
        with open(fingerprint_file, "r", encoding="utf-8") as file:
            saved_state = json.load(file)
            assert isinstance(saved_state, dict)
    # Emulated fingerprints are no longer used. Drop the ones saved by earlier versions.
    _ = saved_state.pop("fingerprint", None)

    context = await browser.new_context(
        no_viewport=True,
        locale=locale,
        storage_state=str(state_file) if state_file.exists() else None,
    )
    return context, saved_state


async def prepare_context_page(
    browser: Browser,
    state_file: str,
    locale: str,
) -> tuple[BrowserContext, Page, dict[str, Any], Path]:
    """Construct a fresh page along with the loaded browser context and metadata."""
    state_file_path = Path(state_file)
    context, saved_state = await create_browser_context(browser, state_file_path, locale)
    page = await context.new_page()
    return context, page, saved_state, state_file_path


async def persist_state(
    context: BrowserContext,
    state_file_path: Path,
    saved_state: dict[str, Any],
) -> None:
    """Persist storage state and the metadata saved next to it."""

    _ = await context.storage_state(path=str(state_file_path))
    fingerprint_file = state_file_path.with_suffix(FINGERPRINT_SUFFIX)
    with open(fingerprint_file, "w", encoding="utf-8") as file:
        json.dump(saved_state, file, indent=2)


__all__ = [
    "DEFAULT_CHANNEL",
    "create_browser_context",
    "launch_browser",
    "persist_state",
    "prepare_context_page",
]
