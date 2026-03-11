import os
import json

from fastmcp import FastMCP

from .search import google_search
from .page_content import fetch_page_markdown_async

MCP = FastMCP("Google Search 🚀")
HEADLESS = os.environ.get("HEADLESS", "false").lower().startswith("t")


@MCP.tool
async def search(query: str, limit: int = 10, timeout: int = 60000, headless: None | bool = None) -> str:
    """Search the web using Google and return structured results.

    Opens Google in a Chromium browser, performs the given search query, and returns
    the results as a JSON string containing the query and a list of results. Each
    result includes a title, link, and snippet.

    Args:
        query: The search query string to search for on Google.
        limit: Maximum number of results to return. Defaults to 10.
        timeout: Maximum time in milliseconds to wait for the search to complete.
            Defaults to 60000 (60 seconds).
        headless: Whether to run the browser in headless mode. If None, uses the
            HEADLESS environment variable or defaults to False. Headless mode may
            trigger Google's bot detection.

    Returns:
        A JSON string containing the search results with the following structure:
        {
            "query": "search terms",
            "results": [
                {"title": "Result Title", "link": "https://...", "snippet": "Description..."},
                ...
            ]
        }
        Returns an "error" field if the search fails.

    Raises:
        RuntimeError: If the browser fails to launch or navigate to Google.
    """
    if headless is None:
        # Use the environment variable or the default if not explicitly specified
        headless = HEADLESS
    results = await google_search(query=query, limit=limit, timeout=timeout, headless=headless)
    return json.dumps(results, indent=2, ensure_ascii=False)


@MCP.tool
async def fetch_markdown(
    url: str,
    timeout: int = 10000,
    max_n_chars: int = 250_000,
    headless: None | bool = None,
) -> str:
    """Open the given URL in Chromium and return the page rendered as Markdown.

    Launches a Chromium browser, navigates to the specified URL, waits for the page
    to fully load, and converts the rendered content to Markdown format.

    Args:
        url: The URL of the web page to fetch and convert to Markdown.
        timeout: Maximum time in milliseconds to wait for the page to load.
            Defaults to 10000 (10 seconds).
        max_n_chars: Maximum number of characters to return. If the Markdown content
            exceeds this limit, it will be truncated and appended with "...
            (truncated)". Set to 0 to disable truncation. Defaults to 250000.
        headless: Whether to run the browser in headless mode. If None, uses the
            HEADLESS environment variable or defaults to False. Headless mode may
            trigger bot detection on some sites.

    Returns:
        The page content rendered as Markdown text. If max_n_chars is exceeded,
        the content is truncated and ends with "... (truncated)".

    Raises:
        RuntimeError: If the browser fails to launch or the page fails to load.
    """
    if headless is None:
        # Use the environment variable or the default if not explicitly specified
        headless = HEADLESS
    markdown_content = await fetch_page_markdown_async(url=url, timeout=timeout, headless=headless)
    if max_n_chars > 0 and len(markdown_content) > max_n_chars:
        markdown_content = markdown_content[:max_n_chars] + "\n\n... (truncated)"
    return markdown_content


if __name__ == "__main__":
    MCP.run()
