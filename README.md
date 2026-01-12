# Playwright-Powered Google Search and Fetch Tools

A Python-based Google Search tool powered by Playwright, providing both a command-line interface (CLI) and a Model-graded Contextual Processor (MCP) server.

This project is a Python port and enhancement of the original Node.js version from [web-agent-master/google-search](https://github.com/web-agent-master/google-search). It's designed for reliability and flexibility, driving a real browser to deliver accurate, real-time search results.

## Features

- **CLI & MCP Server**: Use it as a standalone CLI or as a tool server for AI agents.
- **Real Browser Integration**: Leverages Playwright to mimic human-like browsing and avoid blocking.
- **Markdown Conversion**: Includes a tool to fetch and convert web page content to clean Markdown.
- **Customizable**: Offers options for headless browsing, session state management, and more.

## Python Environment (uv)

This project uses `uv` for Python environment and dependency management.

- **Prepare the environment** (creates/updates the virtualenv from the lockfile):
  ```bash
  uv sync --frozen
  ```

- **Run all tools and scripts** via `uv`:
  ```bash
  uv run pytest
  uv run python path/to/script.py
  uv run ruff check .
  ```

- **Format code**:
  ```bash
  uv run ruff format --line-length 120
  ```

## Installation

To set up the project and install dependencies, follow these steps:

1.  **Sync the environment**:
    ```bash
    uv sync --frozen
    ```
2.  **Install Playwright browsers**:
    ```bash
    uv run playwright install chromium --with-deps --no-shell
    ```

## CLI Usage

The package provides two CLI tools: `google-search-cli` and `google-search-mcp-cli`.

### `google-search-cli`

#### Basic Search

To perform a simple search, run:

```bash
uv run google-search-cli "your search query"
```

This will output the search results in JSON format.

#### Fetch Markdown

To fetch a URL and convert it to Markdown:

```bash
uv run google-search-cli fetch-markdown "https://example.com"
```

#### Options

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
uv run google-search-cli "claude 3.5 sonnet" --limit 5 --timeout 60000
```

### `google-search-mcp-cli`

This tool allows you to interact with the MCP server from the command line.

```bash
uv run google-search-mcp-cli --help
```

## MCP Server Usage


### Overview

The package also includes an MCP server that exposes the search functionality as a tool for agents.

To start the server, run this `uv` command in the project's root folder:

```bash
uv run google-search-mcp-server
```

or use the code on GitHub via `uvx`:

```bash
uvx --from git+https://github.com/ceshine/python-playwright-google-search.git google-search-mcp-server
```

By default, the server uses the STDIO Transport. The server provides the following tools:

- `search(query: str, limit: int = 10, timeout: int = 60000)`: Performs a Google search.
- `fetch_markdown(url: str, timeout: int = 60000, max_n_chars: int = 250_000)`: Fetches a URL and returns its content as Markdown.

### Prerequisites for uvx

If you're using `uvx` to run the MCP server, you need to run this command once to install the Playwright dependencies:

```bash
uvx run playwright install chromium --with-deps --no-shell
```

Alternatively, you can wrap the installation command in a sh -c call in your MCP configuration file:

```json
{
  "command": [
    "sh",
    "-c",
    "uvx playwright install chromium --no-shell && uvx --from git+https://github.com/ceshine/python-playwright-google-search.git google-search-mcp-server"
  ]
}
```

The downside of this approach is that you can't use the --with-deps flag because it requires root privileges. Additionally, it adds minor overhead to check the Playwright installation at every server startup.

### Examples

[OpenCode configuration](https://opencode.ai):

```json
{
  "mcp": {
    "browser_mcp": {
      "type": "local",
      "command": [
        "uvx",
        "--from",
        "git+https://github.com/ceshine/python-playwright-google-search.git",
        "google-search-mcp-server"
      ]
    }
  },
  "$schema": "https://opencode.ai/config.json"
}
```

## Acknowledgements

- The [AGENTS.md](./AGENTS.md) was adapted from the example sin this blog post: [Getting Good Results from Claude Code](https://www.dzombak.com/blog/2025/08/getting-good-results-from-claude-code/).
