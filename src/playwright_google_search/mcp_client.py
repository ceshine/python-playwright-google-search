"""CLI for testing the MCP methods."""

import json
import asyncio
from collections.abc import Awaitable
from typing import TypeVar

import typer
from fastmcp import Client

from .mcp_server import MCP as MCP_APP

CLIENT = Client(MCP_APP)
APP = typer.Typer(pretty_exceptions_short=True)
T = TypeVar("T")


def run_sync(coro: Awaitable[T]) -> T:
    running_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(running_loop)

    try:
        return running_loop.run_until_complete(coro)
    finally:
        running_loop.close()


@APP.command()
def search(query: str):
    async def _internal_func():
        async with CLIENT:
            result = await CLIENT.call_tool("search", arguments={"query": query})
            if result.structured_content is not None:
                print(json.dumps(json.loads(result.structured_content["result"]), indent=2, ensure_ascii=False))
            else:
                print("Got an empty response")
                raise typer.Exit(1)

    _ = run_sync(_internal_func())


@APP.command()
def fetch(url: str):
    async def _internal_func():
        async with CLIENT:
            result = await CLIENT.call_tool("fetch_markdown", arguments={"url": url, "timeout": 5000})
            if result.structured_content is not None:
                print(result.structured_content["result"])
            else:
                print("Got an empty response")
                raise typer.Exit(1)

    _ = run_sync(_internal_func())


if __name__ == "__main__":
    APP()
