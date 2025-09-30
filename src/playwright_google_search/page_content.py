"""Utilities for retrieving web pages and converting them to Markdown."""

from __future__ import annotations

import asyncio
from io import BytesIO

from markitdown import MarkItDown, StreamInfo
from playwright.async_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from .browser_utils import launch_browser


async def _render_page_html(url: str, timeout: int, headless: bool = False) -> str:
    """Render the page at ``url`` in Chromium and return its HTML content."""

    async with async_playwright() as playwright:
        browser = await launch_browser(playwright, headless=headless)
        try:
            page = await browser.new_page()
            _ = await page.goto(url, wait_until="networkidle", timeout=timeout)
            return await page.content()
        finally:
            await browser.close()


def convert_html_to_markdown(html: str, url: str) -> str:
    """Convert HTML and metadata returned by Playwright into Markdown text."""
    converter = MarkItDown()
    stream = BytesIO(html.encode("utf-8"))
    stream_info = StreamInfo(url=url, extension=".html")
    markdown_doc = converter.convert_stream(stream, stream_info=stream_info)
    return markdown_doc.text_content


async def fetch_page_markdown_async(url: str, timeout: int = 60000, headless: bool = False) -> str:
    """Fetch the page at ``url`` and return its Markdown representation."""

    try:
        html = await _render_page_html(url=url, timeout=timeout, headless=headless)
    except PlaywrightTimeoutError as exc:  # pragma: no cover - runtime safeguard
        raise RuntimeError(f"Timed out while loading page: {url}") from exc
    except PlaywrightError as exc:  # pragma: no cover - runtime safeguard
        raise RuntimeError(f"Failed to load page: {url}") from exc

    return convert_html_to_markdown(html=html, url=url)


def fetch_page_markdown(url: str, timeout: int = 60000, headless: bool = False) -> str:
    """Blocking wrapper that returns the Markdown content of ``url``."""

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(fetch_page_markdown_async(url=url, timeout=timeout, headless=headless))
    finally:
        loop.close()
