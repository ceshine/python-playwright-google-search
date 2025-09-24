import re
import json
import random
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Error as PlaywrightError,
    async_playwright,
)

# --- Logger Setup ---
log_dir = Path.home() / ".playwright-google-search"
log_dir.mkdir(parents=True, exist_ok=True)
log_file_path = log_dir / "google-search.log"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

LOGGER = logging.getLogger(__name__)
handler = logging.FileHandler(log_file_path)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
LOGGER.addHandler(handler)


# --- Constants ---
DEFAULT_TIMEOUT = 60000
SORRY_PATTERNS = [
    "google.com/sorry/index",
    "google.com/sorry",
    "recaptcha",
    "captcha",
    "unusual traffic",
]
GOOGLE_DOMAINS = [
    "https://www.google.com",
    "https://www.google.co.uk",
    "https://www.google.ca",
    "https://www.google.com.au",
]
SEARCH_RESULT_SELECTORS = [
    "#search",
    "#rso",
    ".g",
    "[data-sokoban-container]",
    "div[role='main']",
]
SEARCH_INPUT_SELECTORS = [
    "textarea[name='q']",
    "input[name='q']",
    "textarea[title='Search']",
    "input[title='Search']",
    "textarea[aria-label='Search']",
    "input[aria-label='Search']",
    "textarea",
]
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


