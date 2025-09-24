# Google Search CLI & MCP Powered by Playwright

A Python port of the Google Search tool from [web-agent-master/google-search](https://github.com/web-agent-master/google-search), originally built for Node.js. It provides:

- A simple command-line interface for running Google searches
- A Model Context Protocol (MCP) server for agent integration

Built on Playwright to drive a real browser for reliable results.

## Installation

To install the package and its dependencies, you can use `pip`:

```bash
pip install .
```

This will install the package and the necessary dependencies, including Playwright, Typer, and FastMCP. After installation, Playwright may need to download browser binaries. You can do this by running:

```bash
uvx playwright install chromium --with-deps --no-shell
```

## CLI Usage

The package provides a command-line interface called `google-search-cli`.

### Basic Search

To perform a simple search, run:

```bash
google-search-cli "your search query"
```

This will output the search results in JSON format.

### Options

The CLI supports several options to customize the search:

- `--limit` or `-l`: Limit the number of search results (default: 10).
- `--timeout`: Set a timeout in milliseconds for the search (default: 30000).
- `--headless`: Run the browser in headless mode (default: True). Use `--no-headless` to run with a visible browser window.
- `--state-file`: Specify a path to a browser state file to reuse cookies and other session data.
- `--save-state`: Save the browser state for the current session (default: True). Use `--no-save-state` to disable.
- `--get-html`: Get the raw HTML of the search results page instead of parsed results.
- `--save-html`: Save the HTML to a file.
- `--html-output`: Specify the output path for the saved HTML file.

Example with options:

```bash
google-search-cli "claude 3.5 sonnet" --limit 5 --timeout 60000
```

## MCP Server Usage

The package also includes an MCP server that exposes the search functionality as a tool for agents.

To start the server, run:

```bash
google-search-server
```

By default, the server will be available at `http://localhost:8000`. You can then interact with it using an MCP client. The server provides a `search` tool that accepts a `query`, `limit`, and `timeout`.

## Acknowledgements

- The [AGENTS.md](./AGENTS.md) was adapted from the example sin this blog post: [Getting Good Results from Claude Code](https://www.dzombak.com/blog/2025/08/getting-good-results-from-claude-code/).