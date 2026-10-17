"""Utilities for retrieving web pages and converting them to Markdown."""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from typing import Literal

from markitdown import MarkItDown, StreamInfo
from patchright.async_api import (
    Page,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from .browser_utils import persist_state, launch_browser, prepare_context_page

LOGGER = logging.getLogger(__name__)

WaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]
LoadState = Literal["domcontentloaded", "load", "networkidle"]

# Every Cloudflare challenge page (managed, JS and interactive) defines `window._cf_chl_opt` in an inline script.
# Normal pages that merely embed a Turnstile widget do not, so they are not treated as blocked.
# Patchright evaluates scripts in an isolated world where page variables are not visible, so match the script text.
IS_CHALLENGE_JS = "() => [...document.scripts].some((script) => script.text.includes('_cf_chl_opt'))"


class TurnstileDetectedError(RuntimeError):
    """Raised when a Cloudflare Turnstile challenge is detected in headless mode."""


async def _page_has_turnstile(page: Page) -> bool:
    """Check whether the page is a Cloudflare challenge page.

    Args:
        page (Page): The page to inspect.

    Returns:
        bool: True if the page is a Cloudflare challenge page.
    """
    return bool(await page.evaluate(IS_CHALLENGE_JS))


async def _wait_for_turnstile_clear(page: Page, timeout: int) -> None:
    """Wait until the Cloudflare challenge page has been replaced by another page.

    Args:
        page (Page): The page showing the challenge.
        timeout (int): Maximum time to wait, in milliseconds.

    Raises:
        PlaywrightTimeoutError: If the challenge page is still showing after `timeout`.
    """
    _ = await page.wait_for_function(f"() => !({IS_CHALLENGE_JS})()", timeout=timeout)


def _load_state_after_turnstile(wait_until: WaitUntil | None) -> LoadState:
    """Pick the load state to wait for once the real page replaces the challenge.

    Args:
        wait_until (WaitUntil | None): The `wait_until` value used for the initial navigation.

    Returns:
        LoadState: The same state as `wait_until`, or `load` for `commit` and `None`, which
            `Page.wait_for_load_state` does not accept.
    """
    if wait_until is None or wait_until == "commit":
        return "load"
    return wait_until


async def _handle_turnstile_if_present(
    page: Page,
    headless: bool,
    timeout: int,
    wait_until: WaitUntil | None,
) -> None:
    """Wait for the user to pass a Cloudflare challenge in the browser window, if the page shows one.

    A timeout is logged rather than raised, so that the caller can still parse the page and verify the result.

    Args:
        page (Page): The page to inspect.
        headless (bool): Whether the browser is headless. A challenge cannot be passed in headless mode.
        timeout (int): The navigation timeout, in milliseconds. The user gets twice this time to pass the challenge.
        wait_until (WaitUntil | None): The `wait_until` value used for the initial navigation. The real page is
            awaited to the same state.

    Raises:
        TurnstileDetectedError: If the page shows a challenge while running headless.
    """
    if not await _page_has_turnstile(page):
        return

    if headless:
        raise TurnstileDetectedError("Cloudflare Turnstile detected while running headless.")

    try:
        LOGGER.warning("Cloudflare Turnstile detected. Complete verification in the browser to continue.")
        await _wait_for_turnstile_clear(page, timeout=timeout * 2)
        # The wait ends as soon as the real page replaces the challenge, before the real page has loaded.
        await page.wait_for_load_state(_load_state_after_turnstile(wait_until), timeout=timeout)
        LOGGER.info("Cloudflare Turnstile verification complete. Continuing.")
    except PlaywrightTimeoutError:
        LOGGER.warning(
            "Timed out waiting for Cloudflare Turnstile verification. Attempting to parse the result anyway: %s",
            page.url,
        )


