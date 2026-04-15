import re
import random
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup
from patchright.async_api import Page, Error as PlaywrightError, async_playwright

from .browser_utils import persist_state, launch_browser, prepare_context_page

# --- Logger Setup ---
log_dir = Path.home() / ".playwright-google-search"
log_dir.mkdir(parents=True, exist_ok=True)
log_file_path = log_dir / "google-search.log"

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

    LOGGER.info("Navigating to %s", selected_domain)
    _ = await page.goto(selected_domain, timeout=timeout, wait_until="domcontentloaded")
    LOGGER.info("Navigated to %s", page.url)

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
    async with page.expect_navigation(wait_until="domcontentloaded", timeout=timeout):
        await page.keyboard.press("Enter")

    # Detect ReCAPTCHA
    if any(pattern in page.url for pattern in SORRY_PATTERNS):
        await detect_recaptcha(page, headless_mode, timeout, "clicking search button")

    # Wait for any of the known results containers to appear, rather than gating on
    # network idle (Google keeps long-lived connections open that never settle).
    try:
        _ = await page.wait_for_selector(", ".join(SEARCH_RESULT_SELECTORS), timeout=timeout)
    except PlaywrightError as exc:
        raise PlaywrightError("Could not find search results element.") from exc

    # Search result is available on the current page now


NEXT_PAGE_SELECTOR = "a#pnnext"


async def _go_to_next_page(page: Page, timeout: int, headless_mode: bool) -> bool:
    """Click the Google "Next" link to flip to the next results page.

    Uses the literal anchor rather than synthesizing a ``&start=N`` URL so that
    Google's own continuation tokens (``sa``, ``sstk``, ``ved``) travel with the
    request — this looks more like a real user click to Google's bot heuristics.

    Args:
        page: Playwright Page currently sitting on a results page.
        timeout: Milliseconds to wait for navigation and the results selector.
        headless_mode: Forwarded to ``detect_recaptcha`` for verification handling.

    Returns:
        True if a next page loaded and its results container appeared. False if
        there is no next-page anchor (i.e. we're on the last page).
    """
    next_link = await page.query_selector(NEXT_PAGE_SELECTOR)
    if not next_link:
        LOGGER.info("No next-page link found; stopping pagination.")
        return False

    LOGGER.info("Flipping to the next results page.")
    async with page.expect_navigation(wait_until="domcontentloaded", timeout=timeout):
        await next_link.click()

    if any(pattern in page.url for pattern in SORRY_PATTERNS):
        await detect_recaptcha(page, headless_mode, timeout, "flipping to next page")

    try:
        _ = await page.wait_for_selector(", ".join(SEARCH_RESULT_SELECTORS), timeout=timeout)
    except PlaywrightError as exc:
        raise PlaywrightError("Could not find search results element after page flip.") from exc
    return True


async def _extract_results(page: Page, limit: int, seen_urls: set[str] | None = None) -> list[dict[str, str]]:
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
    if seen_urls is None:
        seen_urls = set()

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

                seen_urls: set[str] = set()
                results: list[dict[str, str]] = []
                results.extend(await _extract_results(page, limit, seen_urls))

                while len(results) < limit:
                    has_next = await _go_to_next_page(page, timeout, headless)
                    if not has_next:
                        break
                    new_results = await _extract_results(page, limit - len(results), seen_urls)
                    if not new_results:
                        # Safety net: a page that yields zero new unique URLs means
                        # we've exhausted useful results even if Google still shows
                        # a "Next" link.
                        LOGGER.info("Next page returned no new unique URLs; stopping.")
                        break
                    results.extend(new_results)

                if no_save_state is False:
                    await persist_state(context, state_file_path, saved_state)

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
                    LOGGER.error("An error occurred during search: %s", e)
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

                if no_save_state is False:
                    await persist_state(context, state_file_path, saved_state)

                return result

            except PlaywrightError as e:
                if _is_human_verification_error(e) and headless_mode:
                    LOGGER.warning("Human verification detected, restarting in headed mode.")
                    headless_mode = False
                    # retry on next loop iteration
                else:
                    LOGGER.error("An error occurred while getting HTML: %s", e)
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
