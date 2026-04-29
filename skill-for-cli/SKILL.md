---
name: web-search-and-fetch
description: Run Google searches and fetch JS-rendered web pages as Markdown via `google-search-cli`, a Patchright-based CLI invoked through `uvx` from its GitHub repo (no local install needed). Use when the agent needs (1) fresh Google search results from a query, (2) the Markdown of a URL that plain HTTP fetch (curl/WebFetch) cannot render because it requires JavaScript or evades bots, or (3) inspection of the raw HTML of a Google results page. Trigger phrases include "google for ...", "search the web for ...", "fetch this page as markdown", "this page needs JS to render". Do not use for static doc URLs that WebFetch handles cleanly, or when Chromium cannot be installed on the machine.
---

# Web Searching and Webpage Fetching

A thin wrapper around the `google-search-cli` tool from `github.com/ceshine/python-playwright-google-search`. Invoke it via `uvx` so the user does not need a local checkout of the repo. The CLI uses **patchright** (a Playwright fork with anti-bot patches) under the hood.

## Prerequisites (one-time Chromium install)

The first time the CLI runs on a machine, Chromium must be downloaded.

**Do not auto-run this** — it downloads ~150 MB and may need sudo for `--with-deps`. Instead, when the CLI errors with a message like `Executable doesn't exist at ...`, tell the user to run:

```bash
uvx --from git+https://github.com/ceshine/python-playwright-google-search.git patchright install chromium
```

On Linux, add `--with-deps` if the system is missing shared libraries (needs sudo). Routing the install through the same `--from` URL ensures the patchright version used to install the browser matches the version that will later run it. Chromium lands in `~/.cache/ms-playwright/`, so subsequent `uvx` invocations reuse it.

## Commands

Invoke from a stable working directory. `./browser-state.json` is read/written in the CWD; reusing it across calls keeps cookies/session alive and lowers rate-limit risk.

### Search

```bash
uvx --from git+https://github.com/ceshine/python-playwright-google-search.git google-search-cli search "<query>" [-l 10]
```

### Fetch a page as Markdown

```bash
uvx --from git+https://github.com/ceshine/python-playwright-google-search.git google-search-cli fetch-markdown "<url>" [--max-n-chars 250000] [-w 0]
```

- `-w` / `--wait`: seconds to wait after the page loads before capturing content. Increase this (e.g. `-w 2` or `-w 5`) if the Markdown looks incomplete or anomalous (lazy-loaded scripts, late-rendered content).

### Inspect the raw Google results HTML

```bash
uvx --from git+https://github.com/ceshine/python-playwright-google-search.git google-search-cli search "<query>" --get-html
```

## Output contract

- `search`: JSON array of `{title, link, snippet}` objects on stdout. With `--get-html`, JSON metadata including `originalHtmlLength`, `cleanedHtmlLength`, and a 500-char `htmlPreview`.
- `fetch-markdown`: plain Markdown text on stdout. When content exceeds `--max-n-chars`, the literal suffix `\n\n... (truncated)` is appended. Detect this string to decide whether to re-invoke with a larger limit.
- Errors: stderr line `Error: ...`, exit code 1.

## Critical default quirks

The two subcommands have **asymmetric** headless defaults. This is intentional; do not flip them without a specific reason.

- `search` defaults to `headless=True` — mimics human browsing for anti-bot evasion. Override with `--no-headless` only when debugging.
- `fetch-markdown` defaults to `headless=False` — some pages render incorrectly headless. Pass `--headless` only in no-display environments (containers/CI without X).

## Browser-state cache

- Both commands read/write `./browser-state.json` by default. Keeping it preserves cookies/session across invocations.
- For isolated calls: `--no-save-state` and `--state-file <unique-path>`.

## Workflow

1. Choose `search` (for a query) or `fetch-markdown` (for a specific URL).
2. Run the command via `Bash` using the `uvx --from git+...` form above, from a stable CWD.
3. Parse output: JSON for `search`, Markdown text for `fetch-markdown`.
4. If `fetch-markdown` output ends with `... (truncated)` and the user needs more, re-invoke **once** with a larger `--max-n-chars`. Do not loop.
5. If `fetch-markdown` output looks anomalous (e.g. empty, missing expected sections, or clearly incomplete), re-invoke **once** with a higher `-w` value (e.g. `-w 10`) to allow late-rendered content to settle. Do not loop.
6. On exit code ≠ 0:
   - If stderr mentions a missing Chromium executable, prompt the user
     with the install command above and stop.
   - Otherwise, report the stderr line and stop.

## Failure policy

Stop and report to the user when:

- Exit code ≠ 0 and the error is not "Chromium missing".
- Chromium is missing — hand the install command to the user; do not
  auto-install.
- Two `search` calls in a row return empty results — likely rate-limited. Back off and surface the issue rather than retrying in a tight loop.

## Examples

- "Google for recent papers on retrieval-augmented generation, show the top 5 results." → `search "recent papers on retrieval-augmented generation" -l 5`
- "Fetch this blog post as Markdown so I can quote from it." → `fetch-markdown "<url>"`
- "The first page came back truncated — grab more of it." → re-run with `--max-n-chars 500000`.