async def _render_page_html(
    url: str,
    timeout: int,
    headless: bool = False,
    wait_until: WaitUntil | None = "networkidle",
    *,
    # Using .placeholder because we do not use nor save a state file by default
    state_file: str = "/tmp/browser-state.json.placeholder",
    locale: str = "en-US",
    # Do not save state by default (saving the state somehow triggers Cloudflare turnstile in some cases)
    no_save_state: bool = True,
    wait_seconds: float = 0,
) -> str:
    """Render the page at ``url`` in Chromium and return its HTML content."""

    async with async_playwright() as playwright:
        browser = await launch_browser(playwright, headless=headless)

        try:
            (
                context,
                page,
                saved_state,
                state_file_path,
            ) = await prepare_context_page(playwright, browser, state_file, locale)

            try:
                _ = await page.goto(url, wait_until=wait_until, timeout=timeout)
            except PlaywrightTimeoutError:
                LOGGER.warning("Timed out while loading page. Attempting to parse the result anyway: %s", url)

            await _handle_turnstile_if_present(page, headless=headless, timeout=timeout, wait_until=wait_until)

            if no_save_state is False:
                await persist_state(context, state_file_path, saved_state)

            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)

            return await page.content()
        finally:
            await browser.close()


async def _render_page_html_with_headed_fallback(url: str, timeout: int, headless: bool, wait_seconds: float) -> str:
    """Render the page, retrying in headed mode if a headless run hits a Cloudflare challenge.

    Args:
        url (str): The URL to open.
        timeout (int): The navigation timeout, in milliseconds.
        headless (bool): Whether to try headless mode first.
        wait_seconds (float): Extra time to wait before reading the page, in seconds.

    Returns:
        str: The HTML of the page.
    """
    try:
        return await _render_page_html(
            url=url, timeout=timeout, headless=headless, wait_until="networkidle", wait_seconds=wait_seconds
        )
    except TurnstileDetectedError:
        LOGGER.warning(
            "Cloudflare Turnstile detected in headless mode. Retrying in headed mode for manual verification."
        )
        return await _render_page_html(
            url=url, timeout=timeout, headless=False, wait_until="networkidle", wait_seconds=wait_seconds
        )


def convert_html_to_markdown(html: str, url: str) -> str:
    """Convert HTML and metadata returned by Playwright into Markdown text."""
    converter = MarkItDown()
    stream = BytesIO(html.encode("utf-8"))
    stream_info = StreamInfo(url=url, extension=".html")
    markdown_doc = converter.convert_stream(stream, stream_info=stream_info)
    return markdown_doc.text_content


async def fetch_page_markdown_async(
    url: str, timeout: int = 20000, headless: bool = False, wait_seconds: float = 0
) -> str:
    """Fetch the page at ``url`` and return its Markdown representation.

    ``wait_seconds`` defaults to ``0`` so that programmatic callers are not
    penalised by an implicit sleep. Use the blocking ``fetch_page_markdown``
    wrapper (or the CLI) when you want a conservative default wait time.
    """

    try:
        html = await _render_page_html_with_headed_fallback(
            url=url, timeout=timeout, headless=headless, wait_seconds=wait_seconds
        )
    except PlaywrightError as exc:
        LOGGER.error(exc)
        raise RuntimeError(f"Failed to load page: {url}") from exc

    return convert_html_to_markdown(html=html, url=url)


def fetch_page_markdown(url: str, timeout: int = 20000, headless: bool = False, wait_seconds: float = 5) -> str:
    """Blocking wrapper that returns the Markdown content of ``url``.

    Defaults ``wait_seconds`` to ``5`` so that interactive CLI users and
    quick scripts get a safer out-of-the-box experience for pages with
    late-rendered content. The underlying async function defaults to ``0``
    to avoid surprising library consumers with an implicit sleep.
    """

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            fetch_page_markdown_async(url=url, timeout=timeout, headless=headless, wait_seconds=wait_seconds)
        )
    finally:
        loop.close()
