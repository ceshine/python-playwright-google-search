import asyncio
import logging
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from playwright.async_api import Error as PlaywrightError, Page, async_playwright

from .browser_utils import launch_browser, persist_state, prepare_context_page

# --- Logger Setup ---
log_dir = Path.home() / ".playwright-google-search"
log_dir.mkdir(parents=True, exist_ok=True)
log_file_path = log_dir / "google-search.log"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

LOGGER = logging.getLogger(__name__)
try:
    handler = logging.FileHandler(log_file_path)
except OSError as exc:  # pragma: no cover - filesystem restriction safe-guard
    LOGGER.addHandler(logging.NullHandler())
    LOGGER.debug("Failed to attach file handler for search logging: %s", exc)
else:
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


async def detect_recaptcha(page: Page, headless_mode: bool, timeout: int, stage_desc: str):
    if any(pattern in page.url for pattern in SORRY_PATTERNS):
        if headless_mode:
            raise PlaywrightError(f"Human verification page detected after {stage_desc} while in headless mode...")
        LOGGER.warning(
            f"Human verification page detected after {stage_desc}, please complete the verification in the browser...",
        )
        # Wait for the user to complete verification and be redirected back to the search page
        await page.wait_for_url(
            url=lambda url: all(pattern not in url for pattern in SORRY_PATTERNS),
            timeout=timeout * 2,
        )
        LOGGER.info("Human verification complete, continuing search...")


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

    # Detect ReCAPTCHA
    if any(pattern in page.url for pattern in SORRY_PATTERNS):
        await detect_recaptcha(page, headless_mode, timeout, "initial page load")

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
        await detect_recaptcha(page, headless_mode, timeout, "clicking search button")

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
        for _ in range(2):
            browser = await launch_browser(p, headless)
            context = None
            try:
                (
                    context,
                    page,
                    saved_state,
                    state_file_path,
                ) = await prepare_context_page(p, browser, state_file, locale)

                await _navigate_and_search(page, query, timeout, saved_state, headless)
                results = await _extract_results(page, limit)

                await persist_state(context, state_file_path, saved_state, no_save_state)

                return {"query": query, "results": results}

            except PlaywrightError as e:
                if _is_human_verification_error(e):
                    if headless:
                        LOGGER.warning("Human verification detected, restarting in headed mode.")
                        headless = False
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
            browser = await launch_browser(p, headless_mode)
            context = None
            try:
                (
                    context,
                    page,
                    saved_state,
                    state_file_path,
                ) = await prepare_context_page(p, browser, state_file, locale)

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

                await persist_state(context, state_file_path, saved_state, no_save_state)

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
