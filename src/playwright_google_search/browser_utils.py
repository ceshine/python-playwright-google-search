"""Shared utilities for launching Playwright browsers and managing contexts/state."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright

LOGGER = logging.getLogger(__name__)

CHROMIUM_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-site-isolation-trials",
    "--disable-web-security",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-accelerated-2d-canvas",
    "--no-first-run",
    "--no-zygote",
    "--disable-gpu",
    "--hide-scrollbars",
    "--mute-audio",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-breakpad",
    "--disable-component-extensions-with-background-pages",
    "--disable-extensions",
    "--disable-features=TranslateUI",
    "--disable-ipc-flooding-protection",
    "--disable-renderer-backgrounding",
    "--enable-features=NetworkService,NetworkServiceInProcess",
    "--force-color-profile=srgb",
    "--metrics-recording-only",
]


async def launch_browser(
    playwright: Playwright,
    headless: bool,
) -> Browser:
    """Launch Chromium with the shared configuration."""
    LOGGER.info("Launching browser in %s mode...", "headless" if headless else "headed")

    return await playwright.chromium.launch(
        headless=headless,
        args=CHROMIUM_LAUNCH_ARGS,
        ignore_default_args=["--enable-automation"],
    )


async def create_browser_context(
    playwright: Playwright,
    browser: Browser,
    state_file: Path,
    locale: str,
) -> tuple[BrowserContext, dict[str, Any]]:
    """Create a browser context with fingerprinting metadata restored when available."""
    storage_state_path_str = str(state_file) if state_file.exists() else None

    saved_state: dict[str, Any] = {}
    fingerprint_file = state_file.with_suffix(".json-fingerprint.json")
    if fingerprint_file.exists():
        with open(fingerprint_file, "r", encoding="utf-8") as file:
            saved_state = json.load(file)
            assert isinstance(saved_state, dict)

    device_name = saved_state.get("fingerprint", {}).get("deviceName")
    if not device_name or device_name not in playwright.devices:
        device_name = "Desktop Chrome"

    device_config = playwright.devices[device_name]
    context_options = {**device_config}

    if "fingerprint" in saved_state:
        context_options.update(
            {
                "locale": saved_state["fingerprint"]["locale"],
                "timezone_id": saved_state["fingerprint"]["timezoneId"],
                "color_scheme": saved_state["fingerprint"]["colorScheme"],
            }
        )
    else:
        now = datetime.now()
        host_config = {
            "deviceName": device_name,
            "locale": locale,
            "timezoneId": "America/New_York",
            "colorScheme": "dark" if 19 <= now.hour or now.hour < 7 else "light",
            "reducedMotion": "no-preference",
            "forcedColors": "none",
        }
        context_options.update(
            {
                "locale": host_config["locale"],
                "timezone_id": host_config["timezoneId"],
                "color_scheme": host_config["colorScheme"],
            }
        )
        saved_state["fingerprint"] = host_config

    context_options.update(
        {
            "viewport": {"width": 1920, "height": 1080},
            "permissions": ["geolocation", "notifications"],
            "accept_downloads": True,
            "is_mobile": False,
            "has_touch": False,
            "java_script_enabled": True,
        }
    )

    if storage_state_path_str:
        context_options["storage_state"] = storage_state_path_str

    context = await browser.new_context(**context_options)

    await context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
        if (typeof WebGLRenderingContext !== 'undefined') {
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) { return 'Intel Inc.'; }
                if (parameter === 37446) { return 'Intel Iris OpenGL Engine'; }
                return getParameter.call(this, parameter);
            };
        }
        """
    )
    return context, saved_state


async def prepare_context_page(
    playwright: Playwright,
    browser: Browser,
    state_file: str,
    locale: str,
) -> tuple[BrowserContext, Page, dict[str, Any], Path]:
    """Construct a fresh page along with the loaded browser context and metadata."""
    state_file_path = Path(state_file)
    context, saved_state = await create_browser_context(playwright, browser, state_file_path, locale)
    page = await context.new_page()
    return context, page, saved_state, state_file_path


async def persist_state(
    context: BrowserContext,
    state_file_path: Path,
    saved_state: dict[str, Any],
    no_save_state: bool,
) -> None:
    """Persist storage state and fingerprint metadata unless saving is disabled."""
    if no_save_state:
        return

    _ = await context.storage_state(path=str(state_file_path))
    fingerprint_file = state_file_path.with_suffix(".json-fingerprint.json")
    with open(fingerprint_file, "w", encoding="utf-8") as file:
        json.dump(saved_state, file, indent=2)


__all__ = [
    "CHROMIUM_LAUNCH_ARGS",
    "create_browser_context",
    "launch_browser",
    "persist_state",
    "prepare_context_page",
]
