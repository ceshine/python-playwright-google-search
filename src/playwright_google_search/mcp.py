import json
import asyncio

from fastmcp import FastMCP

from .page_content import fetch_page_markdown
from .search import google_search

mcp = FastMCP("Google Search 🚀")


@mcp.tool
def search(query: str, limit: int = 10, timeout: int = 60000) -> str:
    """Uses the Google search engine to query real-time web information,

    returning search results including titles, links, and snippets.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        results = loop.run_until_complete(google_search(query=query, limit=limit, timeout=timeout))
    finally:
        loop.close()

    return json.dumps(results, indent=2)


@mcp.tool
def fetch_markdown(url: str, timeout: int = 60000) -> str:
    """Open the given URL in Chromium and return the page rendered as Markdown."""

    return fetch_page_markdown(url=url, timeout=timeout)


if __name__ == "__main__":
    mcp.run(transport="http")
