#!/usr/bin/env python3
import json
import asyncio

import typer

from .search import google_search, get_google_search_page_html

app = typer.Typer(help="A Google search CLI tool based on Playwright")


@app.command()
def main(
    query: str = typer.Argument(..., help="Search keyword"),
    limit: int = typer.Option(10, "-l", "--limit", help="Limit the number of results"),
    timeout: int = typer.Option(30000, "-t", "--timeout", help="Timeout in milliseconds"),
    headless: bool = typer.Option(
        True,
        help="Allow user to explicitly enable or disable headless mode. The default is True (using headless mode).",
    ),
    state_file: str = typer.Option("./browser-state.json", "--state-file", help="Path to the browser state file"),
    save_state: bool = typer.Option(True, "-s", "--save-state", help="Save browser state for the current session"),
    get_html: bool = typer.Option(
        False,
        help="Get the raw HTML of the search results page instead of parsed results",
    ),
    save_html: bool = typer.Option(False, help="Save the HTML to a file"),
    html_output: str | None = typer.Option(None, help="HTML output file path"),
):
    """Run a Google search using Playwright and return JSON results (or the page HTML)."""
    options = {
        "timeout": timeout,
        "state_file": state_file,
        "no_save_state": not save_state,
        "locale": "en-US",
        "no_headless": not headless,
    }

    async def run():
        try:
            if get_html:
                html_result = await get_google_search_page_html(
                    query=query,
                    options=options,
                    save_to_file=save_html,
                    output_path=html_output,
                )
                if "error" in html_result:
                    typer.echo(json.dumps(html_result, indent=2))
                else:
                    if save_html and html_result.get("savedPath"):
                        typer.echo(f"HTML has been saved to file: {html_result['savedPath']}")

                    output_result = {
                        "query": html_result.get("query"),
                        "url": html_result.get("url"),
                        "originalHtmlLength": html_result.get("originalHtmlLength"),
                        "cleanedHtmlLength": len(html_result.get("html", "")),
                        "savedPath": html_result.get("savedPath"),
                        "screenshotPath": html_result.get("screenshotPath"),
                        "htmlPreview": html_result.get("html", "")[:500]
                        + ("..." if len(html_result.get("html", "")) > 500 else ""),
                    }
                    typer.echo(json.dumps(output_result, indent=2))
            else:
                # Call google_search with explicit parameters to avoid ambiguity.
                # Pass a literal locale string to avoid type errors from dict lookups.
                results = await google_search(
                    query=query,
                    limit=limit,
                    timeout=timeout,
                    state_file=state_file,
                    no_save_state=not save_state,
                    locale="en-US",
                    headless=headless,
                )
                typer.echo(json.dumps(results, indent=2))
        except Exception as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(code=1)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run())
    loop.close()


if __name__ == "__main__":
    app()
