import os
import json

from fastmcp import FastMCP

from .search import google_search
from .page_content import fetch_page_markdown_async

MCP = FastMCP("Google Search 🚀")
HEADLESS = os.environ.get("HEADLESS", "false").lower().startswith("t")


@MCP.tool
async def search(query: str, limit: int = 10, timeout: int = 60000) -> str:
    """Uses the Google search engine to query real-time web information,

    returning search results including titles, links, and snippets.
    """
    results = await google_search(query=query, limit=limit, timeout=timeout, headless=HEADLESS)
    return json.dumps(results, indent=2, ensure_ascii=False)


@MCP.tool
async def fetch_markdown(url: str, timeout: int = 10000) -> str:
    """Open the given URL in Chromium and return the page rendered as Markdown."""

    return await fetch_page_markdown_async(url=url, timeout=timeout, headless=HEADLESS)


if __name__ == "__main__":
    MCP.run(transport="http")