# --- Helper Functions ---
async def _create_browser_context(
    p: Playwright,
    browser: Browser,
    state_file: Path,
    locale: str,
) -> tuple[BrowserContext, dict[str, Any]]:
    """Create and configure a Playwright BrowserContext with persistent fingerprinting/state.

    This helper initializes a BrowserContext tailored for desktop-like browsing and attempts to restore or
    synthesize a lightweight "fingerprint" to reduce detection by sites (for example Google).

    The function will read a storage state file (if present) and a companion fingerprint JSON file to restore
    prior settings such as device name, locale, timezone and color scheme.

    When no fingerprint exists, a host fingerprint is synthesized and returned
    as part of the saved_state.

    Args:
        p (Playwright): The active Playwright instance (from async_playwright()).
        browser (Browser): A launched Playwright Browser instance (typically chromium).
        state_file (Path): Path to a JSON file that may contain a Playwright
            storage state. If present, its path will be passed to the context
            as storage_state, and a companion fingerprint file
            (state_file.with_suffix(".json-fingerprint.json")) will be read.
        locale (str): Preferred locale (e.g. "en-US") to use when no saved
            fingerprint locale is available.

    Returns:
        tuple[BrowserContext, dict[str, Any]]:
            A tuple containing the newly created BrowserContext and a dictionary representing the saved fingerprint/state metadata.
            The saved_state will always include a "fingerprint" key after the call (either loaded from disk or synthesized).

    Raises:
        Any exception raised while reading files or creating the context (e.g. file I/O errors, Playwright errors) will propagate to the caller.

    Notes:
        - The created context is configured with a desktop viewport, common permissions (geolocation, notifications), and options intended to
          mimic a regular browser session.
        - An init script is injected to modify common navigator and WebGL properties to help mask automation artifacts (navigator.webdriver,
          plugins, languages, chrome runtime, WebGL parameters).
        - The function does not close the provided browser; the caller is responsible for closing the context and browser when finished.

    Example:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context, saved_state = await _create_browser_context(
                p, browser, Path("browser-state.json"), "en-US"
            )
    """
    storage_state_path_str = str(state_file) if state_file.exists() else None

    # Load the fingerprint if exists
    saved_state = {}
    fingerprint_file = state_file.with_suffix(".json-fingerprint.json")
    if fingerprint_file.exists():
        with open(fingerprint_file, "r") as f:
            saved_state = json.load(f)
            assert isinstance(saved_state, dict)

    # We always use Chromium for now
    # device_list = ["Desktop Chrome", "Desktop Edge", "Desktop Firefox", "Desktop Safari"]
    device_name = saved_state.get("fingerprint", {}).get("deviceName")
    if not device_name or device_name not in p.devices:
        device_name = "Desktop Chrome"  # We always use Chromium for now

    device_config = p.devices[device_name]
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
        host_config = {
            "deviceName": device_name,
            "locale": locale,
            "timezoneId": "America/New_York",
            "colorScheme": "dark" if datetime.now().hour >= 19 or datetime.now().hour < 7 else "light",
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


async def _navigate_and_search(
    page: Page, query: str, timeout: int, saved_state: dict[str, Any], headless_mode: bool
) -> None:
    """Navigate a Playwright Page to Google and perform a search for a query.

    This helper navigates the provided Playwright Page to a selected Google domain (either restored from saved_state or chosen randomly),
    finds the search input, types the query, and submits the search. It performs several checks to detect human verification (captcha) pages
    and validates that search results are present after the search completes.

    The function may update the provided saved_state in-place by setting a "googleDomain" key when a domain is chosen.
    It intentionally does not close or modify the provided Page object beyond performing navigation and input actions.

    Args:
        page (Page): The Playwright Page instance to use for navigation and interaction.
        query (str): The search query string to enter into Google's search input.
        timeout (int): Timeout in milliseconds for navigation and waiting operations.
        saved_state (dict[str, Any]): Mutable dictionary representing persistent state/fingerprint
            metadata. If a googleDomain is not present it will be set to a chosen domain.
        headless_mode (bool): Indicates whether the search is being performed in headless mode.

    Raises:
        playwright.async_api.Error: Raised when a human verification/captcha page is detected,
            when the search input cannot be located, or when search results cannot be found.
            Any underlying Playwright navigation or interaction errors will also propagate
            as Error.

    Returns:
        None

    Notes:
        - The function uses a small set of selectors to locate the search input and result
            containers; changes in Google's DOM may require selector updates.
        - The function types the query with a small randomized delay between keystrokes and
            waits for network idle when navigating to reduce detection.
    """

    # Decide the Google domain to use
    selected_domain = saved_state.get("googleDomain")
    if not selected_domain:
        selected_domain = random.choice(GOOGLE_DOMAINS)
        saved_state["googleDomain"] = selected_domain

    LOGGER.info(f"Navigating to {selected_domain}")
    _ = await page.goto(selected_domain, timeout=timeout, wait_until="networkidle")
    LOGGER.info(f"Navigated to {page.url}")

    if any(pattern in page.url for pattern in SORRY_PATTERNS):
        LOGGER.warning("Human verification page detected on initial navigation.")
        raise PlaywrightError("Human verification page detected.")

    # Locate the search box
    search_input = None
    for selector in SEARCH_INPUT_SELECTORS:
        search_input = await page.query_selector(selector)
        if search_input:
            break

    if not search_input:
        raise PlaywrightError("Could not find search box.")

    # Type in the query
    await search_input.click()
    await page.keyboard.type(query, delay=random.randint(10, 30))
    await asyncio.sleep(random.randint(100, 300) / 1000)
    async with page.expect_navigation(wait_until="networkidle", timeout=timeout):
        await page.keyboard.press("Enter")

    # Detect ReCAPTCHA
    if any(pattern in page.url for pattern in SORRY_PATTERNS):
        if headless_mode:
            raise PlaywrightError("Human verification page detected after search while in headless mode...")
        LOGGER.warning(
            "Human verification page detected after search, please complete the verification in the browser...",
        )
        # Wait for the user to complete verification and be redirected back to the search page
        await page.wait_for_url(
            url=lambda url: all(pattern not in url for pattern in SORRY_PATTERNS),
            timeout=timeout * 2,
        )
        LOGGER.info("Human verification complete, continuing search...")

    # Detect search result
    results_found = False
    for selector in SEARCH_RESULT_SELECTORS:
        if await page.query_selector(selector):
            results_found = True
            break

    if not results_found:
        raise PlaywrightError("Could not find search results element.")

    # Search result is available on the current page now


async def _extract_results(page: Page, limit: int) -> list[dict[str, str]]:
    """Extract structured search result entries from a Google search results Page.

    This asynchronous helper inspects the provided Playwright Page and attempts to
    locate individual search result containers using a prioritized list of common
    Google result selectors. For each container it extracts a title, a link (URL),
    and an optional snippet/description. The function deduplicates results by URL
    and stops once `limit` results have been collected.

    Args:
        page (Page): Playwright Page object representing a loaded Google search results page.
        limit (int): Maximum number of result dictionaries to return.

    Returns:
        list[dict[str, str]]: A list of result dictionaries, each containing:
            - "title": The visible title text for the result.
            - "link" : The absolute URL string for the result (typically starting with "http").
            - "snippet": A short descriptive snippet if available, otherwise an empty string.

    Raises:
        playwright.async_api.Error: Any Playwright errors raised while querying the DOM or
            retrieving element attributes will propagate to the caller.

    Notes:
        - The function iterates through several selector patterns to maximize compatibility
            with different Google DOM shapes and ranks results by the order of selector_sets.
        - Results without a title, without a valid http/https link, or duplicate URLs are skipped.
        - This function is I/O bound and must be awaited (it performs many Playwright element calls).

    Example:
        results = await _extract_results(page, 10)
    """
    selector_sets = [
        {"container": "#search div[data-hveid]", "title": "h3", "snippet": ".VwiC3b"},
        {"container": "#rso div[data-hveid]", "title": "h3", "snippet": "[data-sncf='1']"},
        {"container": ".g", "title": "h3", "snippet": "div[style*='webkit-line-clamp']"},
        {"container": "div[jscontroller][data-hveid]", "title": "h3", "snippet": "div[role='text']"},
    ]

    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for selectors in selector_sets:
        if len(results) >= limit:
            break

        containers = await page.query_selector_all(selectors["container"])
        for container in containers:
            if len(results) >= limit:
                break

            title_el = await container.query_selector(selectors["title"])
            if not title_el:
                continue

            title = (await title_el.inner_text()).strip()
            if not title:
                continue

            # Find the closest ancestor <a> and get its href
            link = await title_el.evaluate("el => { const a = el.closest('a'); return a ? a.href : '' }")

            if not link or not link.startswith("http") or link in seen_urls:
                continue

            snippet_el = await container.query_selector(selectors["snippet"])
            snippet = (await snippet_el.inner_text()).strip() if snippet_el else ""

            results.append({"title": title, "link": link, "snippet": snippet})
            seen_urls.add(link)

    return results[:limit]


# --- Shared Utilities ---


async def _launch_browser(p: Playwright, headless_mode: bool) -> Browser:
    LOGGER.info("Launching browser in %s mode...", "headless" if headless_mode else "headed")
    return await p.chromium.launch(
        headless=headless_mode,
        args=CHROMIUM_LAUNCH_ARGS,
        ignore_default_args=["--enable-automation"],
    )


async def _prepare_context_page(
    p: Playwright,
    browser: Browser,
    state_file: str,
    locale: str,
) -> tuple[BrowserContext, Page, dict[str, Any], Path]:
    state_file_path = Path(state_file)
    context, saved_state = await _create_browser_context(p, browser, state_file_path, locale)
    page = await context.new_page()
    return context, page, saved_state, state_file_path


async def _persist_state_if_needed(
    context: BrowserContext,
    state_file_path: Path,
    saved_state: dict[str, Any],
    no_save_state: bool,
) -> None:
    if no_save_state:
        return
    _ = await context.storage_state(path=str(state_file_path))
    fingerprint_file = state_file_path.with_suffix(".json-fingerprint.json")
    with open(fingerprint_file, "w") as f:
        json.dump(saved_state, f, indent=2)


def _is_human_verification_error(e: Exception) -> bool:
    return "Human verification" in str(e)


# --- Main Functions ---
async def google_search(
    query: str,
    limit: int = 10,
    timeout: int = DEFAULT_TIMEOUT,
    state_file: str = "./browser-state.json",
    no_save_state: bool = False,
    locale: str = "en-US",
    headless: bool = True,
) -> dict[str, Any]:
    async with async_playwright() as p:
        headless_mode = headless
        for _ in range(2):
            browser = await _launch_browser(p, headless_mode)
            context = None
            try:
                context, page, saved_state, state_file_path = await _prepare_context_page(
                    p, browser, state_file, locale
                )

                await _navigate_and_search(page, query, timeout, saved_state, headless_mode)
                results = await _extract_results(page, limit)

                await _persist_state_if_needed(context, state_file_path, saved_state, no_save_state)

                return {"query": query, "results": results}

            except PlaywrightError as e:
                if _is_human_verification_error(e):
                    if headless_mode:
                        LOGGER.warning("Human verification detected, restarting in headed mode.")
                        headless_mode = False
                        # retry on next loop iteration
                    else:
                        break
                else:
                    LOGGER.error(f"An error occurred during search: {e}")
                    return {"query": query, "results": [], "error": str(e)}
            finally:
                if context:
                    LOGGER.info("Closing the context...")
                    await context.close()
                if browser:
                    LOGGER.info("Closing the browser...")
                    await browser.close()
        return {"query": query, "results": [], "error": "Human verification detected; retry in headed mode exhausted."}


async def get_google_search_page_html(
    query: str,
    options: dict[str, Any],
    save_to_file: bool = False,
    output_path: str | None = None,
) -> dict[str, Any]:
    timeout = options.get("timeout", DEFAULT_TIMEOUT)
    state_file = options.get("state_file", "./browser-state.json")
    no_save_state = options.get("no_save_state", False)
    locale = options.get("locale", "en-US")
    headless = not options.get("no_headless", False)

    async with async_playwright() as p:
        headless_mode = headless
        for _ in range(2):
            browser = await _launch_browser(p, headless_mode)
            context = None
            try:
                context, page, saved_state, state_file_path = await _prepare_context_page(
                    p, browser, state_file, locale
                )

                await _navigate_and_search(page, query, timeout, saved_state, headless_mode)

                full_html = await page.content()
                soup = BeautifulSoup(full_html, "html.parser")
                for tag in soup(["script", "style"]):
                    tag.decompose()
                html = str(soup)

                result = {
                    "query": query,
                    "html": html,
                    "url": page.url,
                    "originalHtmlLength": len(full_html),
                }

                if save_to_file:
                    if not output_path:
                        output_dir = Path("./google-search-html")
                        output_dir.mkdir(exist_ok=True)
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        sanitized_query = re.sub(r"[^a-zA-Z0-9]", "_", query)[:50]
                        output_path = str(output_dir / f"{sanitized_query}-{timestamp}.html")

                    with open(output_path, "w", encoding="utf-8") as f:
                        _ = f.write(html)
                    result["savedPath"] = output_path

                    screenshot_path = Path(output_path).with_suffix(".png")
                    _ = await page.screenshot(path=str(screenshot_path), full_page=True)
                    result["screenshotPath"] = str(screenshot_path)

                await _persist_state_if_needed(context, state_file_path, saved_state, no_save_state)

                return result

            except PlaywrightError as e:
                if _is_human_verification_error(e) and headless_mode:
                    LOGGER.warning("Human verification detected, restarting in headed mode.")
                    headless_mode = False
                    # retry on next loop iteration
                else:
                    LOGGER.error(f"An error occurred while getting HTML: {e}")
                    return {"query": query, "html": "", "url": "", "error": str(e)}
            finally:
                if context:
                    LOGGER.info("Closing the context...")
                    await context.close()
                if browser:
                    LOGGER.info("Closing the browser...")
                    await browser.close()

        return {
            "query": query,
            "html": "",
            "url": "",
            "error": "Human verification detected; retry in headed mode exhausted.",
        }
