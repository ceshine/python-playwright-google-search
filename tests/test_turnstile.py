"""Tests for Cloudflare Turnstile detection and waiting in page_content."""

import time
import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from patchright.async_api import Page, Error as PlaywrightError, async_playwright

from playwright_google_search import page_content
from playwright_google_search.page_content import (
    TurnstileDetectedError,
    _page_has_turnstile,
    _load_state_after_turnstile,
    _handle_turnstile_if_present,
)

# Trimmed from a real Cloudflare managed challenge page.
CHALLENGE_HTML = """<html><head><title>Just a moment...</title></head><body>
<input type="hidden" name="cf-turnstile-response">
<script>window._cf_chl_opt = {cType: 'managed'};</script>
</body></html>"""
# A normal page with an embedded Turnstile widget, e.g. on a contact form.
WIDGET_HTML = """<html><head><title>Contact</title></head><body>
<h1>Contact us</h1>
<form><div class="cf-turnstile" data-sitekey="1x00000000000000000000AA">
<input type="hidden" name="cf-turnstile-response"></div></form>
</body></html>"""
NORMAL_HTML = "<html><head><title>Hello</title></head><body><p>World</p></body></html>"

# A challenge that clears by itself, followed by a real page that is only complete after its slow image has loaded.
SERVED_PAGES = {
    "/challenge": """<html><head><title>Just a moment...</title></head><body>
<script>window._cf_chl_opt = {}; setTimeout(() => location.replace('/real'), 500);</script>
</body></html>""",
    "/real": """<html><head><title>Real page</title></head><body>
<img src="/slow.png">
<script>window.addEventListener('load', () => document.body.append('Loaded ' + 'content'));</script>
</body></html>""",
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/slow.png":
            time.sleep(1)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        _ = self.wfile.write(SERVED_PAGES.get(self.path, "").encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def server_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join()


def _run_with_page[T](action: Callable[[Page], Awaitable[T]]) -> T:
    async def run() -> T:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                return await action(await browser.new_page())
            finally:
                await browser.close()

    return asyncio.run(run())


@pytest.mark.parametrize(
    ("html", "expected"),
    [(CHALLENGE_HTML, True), (WIDGET_HTML, False), (NORMAL_HTML, False)],
    ids=["challenge", "embedded-widget", "normal"],
)
def test_page_has_turnstile(html: str, expected: bool) -> None:
    async def action(page: Page) -> bool:
        await page.set_content(html)
        return await _page_has_turnstile(page)

    assert _run_with_page(action) is expected


def test_handle_turnstile_raises_in_headless_mode() -> None:
    async def action(page: Page) -> None:
        await page.set_content(CHALLENGE_HTML)
        await _handle_turnstile_if_present(page, headless=True, timeout=1000, wait_until="load")

    with pytest.raises(TurnstileDetectedError):
        _run_with_page(action)


def test_handle_turnstile_waits_for_real_page_to_load(server_url: str) -> None:
    async def action(page: Page) -> tuple[str, str]:
        _ = await page.goto(f"{server_url}/challenge", wait_until="domcontentloaded")
        await _handle_turnstile_if_present(page, headless=False, timeout=10000, wait_until="load")
        return await page.title(), await page.content()

    title, html = _run_with_page(action)

    assert title == "Real page"
    assert "Loaded content" in html


def test_handle_turnstile_logs_timeout_and_returns(caplog: pytest.LogCaptureFixture) -> None:
    async def action(page: Page) -> None:
        await page.set_content(CHALLENGE_HTML)
        await _handle_turnstile_if_present(page, headless=False, timeout=250, wait_until="load")

    with caplog.at_level(logging.WARNING):
        _run_with_page(action)

    assert "Timed out waiting for Cloudflare Turnstile verification" in caplog.text


@pytest.mark.parametrize(
    ("wait_until", "expected"),
    [(None, "load"), ("commit", "load"), ("domcontentloaded", "domcontentloaded"), ("networkidle", "networkidle")],
)
def test_load_state_after_turnstile(wait_until: page_content.WaitUntil | None, expected: str) -> None:
    assert _load_state_after_turnstile(wait_until) == expected


def test_fetch_retries_headed_after_headless_turnstile(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    async def fake_render(url: str, timeout: int, headless: bool = False, **kwargs: object) -> str:
        calls.append(headless)
        if headless:
            raise TurnstileDetectedError("challenge")
        return NORMAL_HTML

    monkeypatch.setattr(page_content, "_render_page_html", fake_render)

    markdown = asyncio.run(page_content.fetch_page_markdown_async("https://example.com", headless=True))

    assert calls == [True, False]
    assert "World" in markdown


def test_fetch_wraps_errors_from_headed_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_render(url: str, timeout: int, headless: bool = False, **kwargs: object) -> str:
        if headless:
            raise TurnstileDetectedError("challenge")
        raise PlaywrightError("net::ERR_CONNECTION_RESET")

    monkeypatch.setattr(page_content, "_render_page_html", fake_render)

    with pytest.raises(RuntimeError, match="Failed to load page: https://example.com"):
        asyncio.run(page_content.fetch_page_markdown_async("https://example.com", headless=True))
