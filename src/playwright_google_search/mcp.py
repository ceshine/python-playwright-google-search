import asyncio

from fastmcp import FastMCP

from .search import google_search

mcp = FastMCP("Google Search 🚀")


@mcp.tool
def search(query: str, limit: int = 10, timeout: int = 60000) -> str:
    """
    Uses the Google search engine to query real-time web information,
    returning search results including titles, links, and snippets.
    """
    results = asyncio.run(google_search(query=query, limit=limit, timeout=timeout))
    import json

    return json.dumps(results, indent=2)


if __name__ == "__main__":
    mcp.run(transport="http")
