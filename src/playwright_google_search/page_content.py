"""Utilities for retrieving web pages and converting them to Markdown."""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from typing import Literal

from markitdown import MarkItDown, StreamInfo
from playwright.async_api import (
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from .browser_utils import launch_browser, prepare_context_page, persist_state

LOGGER = logging.getLogger(__name__)

TURNSTILE_URL_PATTERNS = [
    "challenges.cloudflare.com",
    "cf-chl",
]
TURNSTILE_SELECTORS = [
    ".cf-turnstile",
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='turnstile']",
    "input[name='cf-turnstile-response']",
]


class TurnstileDetectedError(RuntimeError):
    """Raised when a Cloudflare Turnstile challenge is detected in headless mode."""


async def _page_has_turnstile(page: Page) -> bool:
    if any(pattern in page.url for pattern in TURNSTILE_URL_PATTERNS):
        return True

    for selector in TURNSTILE_SELECTORS:
        if await page.query_selector(selector):
            return True

    content = await page.content()
    return "cf-turnstile" in content or "challenges.cloudflare.com/turnstile" in content


async def _wait_for_turnstile_clear(page: Page, timeout: int) -> None:
    _ = await page.wait_for_function(
        """
        () => {
            const blockedUrl = /challenges\\.cloudflare\\.com|cf-chl/i.test(location.href);
            const hasWidget =
                document.querySelector('.cf-turnstile') ||
                document.querySelector('iframe[src*="challenges.cloudflare.com"]') ||
                document.querySelector('iframe[src*="turnstile"]') ||
                document.querySelector('input[name="cf-turnstile-response"]');
            return !blockedUrl && !hasWidget;
        }
        """,
        timeout=timeout,
    )


async def _handle_turnstile_if_present(page: Page, headless: bool, timeout: int) -> None:
    if not await _page_has_turnstile(page):
        return

    if headless:
        raise TurnstileDetectedError("Cloudflare Turnstile detected while running headless.")

    LOGGER.warning("Cloudflare Turnstile detected. Complete verification in the browser to continue.")
    await _wait_for_turnstile_clear(page, timeout=timeout * 2)
    LOGGER.info("Cloudflare Turnstile verification complete. Continuing.")


async def _render_page_html(
    url: str,
    timeout: int,
    headless: bool = False,
    wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] | None = "networkidle",
    *,
    # Using .placeholder because we do not use nor save a state file by default
    state_file: str = "/tmp/browser-state.json.placeholder",
    locale: str = "en-US",
    # Do not save state by default (saving the state somehow triggers Cloudflare turnstile in some cases)
    no_save_state: bool = True,
) -> str:
    """Render the page at ``url`` in Chromium and return its HTML content."""

    async with async_playwright() as playwright:
        browser = await launch_browser(playwright, headless=headless)

        goto_timed_out = False
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
                goto_timed_out = True
                LOGGER.warning("Timed out while loading page. Attempting to parse the result anyway: %s", url)

            await _handle_turnstile_if_present(page, headless=headless, timeout=timeout)

            await persist_state(context, state_file_path, saved_state, no_save_state)

            return await page.content()
        except PlaywrightTimeoutError:
            raise
        except Exception as exc:
            if goto_timed_out:
                LOGGER.error("Failed to get the page content after timeout: %s", exc)
            raise
        finally:
            await browser.close()


def convert_html_to_markdown(html: str, url: str) -> str:
    """Convert HTML and metadata returned by Playwright into Markdown text."""
    converter = MarkItDown()
    stream = BytesIO(html.encode("utf-8"))
    stream_info = StreamInfo(url=url, extension=".html")
    markdown_doc = converter.convert_stream(stream, stream_info=stream_info)
    return markdown_doc.text_content


async def fetch_page_markdown_async(url: str, timeout: int = 20000, headless: bool = False) -> str:
    """Fetch the page at ``url`` and return its Markdown representation."""

    try:
        html = await _render_page_html(url=url, timeout=timeout, headless=headless, wait_until="networkidle")
    except TurnstileDetectedError as exc:
        if not headless:
            raise RuntimeError(f"Failed to load page: {url}") from exc

        LOGGER.warning(
            "Cloudflare Turnstile detected in headless mode. Retrying in headed mode for manual verification."
        )
        html = await _render_page_html(url=url, timeout=timeout, headless=False, wait_until="networkidle")
    except PlaywrightError as exc:
        raise RuntimeError(f"Failed to load page: {url}") from exc

    return convert_html_to_markdown(html=html, url=url)


def fetch_page_markdown(url: str, timeout: int = 20000, headless: bool = False) -> str:
    """Blocking wrapper that returns the Markdown content of ``url``."""

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(fetch_page_markdown_async(url=url, timeout=timeout, headless=headless))
    finally:
        loop.close()
